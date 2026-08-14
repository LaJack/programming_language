from dataclasses import dataclass
from pathlib import Path

try:
    from .ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        ImportBinding,
        ImportDeclaration,
        IndexExpression,
        ModuleDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        StructLiteralExpression,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
    from .parser import ParseError, parse
except ImportError:
    from ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        ImportBinding,
        ImportDeclaration,
        IndexExpression,
        ModuleDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        StructLiteralExpression,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
    from parser import ParseError, parse


class ModuleLoadError(Exception):
    pass


def default_module_roots() -> list[Path]:
    package_root = Path(__file__).resolve().parent
    if (package_root / 'std').is_dir():
        return [package_root]
    return []


@dataclass(frozen=True)
class LoadedModule:
    name: str
    path: Path


@dataclass(frozen=True)
class NameRewriteContext:
    local_symbols: dict[str, str]
    imported_symbols: dict[str, str]
    ambiguous_imports: dict[str, set[str]]
    aliases: dict[str, ImportBinding]


class NameRewriteScope:
    def __init__(self, parent: "NameRewriteScope | None" = None) -> None:
        self.parent = parent
        self.values: set[str] = set()

    def declare(self, name: str) -> None:
        self.values.add(name)

    def contains(self, name: str) -> bool:
        if name in self.values:
            return True
        if self.parent is not None:
            return self.parent.contains(name)
        return False


def load_source_file(
    path: Path,
    import_overrides: dict[str, str] | None = None,
    search_roots: list[Path] | None = None,
) -> list[Statement]:
    resolver = ModuleResolver(path.parent, import_overrides or {}, search_roots)
    return resolver.load_entry(path)


class ModuleResolver:
    MODULE_SUFFIXES = ('.jack', '.jk')

    def __init__(
        self,
        entry_root: Path,
        import_overrides: dict[str, str],
        search_roots: list[Path] | None,
    ) -> None:
        roots = [entry_root, *(search_roots or []), *default_module_roots()]
        self.search_roots = []
        for root in roots:
            resolved = root.resolve()
            if resolved not in self.search_roots:
                self.search_roots.append(resolved)
        self.import_overrides = import_overrides
        self.loaded: set[str] = set()
        self.loading: list[str] = []
        self.module_symbols: dict[str, dict[str, str]] = {}
        self.module_public_symbols: dict[str, set[str]] = {}

    def load_entry(self, path: Path) -> list[Statement]:
        return self._load_path(path.resolve(), expected_module=None, is_entry=True)

    def _load_module(self, requested_name: str) -> list[Statement]:
        effective_name = self._effective_module_name(requested_name)
        if effective_name in self.loaded:
            return []
        if effective_name in self.loading:
            cycle = ' -> '.join([*self.loading, effective_name])
            raise ModuleLoadError(f'Module import cycle detected: {cycle}.')
        path = self._resolve_module_path(effective_name)
        return self._load_path(path, expected_module=effective_name, is_entry=False)

    def _effective_module_name(self, requested_name: str) -> str:
        return self.import_overrides.get(requested_name, requested_name)

    def _load_path(
        self, path: Path, expected_module: str | None, is_entry: bool
    ) -> list[Statement]:
        try:
            source = path.read_text()
        except OSError as err:
            raise ModuleLoadError(f'Cannot read module source "{path}".') from err

        try:
            ast = parse(source)
        except ParseError as err:
            raise ModuleLoadError(f'Cannot parse module source "{path}": {err}') from err

        module_name, imports, body = self._split_module_ast(ast, path, expected_module, is_entry)
        if module_name in self.loaded:
            return []
        if module_name in self.loading:
            cycle = ' -> '.join([*self.loading, module_name])
            raise ModuleLoadError(f'Module import cycle detected: {cycle}.')

        self.loading.append(module_name)
        try:
            flattened: list[Statement] = []
            import_bindings: list[ImportBinding] = []
            for declaration in imports:
                effective_name = self._effective_module_name(declaration.module_name)
                flattened.extend(self._load_module(declaration.module_name))
                import_bindings.append(
                    ImportBinding(effective_name, declaration.alias, declaration.symbols)
                )
            local_symbols = self._declare_module_symbols(module_name, body, is_entry)
            self._validate_import_bindings(module_name, import_bindings, body)
            context = self._name_rewrite_context(local_symbols, import_bindings)
            for statement in body:
                self._rewrite_module_names(statement, context, NameRewriteScope(), top_level=True)
                self._mark_module_owner(statement, module_name, import_bindings)
            flattened.extend(body)
            self.loaded.add(module_name)
            return flattened
        finally:
            self.loading.pop()

    def _declare_module_symbols(
        self, module_name: str, body: list[Statement], is_entry: bool
    ) -> dict[str, str]:
        symbols: dict[str, str] = {}
        public_symbols: set[str] = set()
        for statement in body:
            if not self._is_top_level_symbol_declaration(statement):
                continue
            source_name = statement.name
            if source_name in symbols:
                raise ModuleLoadError(
                    f'Module "{module_name}" declares "{source_name}" more than once.'
                )
            internal_name = self._internal_declaration_name(
                module_name, source_name, statement, is_entry
            )
            statement.source_name = source_name
            symbols[source_name] = internal_name
            if statement.public:
                public_symbols.add(source_name)

        self.module_symbols[module_name] = symbols
        self.module_public_symbols[module_name] = public_symbols
        return symbols

    def _validate_import_bindings(
        self, module_name: str, imports: list[ImportBinding], body: list[Statement]
    ) -> None:
        local_names = {
            statement.source_name or statement.name
            for statement in body
            if self._is_top_level_symbol_declaration(statement)
        }
        aliases: dict[str, str] = {}
        exposed: dict[str, str] = {}
        for binding in imports:
            if binding.module_name not in self.module_symbols:
                raise ModuleLoadError(
                    f'Module "{module_name}" imports unloaded module "{binding.module_name}".'
                )
            if binding.alias is not None:
                if binding.alias in aliases:
                    raise ModuleLoadError(f'Import alias "{binding.alias}" is already used.')
                if binding.alias in local_names:
                    raise ModuleLoadError(
                        f'Import alias "{binding.alias}" conflicts with a local declaration.'
                    )
                aliases[binding.alias] = binding.module_name
                continue

            public_symbols = self.module_public_symbols.get(binding.module_name, set())
            exposed_symbols = binding.symbols if binding.symbols is not None else sorted(public_symbols)
            for symbol in exposed_symbols:
                self._validate_imported_public_symbol(binding.module_name, symbol)
                if symbol in local_names:
                    raise ModuleLoadError(
                        f'Imported symbol "{symbol}" from module "{binding.module_name}" conflicts with a local declaration.'
                    )
                previous_module = exposed.get(symbol)
                if previous_module is not None and previous_module != binding.module_name:
                    raise ModuleLoadError(
                        f'Ambiguous imported symbol "{symbol}" from modules "{previous_module}" and "{binding.module_name}"; use an alias or selective imports.'
                    )
                exposed[symbol] = binding.module_name

    def _validate_imported_public_symbol(self, module_name: str, symbol: str) -> None:
        if symbol not in self.module_symbols.get(module_name, {}):
            raise ModuleLoadError(f'Module "{module_name}" has no symbol "{symbol}".')
        if symbol not in self.module_public_symbols.get(module_name, set()):
            raise ModuleLoadError(f'Symbol "{symbol}" from module "{module_name}" is private.')

    def _name_rewrite_context(
        self, local_symbols: dict[str, str], imports: list[ImportBinding]
    ) -> NameRewriteContext:
        imported_symbols: dict[str, str] = {}
        ambiguous_imports: dict[str, set[str]] = {}
        aliases = {binding.alias: binding for binding in imports if binding.alias is not None}

        for binding in imports:
            if binding.alias is not None:
                continue
            module_symbols = self.module_symbols.get(binding.module_name, {})
            symbols = binding.symbols if binding.symbols is not None else sorted(module_symbols)
            for symbol in symbols:
                if symbol in local_symbols:
                    continue
                internal_name = module_symbols.get(symbol)
                if internal_name is None:
                    continue
                existing = imported_symbols.get(symbol)
                if existing is not None and existing != internal_name:
                    ambiguous_imports.setdefault(symbol, set()).add(existing)
                    ambiguous_imports[symbol].add(internal_name)
                    imported_symbols.pop(symbol, None)
                    continue
                if symbol not in ambiguous_imports:
                    imported_symbols[symbol] = internal_name

        return NameRewriteContext(local_symbols, imported_symbols, ambiguous_imports, aliases)

    def _mark_module_owner(
        self, statement: Statement, module_name: str, imports: list[ImportBinding]
    ) -> None:
        statement.module_name = module_name
        statement.imports = list(imports)
        if hasattr(statement, 'fields'):
            for field in statement.fields:
                self._mark_module_owner(field, module_name, imports)
        if hasattr(statement, 'methods'):
            for method in statement.methods:
                self._mark_module_owner(method, module_name, imports)
        if hasattr(statement, 'body') and isinstance(statement.body, list):
            for child in statement.body:
                self._mark_module_owner(child, module_name, imports)
        if hasattr(statement, 'branches'):
            for branch in statement.branches:
                for child in branch.body:
                    self._mark_module_owner(child, module_name, imports)
        if getattr(statement, 'else_body', None) is not None:
            for child in statement.else_body:
                self._mark_module_owner(child, module_name, imports)
        if getattr(statement, 'initializer', None) is not None:
            self._mark_module_owner(statement.initializer, module_name, imports)
        if getattr(statement, 'update', None) is not None:
            self._mark_module_owner(statement.update, module_name, imports)
        if hasattr(statement, 'catches'):
            for catch in statement.catches:
                for child in catch.body:
                    self._mark_module_owner(child, module_name, imports)

    def _rewrite_module_names(
        self,
        statement: Statement,
        context: NameRewriteContext,
        scope: NameRewriteScope,
        top_level: bool = False,
    ) -> set[tuple[str, str]]:
        used_aliases: set[tuple[str, str]] = set()

        if type(statement) is VariableDeclaration:
            self._rewrite_type_names(statement.type, context, used_aliases)
            if statement.expr is not None:
                self._rewrite_expression_names(statement.expr, context, scope, used_aliases)
            for argument in statement.constructor_args:
                self._rewrite_expression_names(argument, context, scope, used_aliases)
            if top_level:
                statement.name = self._top_level_internal_name(statement, context)
            else:
                scope.declare(statement.name)
        elif type(statement) is Assignment:
            if type(statement.name) is str:
                statement.name = self._rewrite_value_name(statement.name, context, scope, used_aliases)
            else:
                self._rewrite_expression_names(statement.name, context, scope, used_aliases)
            self._rewrite_expression_names(statement.expr, context, scope, used_aliases)
        elif type(statement) is FunctionCall:
            statement.function_name = self._rewrite_value_name(
                statement.function_name, context, scope, used_aliases
            )
            for argument in statement.parameters:
                self._rewrite_expression_names(argument, context, scope, used_aliases)
        elif type(statement) is Raise:
            self._rewrite_expression_names(statement.expr, context, scope, used_aliases)
        elif type(statement) is Rethrow:
            pass
        elif type(statement) is Print:
            statement.name = self._rewrite_value_name(statement.name, context, scope, used_aliases)
            if statement.expr is not None:
                self._rewrite_expression_names(statement.expr, context, scope, used_aliases)
        elif type(statement) is Return:
            if statement.expr is not None:
                self._rewrite_expression_names(statement.expr, context, scope, used_aliases)
        elif type(statement) is If:
            for branch in statement.branches:
                self._rewrite_expression_names(branch.condition, context, scope, used_aliases)
                branch_scope = NameRewriteScope(scope)
                used_aliases.update(
                    self._rewrite_statement_list_names(branch.body, context, branch_scope)
                )
            if statement.else_body is not None:
                used_aliases.update(
                    self._rewrite_statement_list_names(
                        statement.else_body, context, NameRewriteScope(scope)
                    )
                )
        elif type(statement) is While:
            self._rewrite_expression_names(statement.condition, context, scope, used_aliases)
            used_aliases.update(
                self._rewrite_statement_list_names(statement.body, context, NameRewriteScope(scope))
            )
        elif type(statement) is For:
            loop_scope = NameRewriteScope(scope)
            if statement.initializer is not None:
                used_aliases.update(
                    self._rewrite_module_names(statement.initializer, context, loop_scope)
                )
            if statement.condition is not None:
                self._rewrite_expression_names(statement.condition, context, loop_scope, used_aliases)
            if statement.update is not None:
                used_aliases.update(
                    self._rewrite_module_names(statement.update, context, loop_scope)
                )
            used_aliases.update(
                self._rewrite_statement_list_names(statement.body, context, NameRewriteScope(loop_scope))
            )
        elif type(statement) is Try:
            used_aliases.update(
                self._rewrite_statement_list_names(statement.body, context, NameRewriteScope(scope))
            )
            for catch in statement.catches:
                self._rewrite_type_names(catch.error_type, context, used_aliases)
                catch_scope = NameRewriteScope(scope)
                if catch.name is not None:
                    catch_scope.declare(catch.name)
                used_aliases.update(
                    self._rewrite_statement_list_names(catch.body, context, catch_scope)
                )
        elif type(statement) is TypeDeclaration:
            for parameter in statement.parameters:
                used_aliases.update(self._rewrite_parameter_names(parameter, context))
            for field in statement.fields:
                self._rewrite_type_names(field.type, context, used_aliases)
                if field.expr is not None:
                    self._rewrite_expression_names(field.expr, context, scope, used_aliases)
            for method in statement.methods:
                used_aliases.update(self._rewrite_function_names(method, context, method_owner=True))
            if top_level:
                statement.name = self._top_level_internal_name(statement, context)
        elif type(statement) is FunctionDeclaration:
            used_aliases.update(self._rewrite_function_names(statement, context))
            if top_level:
                statement.name = self._top_level_internal_name(statement, context)
        elif type(statement) is ViewDeclaration:
            for field in statement.fields:
                self._rewrite_type_names(field.type, context, used_aliases)
            if top_level:
                statement.name = self._top_level_internal_name(statement, context)

        statement.qualified_imports = self._alias_use_bindings(used_aliases)
        return used_aliases

    def _rewrite_function_names(
        self,
        statement: FunctionDeclaration,
        context: NameRewriteContext,
        method_owner: bool = False,
    ) -> set[tuple[str, str]]:
        used_aliases: set[tuple[str, str]] = set()
        self._rewrite_type_names(statement.return_type, context, used_aliases)
        for error in statement.raises:
            self._rewrite_type_names(error, context, used_aliases)
        scope = NameRewriteScope()
        if method_owner:
            scope.declare('self')
        if statement.self_parameter is not None:
            if statement.self_parameter.type.name != 'self':
                self._rewrite_type_names(statement.self_parameter.type, context, used_aliases)
            scope.declare(statement.self_parameter.name)
        for parameter in statement.parameters:
            self._rewrite_type_names(parameter.type, context, used_aliases)
            scope.declare(parameter.name)
        used_aliases.update(self._rewrite_statement_list_names(statement.body, context, scope))
        statement.qualified_imports = self._alias_use_bindings(used_aliases)
        return used_aliases

    def _rewrite_parameter_names(
        self, parameter: VariableDeclaration, context: NameRewriteContext
    ) -> set[tuple[str, str]]:
        used_aliases: set[tuple[str, str]] = set()
        self._rewrite_type_names(parameter.type, context, used_aliases)
        return used_aliases

    def _rewrite_statement_list_names(
        self,
        statements: list[Statement],
        context: NameRewriteContext,
        scope: NameRewriteScope,
    ) -> set[tuple[str, str]]:
        used_aliases: set[tuple[str, str]] = set()
        for statement in statements:
            used_aliases.update(self._rewrite_module_names(statement, context, scope))
        return used_aliases

    def _rewrite_type_names(
        self,
        type_ref: TypeReference,
        context: NameRewriteContext,
        used_aliases: set[tuple[str, str]],
    ) -> None:
        type_ref.name = self._rewrite_symbol_name(type_ref.name, context, None, used_aliases)
        for argument in type_ref.arguments:
            if type(argument) is TypeReference:
                self._rewrite_type_names(argument, context, used_aliases)
            elif isinstance(argument, Expression):
                self._rewrite_expression_names(argument, context, NameRewriteScope(), used_aliases)
        if type_ref.array_size is not None:
            self._rewrite_expression_names(type_ref.array_size, context, NameRewriteScope(), used_aliases)

    def _rewrite_expression_names(
        self,
        expression: Expression,
        context: NameRewriteContext,
        scope: NameRewriteScope,
        used_aliases: set[tuple[str, str]],
    ) -> None:
        if type(expression) is VariableExpression:
            expression.name = self._rewrite_value_name(expression.name, context, scope, used_aliases)
        elif type(expression) is FunctionCall:
            expression.function_name = self._rewrite_value_name(
                expression.function_name, context, scope, used_aliases
            )
            for argument in expression.parameters:
                self._rewrite_expression_names(argument, context, scope, used_aliases)
        elif type(expression) is TypeExpression:
            self._rewrite_type_names(expression.type_ref, context, used_aliases)
        elif type(expression) is StructLiteralExpression:
            self._rewrite_type_names(expression.type_ref, context, used_aliases)
            for field in expression.fields:
                self._rewrite_expression_names(field.expr, context, scope, used_aliases)
        elif type(expression) is CompositeExpression:
            self._rewrite_expression_names(expression.left, context, scope, used_aliases)
            self._rewrite_expression_names(expression.right, context, scope, used_aliases)
        elif type(expression) is BorrowExpression:
            self._rewrite_expression_names(expression.expr, context, scope, used_aliases)
        elif type(expression) is IndexExpression:
            self._rewrite_expression_names(expression.target, context, scope, used_aliases)
            self._rewrite_expression_names(expression.index, context, scope, used_aliases)
        elif type(expression) is SliceExpression:
            self._rewrite_expression_names(expression.target, context, scope, used_aliases)
            if expression.start is not None:
                self._rewrite_expression_names(expression.start, context, scope, used_aliases)
            if expression.end is not None:
                self._rewrite_expression_names(expression.end, context, scope, used_aliases)
        elif type(expression) is FormattedStringExpression:
            for part in expression.parts:
                if isinstance(part, Expression):
                    self._rewrite_expression_names(part, context, scope, used_aliases)

    def _rewrite_value_name(
        self,
        name: str,
        context: NameRewriteContext,
        scope: NameRewriteScope,
        used_aliases: set[tuple[str, str]],
    ) -> str:
        if name == '' or scope.contains(name.split('.', 1)[0]):
            return name
        return self._rewrite_symbol_name(name, context, scope, used_aliases)

    def _rewrite_symbol_name(
        self,
        name: str,
        context: NameRewriteContext,
        scope: NameRewriteScope | None,
        used_aliases: set[tuple[str, str]],
    ) -> str:
        parts = name.split('.')
        if any(part == '' for part in parts):
            return name
        if len(parts) >= 2:
            binding = context.aliases.get(parts[0])
            if binding is not None and (scope is None or not scope.contains(parts[0])):
                internal = self._imported_internal_name(binding, parts[1])
                used_aliases.add((binding.module_name, parts[1]))
                return '.'.join([internal, *parts[2:]])

        root = parts[0]
        if root in context.local_symbols:
            return '.'.join([context.local_symbols[root], *parts[1:]])
        if root in context.ambiguous_imports:
            modules = ', '.join(sorted(context.ambiguous_imports[root]))
            raise ModuleLoadError(
                f'Ambiguous imported symbol "{root}" resolved to {modules}; use an alias or selective import.'
            )
        if root in context.imported_symbols:
            return '.'.join([context.imported_symbols[root], *parts[1:]])
        return name

    def _imported_internal_name(self, binding: ImportBinding, symbol: str) -> str:
        module_symbols = self.module_symbols.get(binding.module_name, {})
        internal = module_symbols.get(symbol)
        if internal is None:
            raise ModuleLoadError(f'Module "{binding.module_name}" has no symbol "{symbol}".')
        return internal

    def _top_level_internal_name(
        self, statement: Statement, context: NameRewriteContext
    ) -> str:
        source_name = getattr(statement, 'source_name', None) or getattr(statement, 'name')
        return context.local_symbols.get(source_name, source_name)

    def _internal_declaration_name(
        self, module_name: str, source_name: str, statement: Statement, is_entry: bool
    ) -> str:
        if is_entry or self._keeps_external_symbol_name(statement):
            return source_name
        return f'{self._module_symbol_prefix(module_name)}${source_name}'

    def _module_symbol_prefix(self, module_name: str) -> str:
        return module_name.replace('.', '$')

    def _keeps_external_symbol_name(self, statement: Statement) -> bool:
        return bool(getattr(statement, 'extern', False) and getattr(statement, 'abi', None) == 'c')

    def _is_top_level_symbol_declaration(self, statement: Statement) -> bool:
        return type(statement) in {
            TypeDeclaration,
            FunctionDeclaration,
            VariableDeclaration,
            ViewDeclaration,
        }

    def _alias_use_bindings(self, used_aliases: set[tuple[str, str]]) -> list[ImportBinding]:
        grouped: dict[str, set[str]] = {}
        for module_name, symbol in used_aliases:
            grouped.setdefault(module_name, set()).add(symbol)
        return [
            ImportBinding(module_name, symbols=sorted(symbols))
            for module_name, symbols in sorted(grouped.items())
        ]

    def _split_module_ast(
        self,
        ast: list[Statement],
        path: Path,
        expected_module: str | None,
        is_entry: bool,
    ) -> tuple[str, list[ImportDeclaration], list[Statement]]:
        module_declarations = [node for node in ast if type(node) is ModuleDeclaration]
        if len(module_declarations) > 1:
            raise ModuleLoadError(f'Module source "{path}" declares more than one module.')

        declared_name = module_declarations[0].name if module_declarations else None
        if expected_module is not None and declared_name is not None and declared_name != expected_module:
            raise ModuleLoadError(
                f'Module source "{path}" declares "{declared_name}", expected "{expected_module}".'
            )
        if expected_module is not None and declared_name is None:
            declared_name = expected_module
        if declared_name is None:
            declared_name = path.stem if is_entry else expected_module
        if declared_name is None:
            raise ModuleLoadError(f'Cannot determine module name for "{path}".')

        imports: list[ImportDeclaration] = []
        body: list[Statement] = []
        seen_import = False
        seen_body = False
        for node in ast:
            if type(node) is ModuleDeclaration:
                if seen_import or seen_body:
                    raise ModuleLoadError(f'Module declaration in "{path}" must appear before imports and code.')
                continue
            if type(node) is ImportDeclaration:
                if seen_body:
                    raise ModuleLoadError(f'Import declarations in "{path}" must appear before code.')
                seen_import = True
                imports.append(node)
                continue
            seen_body = True
            body.append(node)

        return declared_name, imports, body

    def _resolve_module_path(self, module_name: str) -> Path:
        relative = Path(*module_name.split('.'))
        candidates: list[Path] = []
        for root in self.search_roots:
            for suffix in self.MODULE_SUFFIXES:
                candidates.append(root / relative.with_suffix(suffix))

        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()

        formatted = ', '.join(str(candidate) for candidate in candidates)
        raise ModuleLoadError(f'Cannot resolve module "{module_name}". Tried: {formatted}.')
