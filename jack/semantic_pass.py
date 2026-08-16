from dataclasses import dataclass
import copy

try:
    from .borrow_modes import (
        BORROW_MODES,
        borrow_mode_can_read,
        borrow_mode_can_write,
        borrow_mode_compatible,
    )
    from .builtin_types import (
        BUILTIN_TYPE_SPECS,
        builtin_conversion_allowed,
        cast_builtin_value,
        is_bool_type,
        is_builtin_type,
        is_integer_type,
        is_numeric_type,
        is_raw_byte_type,
    )
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
        ImplementationDeclaration,
        InterfaceDeclaration,
        ImportBinding,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        MoveExpression,
        ImportDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        SourceSpan,
        StructLiteralExpression,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        ViewField,
        While,
    )
except ImportError:
    from borrow_modes import (
        BORROW_MODES,
        borrow_mode_can_read,
        borrow_mode_can_write,
        borrow_mode_compatible,
    )
    from builtin_types import (
        BUILTIN_TYPE_SPECS,
        builtin_conversion_allowed,
        cast_builtin_value,
        is_bool_type,
        is_builtin_type,
        is_integer_type,
        is_numeric_type,
        is_raw_byte_type,
    )
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
        ImplementationDeclaration,
        InterfaceDeclaration,
        ImportBinding,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        MoveExpression,
        ImportDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        SourceSpan,
        StructLiteralExpression,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        ViewField,
        While,
    )


class SemanticError(Exception):
    def __init__(self, message: str, span: SourceSpan | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.span = span


@dataclass(frozen=True)
class SymbolInfo:
    kind: str
    type_ref: TypeReference | None = None
    module_name: str | None = None
    public: bool = False
    source_name: str | None = None
    borrow_accesses: tuple['BorrowAccess', ...] = ()
    view_borrow_accesses: tuple['ViewBorrowAccess', ...] = ()
    can_return_borrow: bool = False
    passing_mode: str = 'copy'
    owned_local: bool = False


@dataclass(frozen=True)
class BorrowPath:
    root: str
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class BorrowAccess:
    path: BorrowPath
    mode: str


@dataclass(frozen=True)
class ViewBorrowAccess:
    field_name: str
    accesses: tuple[BorrowAccess, ...]


@dataclass(frozen=True)
class LiveBorrow:
    owner: str
    access: BorrowAccess


class SemanticScope:
    def __init__(self, parent: 'SemanticScope | None' = None) -> None:
        self.parent = parent
        self.symbols: dict[str, SymbolInfo] = {}
        self.active_borrows: list[LiveBorrow] = []

    def declare(self, name: str, info: SymbolInfo) -> None:
        if name in self.symbols:
            raise SemanticError(f'Name "{name}" is already declared in this scope.')
        self.symbols[name] = info

    def get(self, name: str) -> SymbolInfo | None:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent is not None:
            return self.parent.get(name)
        return None

    def add_borrow(self, owner: str, access: BorrowAccess) -> None:
        self.active_borrows.append(LiveBorrow(owner, access))

    def live_borrows(self) -> list[LiveBorrow]:
        borrows = list(self.active_borrows)
        if self.parent is not None:
            borrows.extend(self.parent.live_borrows())
        return borrows


RUNTIME_SCALAR_TYPES = {'str', 'c_char', 'c_void', *BUILTIN_TYPE_SPECS.keys()}


def validate_runtime_ast(ast: list[Statement]) -> list[Statement]:
    SemanticPass().validate(ast)
    return ast


class SemanticPass:
    def __init__(self) -> None:
        self.types: dict[str, TypeDeclaration] = {}
        self.functions: dict[str, FunctionDeclaration] = {}
        self.views: dict[str, ViewDeclaration] = {}
        self.interfaces: dict[str, InterfaceDeclaration] = {}
        self.implementations: dict[tuple[str, str], ImplementationDeclaration] = {}
        self.global_scope = SemanticScope()
        self.current_module_name: str | None = None
        self.current_imports: list[ImportBinding] = []
        self.current_qualified_imports: list[ImportBinding] = []
        self.current_return_type: TypeReference | None = None
        self.current_function_name: str | None = None
        self.current_raises: set[str] | None = None
        self.current_caught_errors: set[str] = set()
        self.current_rethrow_errors: list[str] = []
        self.current_borrow_return_accesses: list[BorrowAccess] | None = None
        self.borrow_return_summaries: dict[int, tuple[BorrowAccess, ...]] = {}
        self.ownership_states: dict[int, str] = {}
        self.function_body_states: dict[int, str] = {}

    def validate(self, ast: list[Statement]) -> None:
        runtime_ast = [node for node in ast if type(node) not in {ModuleDeclaration, ImportDeclaration}]
        self._register_top_level_names(runtime_ast)
        self._validate_interface_declarations(runtime_ast)
        self._validate_implementation_declarations(runtime_ast)
        self._validate_type_declarations(runtime_ast)
        self._validate_view_declarations(runtime_ast)
        self._validate_top_level_statements(runtime_ast)
        self._validate_function_bodies(runtime_ast)

    def _register_top_level_names(self, ast: list[Statement]) -> None:
        for node in ast:
            if getattr(node, 'comptime', False):
                raise SemanticError(
                    f'Unexpected comptime statement "{type(node).__name__}" after the compile-time pass.'
                )
            if type(node) is TypeDeclaration:
                self._validate_abi(node.abi, f'type "{node.name}"')
                if node.parameters:
                    raise SemanticError(f'Generic type "{node.name}" reached semantic validation.')
                if node.name in self.types or node.name in self.views:
                    raise SemanticError(f'Type "{node.name}" is already declared.')
                self.types[node.name] = node
                self.global_scope.declare(
                    node.name,
                    SymbolInfo(
                        'type',
                        TypeReference(node.name),
                        module_name=node.module_name,
                        public=node.public,
                        source_name=node.source_name or node.name,
                    ),
                )
            elif type(node) is ViewDeclaration:
                if node.name in self.types or node.name in self.views:
                    raise SemanticError(f'View "{node.name}" is already declared.')
                self.views[node.name] = node
                self.global_scope.declare(
                    node.name,
                    SymbolInfo(
                        'view',
                        TypeReference(node.name),
                        module_name=node.module_name,
                        public=node.public,
                        source_name=node.source_name or node.name,
                    ),
                )
            elif type(node) is InterfaceDeclaration:
                if node.name in self.interfaces or self.global_scope.get(node.name) is not None:
                    raise SemanticError(f'Interface "{node.name}" is already declared.', node.span)
                self.interfaces[node.name] = node
                self.global_scope.declare(
                    node.name,
                    SymbolInfo(
                        'interface', TypeReference(node.name),
                        module_name=node.module_name, public=node.public,
                        source_name=node.source_name or node.name,
                    ),
                )
            elif type(node) is FunctionDeclaration:
                self._validate_abi(node.abi, f'function "{node.name}"')
                if node.name in self.functions:
                    raise SemanticError(f'Function "{node.name}" is already declared.')
                self.functions[node.name] = node
                self.global_scope.declare(
                    node.name,
                    SymbolInfo(
                        'function',
                        node.return_type,
                        module_name=node.module_name,
                        public=node.public,
                        source_name=node.source_name or node.name,
                    ),
                )

    def _validate_type_declarations(self, ast: list[Statement]) -> None:
        for node in ast:
            if type(node) is not TypeDeclaration:
                continue
            previous_module_name = self.current_module_name
            previous_imports = self.current_imports
            previous_qualified_imports = self.current_qualified_imports
            self.current_module_name = node.module_name
            self.current_imports = list(node.imports)
            self.current_qualified_imports = list(node.qualified_imports)
            try:
                if node.extern:
                    if node.abi != 'c':
                        raise SemanticError(f'Extern type "{node.name}" must declare an ABI, currently only "c" is supported.')
                    if node.fields or node.parameters or node.methods:
                        raise SemanticError(f'Extern type "{node.name}" must be opaque.')
                    continue

                seen_fields: set[str] = set()
                for field in node.fields:
                    if field.name in seen_fields:
                        raise SemanticError(f'Type "{node.name}" has duplicate field "{field.name}".')
                    seen_fields.add(field.name)
                    self._validate_variable_type(field.name, field.type, allow_void=False)
                    if field.type.array_size is not None and type(field.type.array_size) is not LiteralExpression:
                        raise SemanticError(f'Field "{field.name}" array size must be literal for now.')

                seen_methods: set[str] = set()
                for method in node.methods:
                    if method.name in seen_methods:
                        raise SemanticError(f'Type "{node.name}" has duplicate method "{method.name}".')
                    seen_methods.add(method.name)
                    self._validate_function_signature(method, owner=node.name)
            finally:
                self.current_module_name = previous_module_name
                self.current_imports = previous_imports
                self.current_qualified_imports = previous_qualified_imports

    def _validate_view_declarations(self, ast: list[Statement]) -> None:
        for node in ast:
            if type(node) is not ViewDeclaration:
                continue
            previous_module_name = self.current_module_name
            previous_imports = self.current_imports
            previous_qualified_imports = self.current_qualified_imports
            self.current_module_name = node.module_name
            self.current_imports = list(node.imports)
            self.current_qualified_imports = list(node.qualified_imports)
            try:
                seen_fields: set[str] = set()
                for field in node.fields:
                    if field.mode not in BORROW_MODES:
                        raise SemanticError(f'Unknown view field mode "{field.mode}".')
                    if field.name in seen_fields:
                        raise SemanticError(f'View "{node.name}" has duplicate field "{field.name}".')
                    seen_fields.add(field.name)
                    self._validate_view_field_type(node.name, field)
            finally:
                self.current_module_name = previous_module_name
                self.current_imports = previous_imports
                self.current_qualified_imports = previous_qualified_imports

    def _validate_interface_declarations(self, ast: list[Statement]) -> None:
        for declaration in ast:
            if type(declaration) is not InterfaceDeclaration:
                continue
            seen: set[str] = set()
            for method in declaration.methods:
                if method.name in seen:
                    raise SemanticError(
                        f'Interface "{declaration.name}" has duplicate method "{method.name}".',
                        method.span,
                    )
                seen.add(method.name)
                if method.body:
                    raise SemanticError('Interface methods cannot have bodies.', method.span)
                if method.self_parameter is None:
                    raise SemanticError(
                        f'Interface method "{declaration.name}.{method.name}" must declare self.',
                        method.span,
                    )
                self_parameter = method.self_parameter
                if (
                    self_parameter.type.name not in {'Self', 'self'}
                    or self_parameter.type.borrow is None
                ):
                    raise SemanticError(
                        f'Interface method "{declaration.name}.{method.name}" self '
                        'parameter must explicitly borrow Self.',
                        self_parameter.span,
                    )
                for parameter in method.parameters:
                    self._validate_interface_type_reference(parameter.type)
                self._validate_interface_type_reference(method.return_type, allow_void=True)
                for error in method.raises:
                    self._validate_interface_type_reference(error)

    def _validate_interface_type_reference(
        self, type_ref: TypeReference, allow_void: bool = False
    ) -> None:
        if type_ref.name not in {'Self', 'self'}:
            self._validate_type_reference(type_ref, allow_void=allow_void)
        for argument in type_ref.arguments:
            if type(argument) is TypeReference:
                self._validate_interface_type_reference(argument)

    def _validate_implementation_declarations(self, ast: list[Statement]) -> None:
        for implementation in ast:
            if type(implementation) is not ImplementationDeclaration:
                continue
            if implementation.parameters:
                raise SemanticError(
                    f'Generic implementation for "{implementation.type_name}" reached semantic validation.',
                    implementation.span,
                )
            type_decl = self.types.get(implementation.type_name)
            interface = self.interfaces.get(implementation.interface.name)
            if type_decl is None:
                raise SemanticError(
                    f'Unknown implementation type "{implementation.type_name}".', implementation.span
                )
            if interface is None and implementation.interface.name != 'Copyable':
                raise SemanticError(
                    f'Unknown interface "{implementation.interface.name}".', implementation.span
                )
            interface_module = interface.module_name if interface is not None else None
            if implementation.module_name not in {type_decl.module_name, interface_module}:
                raise SemanticError(
                    'An implementation must be declared in the module owning its type or interface.',
                    implementation.span,
                )
            key = (implementation.type_name, implementation.interface.name)
            if key in self.implementations:
                raise SemanticError(
                    f'Duplicate implementation of "{implementation.interface.name}" for "{implementation.type_name}".',
                    implementation.span,
                )
            self.implementations[key] = implementation
            requirements = self._interface_requirements(interface, implementation.interface.name)
            entries: dict[str, FunctionDeclaration | None] = {}
            for use in implementation.uses:
                if use.name in entries:
                    raise SemanticError(f'Duplicate implementation entry "{use.name}".', use.span)
                entries[use.name] = None
            for method in implementation.methods:
                if method.name in entries:
                    raise SemanticError(f'Duplicate implementation entry "{method.name}".', method.span)
                entries[method.name] = method
            unknown = sorted(set(entries) - set(requirements))
            missing = sorted(set(requirements) - set(entries))
            if unknown:
                raise SemanticError(
                    f'Interface "{implementation.interface.name}" has no method "{unknown[0]}".',
                    implementation.span,
                )
            if missing:
                raise SemanticError(
                    f'Implementation of "{implementation.interface.name}" is missing method "{missing[0]}".',
                    implementation.span,
                )
            inherent = {method.name: method for method in type_decl.methods}
            for name, supplied in entries.items():
                candidate = inherent.get(name) if supplied is None else supplied
                if candidate is None:
                    raise SemanticError(f'No visible inherent method "{name}" exists for use.', implementation.span)
                if not self._interface_signature_matches(requirements[name], candidate, type_decl.name):
                    raise SemanticError(
                        f'Method "{name}" does not match interface "{implementation.interface.name}".',
                        candidate.span,
                    )

    def _interface_requirements(
        self, interface: InterfaceDeclaration | None, name: str
    ) -> dict[str, FunctionDeclaration]:
        if interface is not None:
            return {method.name: method for method in interface.methods}
        if name == 'Copyable':
            return {'init': FunctionDeclaration(
                'init', [VariableDeclaration('other', TypeReference('Self', borrow='in'))],
                [], TypeReference('void'),
                self_parameter=VariableDeclaration('self', TypeReference('Self', borrow='out')),
            )}
        return {}

    def _interface_signature_matches(
        self, required: FunctionDeclaration, actual: FunctionDeclaration, self_name: str
    ) -> bool:
        required_parameters = [required.self_parameter, *required.parameters]
        actual_parameters = [actual.self_parameter, *actual.parameters]
        if any(parameter is None for parameter in required_parameters + actual_parameters):
            return False
        if len(required_parameters) != len(actual_parameters):
            return False
        if required.comptime != actual.comptime or required.raises_inferred != actual.raises_inferred:
            return False
        if not self._interface_type_matches(required.return_type, actual.return_type, self_name):
            return False
        if len(required.raises) != len(actual.raises):
            return False
        if any(not self._interface_type_matches(left, right, self_name)
               for left, right in zip(required.raises, actual.raises)):
            return False
        for left, right in zip(required_parameters, actual_parameters):
            assert left is not None and right is not None
            if (left.comptime, left.passing_mode) != (right.comptime, right.passing_mode):
                return False
            if not self._interface_type_matches(left.type, right.type, self_name):
                return False
        return True

    def _interface_type_matches(
        self, required: TypeReference, actual: TypeReference, self_name: str
    ) -> bool:
        left = copy.deepcopy(required)
        right = copy.deepcopy(actual)
        self._substitute_self_type(left, self_name)
        self._substitute_self_type(right, self_name)
        return self._type_name(left) == self._type_name(right)

    def _validate_view_field_type(self, view_name: str, field: ViewField) -> None:
        type_ref = field.type
        if type_ref.arguments:
            raise SemanticError(f'View "{view_name}" field "{field.name}" has unresolved generic type "{type_ref.name}".')
        if type_ref.borrow is not None:
            raise SemanticError(f'View "{view_name}" field "{field.name}" cannot use a nested borrow type.')
        if type_ref.array_size is not None and type_ref.is_slice:
            raise SemanticError(f'Type "{type_ref.name}" cannot be both array and slice.')
        if type_ref.name == 'void':
            raise SemanticError('View fields cannot have type "void".')
        if type_ref.name == 'type':
            raise SemanticError('Comptime type value reached semantic validation.')
        if type_ref.name in self.views:
            raise SemanticError(f'View "{view_name}" field "{field.name}" cannot use view type "{type_ref.name}".')
        if not self._is_known_base_type(type_ref.name):
            raise SemanticError(f'Unknown type "{type_ref.name}".')
        declaration = self.types.get(type_ref.name)
        if declaration is not None:
            self._require_declaration_visible('Type', type_ref.name, declaration)
        if type_ref.array_size is not None:
            if type(type_ref.array_size) is not LiteralExpression:
                raise SemanticError(f'View "{view_name}" field "{field.name}" array size must be literal for now.')
            if not is_integer_type(type_ref.array_size.type):
                raise SemanticError('Array size literals must be integer values.')

    def _validate_top_level_statements(self, ast: list[Statement]) -> None:
        for node in ast:
            if type(node) in {
                TypeDeclaration, FunctionDeclaration, ViewDeclaration,
                InterfaceDeclaration, ImplementationDeclaration,
            }:
                continue
            previous_module_name = self.current_module_name
            previous_imports = self.current_imports
            previous_qualified_imports = self.current_qualified_imports
            self.current_module_name = node.module_name
            self.current_imports = list(node.imports)
            self.current_qualified_imports = list(node.qualified_imports)
            try:
                self._validate_statement(node, self.global_scope, allow_return=False)
            finally:
                self.current_module_name = previous_module_name
                self.current_imports = previous_imports
                self.current_qualified_imports = previous_qualified_imports

    def _validate_function_bodies(self, ast: list[Statement]) -> None:
        for node in ast:
            if type(node) is FunctionDeclaration:
                self._validate_function_signature_with_context(node)
                if not node.extern:
                    self._ensure_function_body_validated(node, self.global_scope)
            elif type(node) is TypeDeclaration:
                for method in node.methods:
                    self._ensure_method_body_validated(node, method)
            elif type(node) is ImplementationDeclaration:
                type_decl = self.types.get(node.type_name)
                if type_decl is not None:
                    for method in node.methods:
                        self._substitute_interface_self(method, type_decl.name)
                        self._ensure_method_body_validated(type_decl, method)

    def _substitute_interface_self(
        self, declaration: FunctionDeclaration, self_name: str
    ) -> None:
        references = [declaration.return_type, *declaration.raises]
        if declaration.self_parameter is not None:
            references.append(declaration.self_parameter.type)
        references.extend(parameter.type for parameter in declaration.parameters)
        for type_ref in references:
            self._substitute_self_type(type_ref, self_name)

    def _substitute_self_type(self, type_ref: TypeReference, self_name: str) -> None:
        if type_ref.name in {'Self', 'self'}:
            type_ref.name = self_name
        for argument in type_ref.arguments:
            if type(argument) is TypeReference:
                self._substitute_self_type(argument, self_name)

    def _ensure_function_body_validated(
        self, declaration: FunctionDeclaration, parent_scope: SemanticScope
    ) -> None:
        key = id(declaration)
        state = self.function_body_states.get(key)
        if state == 'done':
            return
        if state == 'validating':
            return
        self.function_body_states[key] = 'validating'
        try:
            self._validate_function_signature_with_context(declaration)
            self._validate_function_body(declaration, parent_scope)
        except Exception:
            self.function_body_states.pop(key, None)
            raise
        self.function_body_states[key] = 'done'

    def _ensure_method_body_validated(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> None:
        key = id(method)
        state = self.function_body_states.get(key)
        if state == 'done':
            return
        if state == 'validating':
            return
        self.function_body_states[key] = 'validating'
        try:
            self._validate_method_signature_with_context(type_decl, method)
            self._validate_method_body(type_decl, method)
        except Exception:
            self.function_body_states.pop(key, None)
            raise
        self.function_body_states[key] = 'done'

    def _validate_function_signature_with_context(
        self, declaration: FunctionDeclaration
    ) -> None:
        previous_module_name = self.current_module_name
        previous_imports = self.current_imports
        previous_qualified_imports = self.current_qualified_imports
        self.current_module_name = declaration.module_name
        self.current_imports = list(declaration.imports)
        self.current_qualified_imports = list(declaration.qualified_imports)
        try:
            self._validate_function_signature(declaration)
        except SemanticError as err:
            self._attach_error_span(err, declaration)
            raise
        finally:
            self.current_module_name = previous_module_name
            self.current_imports = previous_imports
            self.current_qualified_imports = previous_qualified_imports

    def _validate_method_signature_with_context(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> None:
        previous_module_name = self.current_module_name
        previous_imports = self.current_imports
        previous_qualified_imports = self.current_qualified_imports
        self.current_module_name = type_decl.module_name
        self.current_imports = list(type_decl.imports)
        self.current_qualified_imports = list(method.qualified_imports or type_decl.qualified_imports)
        try:
            self._validate_function_signature(method, owner=type_decl.name)
        except SemanticError as err:
            self._attach_error_span(err, method)
            raise
        finally:
            self.current_module_name = previous_module_name
            self.current_imports = previous_imports
            self.current_qualified_imports = previous_qualified_imports

    def _attach_error_span(self, err: SemanticError, node: object) -> None:
        if err.span is None:
            err.span = getattr(node, 'span', None)

    def _validate_function_signature(
        self, declaration: FunctionDeclaration, owner: str | None = None
    ) -> None:
        context = f'{owner}.{declaration.name}' if owner is not None else declaration.name
        self._validate_abi(declaration.abi, f'function "{context}"')
        if declaration.raises_inferred:
            raise SemanticError(f'Function "{context}" has an inferred raises clause after the compile-time pass.')
        self._validate_raises_clause(declaration, context)
        self._validate_type_reference(declaration.return_type, allow_void=True)
        if (
            not self._is_void_type(declaration.return_type)
            and self._is_opaque_extern_type(declaration.return_type)
            and not self._is_borrow_type(declaration.return_type)
        ):
            raise SemanticError(
                f'Opaque extern type "{declaration.return_type.name}" cannot be returned by value; use an explicit borrow type.'
            )
        if owner is None:
            if declaration.self_parameter is not None:
                raise SemanticError(f'Function "{context}" cannot declare a self parameter.')
        else:
            if declaration.self_parameter is None:
                raise SemanticError(f'Method "{context}" must declare an explicit self parameter.')
            type_decl = self.types.get(owner)
            if type_decl is not None:
                self._validate_method_self_parameter(type_decl, declaration)

        seen_parameters: set[str] = set()
        for parameter in declaration.parameters:
            if owner is not None and parameter.name == 'self':
                raise SemanticError(f'Method "{context}" self parameter must be the first parameter.')
            if parameter.name in seen_parameters:
                raise SemanticError(f'Function "{context}" has duplicate parameter "{parameter.name}".')
            seen_parameters.add(parameter.name)
            self._validate_parameter(parameter, context)
            if declaration.abi == 'c' and parameter.passing_mode == 'move':
                raise SemanticError(
                    f'Extern C function "{context}" cannot declare move parameter "{parameter.name}".'
                )
            if (
                parameter.passing_mode == 'copy'
                and not self._is_borrow_type(parameter.type)
                and not self._is_copyable_type(parameter.type)
            ):
                raise SemanticError(
                    f'Parameter "{parameter.name}" of function "{context}" requires a copyable type, '
                    f'but "{self._type_name(parameter.type)}" is not copyable.'
                )

        if declaration.name in {'init', 'deinit'}:
            if not self._is_void_type(declaration.return_type):
                raise SemanticError(f'Method "{declaration.name}" must return void.')
        if declaration.name == 'deinit' and declaration.parameters:
            raise SemanticError('Method "deinit" cannot have parameters.')
        if declaration.name == 'deinit' and declaration.raises:
            raise SemanticError('Method "deinit" cannot raise errors.')

    def _validate_method_self_parameter(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> None:
        parameter = method.self_parameter
        context = f'{type_decl.name}.{method.name}'
        if parameter is None:
            raise SemanticError(f'Method "{context}" must declare an explicit self parameter.')
        if parameter.comptime:
            raise SemanticError(f'Method "{context}" self parameter cannot be comptime.')
        if parameter.name != 'self':
            raise SemanticError(f'Method "{context}" self parameter must be named "self".')
        if parameter.type.name == 'self':
            parameter.type.name = type_decl.name
        self._validate_parameter(parameter, context)
        if not self._is_borrow_type(parameter.type):
            raise SemanticError(f'Method "{context}" self parameter must be an explicit borrow type.')

        self_type_name = self._type_name(self._element_type(parameter.type))
        if self_type_name == type_decl.name:
            return
        if self_type_name in self.views and self._view_accepts_type(self_type_name, TypeReference(type_decl.name)):
            return
        raise SemanticError(
            f'Method "{context}" self parameter must borrow "{type_decl.name}" or a compatible view, got "{self._type_name(parameter.type)}".'
        )

    def _method_self_parameter(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> VariableDeclaration:
        if method.self_parameter is None:
            raise SemanticError(f'Method "{type_decl.name}.{method.name}" must declare an explicit self parameter.')
        return method.self_parameter

    def _method_self_type(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> TypeReference:
        return copy.deepcopy(self._method_self_parameter(type_decl, method).type)

    def _validate_parameter(self, parameter: VariableDeclaration, function_name: str) -> None:
        if parameter.passing_mode not in {'copy', 'move'}:
            raise SemanticError(
                f'Parameter "{parameter.name}" has invalid passing mode "{parameter.passing_mode}".'
            )
        if parameter.comptime and parameter.passing_mode == 'move':
            raise SemanticError(f'Comptime parameter "{parameter.name}" cannot use move.')
        if parameter.passing_mode == 'move' and self._is_borrow_type(parameter.type):
            raise SemanticError(
                f'Parameter "{parameter.name}" cannot combine move with a borrow type.'
            )
        if parameter.abi == 'c' and parameter.passing_mode == 'move':
            raise SemanticError(f'Extern C parameter "{parameter.name}" cannot use move.')
        self._validate_variable_type(parameter.name, parameter.type, allow_void=False)
        if parameter.type.array_size is not None and type(parameter.type.array_size) is not LiteralExpression:
            raise SemanticError(f'Parameter "{parameter.name}" array size must be literal for now.')
        if self._is_array_type(parameter.type) and not self._is_borrow_type(parameter.type):
            raise SemanticError(
                f'Array parameter "{parameter.name}" in function "{function_name}" must be an explicit borrow or slice.'
            )
    def _validate_raises_clause(
        self, declaration: FunctionDeclaration, context: str
    ) -> None:
        seen: set[str] = set()
        for error in declaration.raises:
            name = self._validate_error_type_reference(error)
            if name in seen:
                raise SemanticError(f'Function "{context}" declares error "{name}" more than once.')
            seen.add(name)

    def _validate_error_type_reference(self, type_ref: TypeReference) -> str:
        if (
            type_ref.arguments
            or type_ref.array_size is not None
            or type_ref.is_slice
            or type_ref.borrow is not None
        ):
            raise SemanticError('Error references cannot have type arguments, arrays, slices, or borrows.')
        self._validate_type_reference(type_ref, allow_void=False)
        self._validate_raiseable_error_type(type_ref, set())
        return type_ref.name

    def _validate_raiseable_error_type(self, type_ref: TypeReference, seen: set[str]) -> None:
        type_name = self._type_name(self._element_type(type_ref))
        if type_ref.borrow is not None or type_ref.is_slice:
            raise SemanticError(f'Error payload type "{self._type_name(type_ref)}" cannot contain borrows or slices yet.')
        if self._is_str_type(type_ref):
            raise SemanticError('Error payload type "str" is not supported yet; use fixed-size scalar fields for now.')
        if type_name in {'c_char', 'c_void', 'void', 'type'}:
            raise SemanticError(f'Error payload type "{type_name}" is not supported.')
        if is_builtin_type(type_name):
            return
        declaration = self.types.get(type_name)
        if declaration is None or declaration.extern or declaration.parameters:
            raise SemanticError(f'Error type "{type_name}" must be a concrete struct type.')
        if type_name in seen:
            return
        seen.add(type_name)
        if any(method.name == 'deinit' for method in declaration.methods):
            raise SemanticError(f'Error type "{type_name}" cannot define deinit.')
        for field in declaration.fields:
            self._validate_raiseable_error_type(field.type, seen)

    def _validate_function_body(self, declaration: FunctionDeclaration, parent_scope: SemanticScope) -> None:
        scope = SemanticScope(parent_scope)
        for parameter in declaration.parameters:
            info = SymbolInfo(
                'variable', parameter.type,
                module_name=declaration.module_name,
                can_return_borrow=self._is_borrow_type(parameter.type),
                passing_mode=parameter.passing_mode,
                owned_local=not self._is_borrow_type(parameter.type),
            )
            scope.declare(parameter.name, info)
            self.ownership_states[id(info)] = 'initialized'

        previous_module_name = self.current_module_name
        previous_imports = self.current_imports
        previous_qualified_imports = self.current_qualified_imports
        previous_return_type = self.current_return_type
        previous_function_name = self.current_function_name
        previous_raises = self.current_raises
        previous_caught_errors = self.current_caught_errors
        previous_rethrow_errors = self.current_rethrow_errors
        previous_borrow_return_accesses = self.current_borrow_return_accesses
        borrow_return_accesses: list[BorrowAccess] | None = (
            [] if self._is_borrow_type(declaration.return_type) else None
        )
        self.current_module_name = declaration.module_name
        self.current_imports = list(declaration.imports)
        self.current_qualified_imports = list(declaration.qualified_imports)
        self.current_return_type = declaration.return_type
        self.current_function_name = declaration.name
        self.current_raises = set(self._type_name(error) for error in declaration.raises)
        self.current_caught_errors = set()
        self.current_rethrow_errors = []
        self.current_borrow_return_accesses = borrow_return_accesses
        try:
            self._validate_statements(declaration.body, scope, allow_return=True)
            if borrow_return_accesses is not None:
                self.borrow_return_summaries[id(declaration)] = self._unique_borrow_accesses(
                    tuple(borrow_return_accesses)
                )
        finally:
            self.current_module_name = previous_module_name
            self.current_imports = previous_imports
            self.current_qualified_imports = previous_qualified_imports
            self.current_return_type = previous_return_type
            self.current_function_name = previous_function_name
            self.current_raises = previous_raises
            self.current_caught_errors = previous_caught_errors
            self.current_rethrow_errors = previous_rethrow_errors
            self.current_borrow_return_accesses = previous_borrow_return_accesses

    def _validate_method_body(self, type_decl: TypeDeclaration, method: FunctionDeclaration) -> None:
        scope = SemanticScope(self.global_scope)
        scope.declare(
            'self',
            SymbolInfo(
                'variable',
                self._method_self_type(type_decl, method),
                module_name=type_decl.module_name,
                can_return_borrow=True,
            ),
        )
        for parameter in method.parameters:
            info = SymbolInfo(
                'variable', parameter.type,
                module_name=type_decl.module_name,
                can_return_borrow=self._is_borrow_type(parameter.type),
                passing_mode=parameter.passing_mode,
                owned_local=not self._is_borrow_type(parameter.type),
            )
            scope.declare(parameter.name, info)
            self.ownership_states[id(info)] = 'initialized'

        previous_module_name = self.current_module_name
        previous_imports = self.current_imports
        previous_qualified_imports = self.current_qualified_imports
        previous_return_type = self.current_return_type
        previous_function_name = self.current_function_name
        previous_raises = self.current_raises
        previous_caught_errors = self.current_caught_errors
        previous_rethrow_errors = self.current_rethrow_errors
        previous_borrow_return_accesses = self.current_borrow_return_accesses
        borrow_return_accesses: list[BorrowAccess] | None = (
            [] if self._is_borrow_type(method.return_type) else None
        )
        self.current_module_name = type_decl.module_name
        self.current_imports = list(type_decl.imports)
        self.current_qualified_imports = list(method.qualified_imports or type_decl.qualified_imports)
        self.current_return_type = method.return_type
        self.current_function_name = f'{type_decl.name}.{method.name}'
        self.current_raises = set(self._type_name(error) for error in method.raises)
        self.current_caught_errors = set()
        self.current_rethrow_errors = []
        self.current_borrow_return_accesses = borrow_return_accesses
        try:
            self._validate_statements(method.body, scope, allow_return=True)
            if borrow_return_accesses is not None:
                self.borrow_return_summaries[id(method)] = self._unique_borrow_accesses(
                    tuple(borrow_return_accesses)
                )
        finally:
            self.current_module_name = previous_module_name
            self.current_imports = previous_imports
            self.current_qualified_imports = previous_qualified_imports
            self.current_return_type = previous_return_type
            self.current_function_name = previous_function_name
            self.current_raises = previous_raises
            self.current_caught_errors = previous_caught_errors
            self.current_rethrow_errors = previous_rethrow_errors
            self.current_borrow_return_accesses = previous_borrow_return_accesses

    def _validate_statements(
        self, statements: list[Statement], scope: SemanticScope, allow_return: bool
    ) -> None:
        for statement in statements:
            self._validate_statement(statement, scope, allow_return)

    def _validate_statement(
        self, statement: Statement, scope: SemanticScope, allow_return: bool
    ) -> None:
        try:
            if getattr(statement, 'comptime', False):
                raise SemanticError(
                    f'Unexpected comptime statement "{type(statement).__name__}" after the compile-time pass.'
                )
            if type(statement) in {ModuleDeclaration, ImportDeclaration}:
                return
            if type(statement) is VariableDeclaration:
                self._validate_variable_declaration(statement, scope)
            elif type(statement) is Assignment:
                self._validate_assignment(statement, scope)
            elif type(statement) is FunctionCall:
                self._expression_type(statement, scope)
            elif type(statement) is Raise:
                self._validate_raise(statement, scope)
            elif type(statement) is Rethrow:
                self._validate_rethrow()
            elif type(statement) is Print:
                self._validate_print(statement, scope)
            elif type(statement) is Return:
                self._validate_return(statement, scope, allow_return)
            elif type(statement) is If:
                self._validate_if(statement, scope, allow_return)
            elif type(statement) is While:
                initial = dict(self.ownership_states)
                self._validate_condition(statement.condition, scope, 'while condition')
                self._validate_statements(statement.body, SemanticScope(scope), allow_return)
                body_outcome = dict(self.ownership_states)
                self._validate_loop_back_edge(initial, body_outcome)
                self.ownership_states = self._join_ownership_states(
                    initial, [initial, body_outcome]
                )
            elif type(statement) is For:
                self._validate_for(statement, scope, allow_return)
            elif type(statement) is Try:
                self._validate_try(statement, scope, allow_return)
            elif type(statement) is TypeDeclaration:
                raise SemanticError('Nested type declarations are not supported.')
            elif type(statement) is ViewDeclaration:
                raise SemanticError('Nested view declarations are not supported.')
            elif type(statement) is FunctionDeclaration:
                raise SemanticError('Nested function declarations are not supported.')
            else:
                raise SemanticError(f'Unknown statement type "{type(statement).__name__}".')
        except SemanticError as err:
            self._attach_error_span(err, statement)
            raise

    def _validate_variable_declaration(
        self, declaration: VariableDeclaration, scope: SemanticScope
    ) -> None:
        self._validate_abi(declaration.abi, f'variable "{declaration.name}"')
        if declaration.extern:
            if scope is not self.global_scope:
                raise SemanticError(f'Extern variable "{declaration.name}" must be declared at top level.')
            if declaration.abi != 'c':
                raise SemanticError(f'Extern variable "{declaration.name}" must declare an ABI, currently only "c" is supported.')
            if declaration.expr is not None or declaration.constructor_args:
                raise SemanticError(f'Extern variable "{declaration.name}" cannot have an initializer or constructor arguments.')
            if declaration.comptime:
                raise SemanticError(f'Extern variable "{declaration.name}" cannot be comptime.')
            self._validate_variable_type(declaration.name, declaration.type, allow_void=False)
            scope.declare(
                declaration.name,
                SymbolInfo(
                    'variable',
                    declaration.type,
                    module_name=declaration.module_name or self.current_module_name,
                    public=declaration.public,
                    source_name=declaration.source_name or declaration.name,
                    can_return_borrow=True,
                ),
            )
            return

        self._validate_variable_type(declaration.name, declaration.type, allow_void=False)
        self._validate_type_runtime_expressions(
            declaration.type, scope, f'array size for "{declaration.name}"'
        )
        if self._is_borrow_type(declaration.type) and declaration.expr is None:
            raise SemanticError(f'Variable "{declaration.name}" of borrow type needs an initializer.')
        if self._is_slice_type(declaration.type) and declaration.expr is None:
            raise SemanticError(f'Variable "{declaration.name}" of slice type needs an initializer.')

        borrow_accesses: tuple[BorrowAccess, ...] = ()
        view_borrow_accesses: tuple[ViewBorrowAccess, ...] = ()
        if declaration.expr is not None:
            expr_type = self._expression_type_for_target(declaration.expr, declaration.type, scope)
            self._expect_assignable(declaration.type, expr_type, declaration.expr, f'initializer for "{declaration.name}"')
            if self._is_borrow_type(declaration.type):
                borrow_accesses = self._borrow_accesses_for_initializer(
                    declaration.expr, declaration.type, scope
                )
                view_borrow_accesses = self._view_borrow_accesses_for_initializer(
                    declaration.expr, declaration.type, scope
                )
                self._check_borrow_conflicts(
                    borrow_accesses,
                    scope,
                    f'borrow variable "{declaration.name}"',
                    ignore_owners=self._borrow_source_owners(declaration.expr, scope),
                )

        info = SymbolInfo(
            'variable', declaration.type,
            module_name=declaration.module_name or self.current_module_name,
            public=declaration.public,
            source_name=declaration.source_name or declaration.name,
            borrow_accesses=borrow_accesses,
            view_borrow_accesses=view_borrow_accesses,
            can_return_borrow=scope is self.global_scope,
            owned_local=scope is not self.global_scope and not self._is_borrow_type(declaration.type),
        )
        scope.declare(declaration.name, info)
        self.ownership_states[id(info)] = 'initialized'
        for access in borrow_accesses:
            scope.add_borrow(declaration.name, access)
        if declaration.constructor_args:
            self._validate_method_call(
                FunctionCall(f'{declaration.name}.init', declaration.constructor_args), scope
            )

    def _validate_assignment(self, assignment: Assignment, scope: SemanticScope) -> None:
        target_type = self._assignment_target_type(assignment.name, scope)
        if not self._is_borrow_type(target_type):
            write_accesses = self._place_accesses(assignment.name, 'out', scope)
            self._check_borrow_conflicts(
                write_accesses,
                scope,
                'assignment target',
                ignore_owners=self._place_source_owners(assignment.name, scope),
            )
        expr_type = self._expression_type_for_target(assignment.expr, target_type, scope)
        self._expect_assignable(target_type, expr_type, assignment.expr, 'assignment')
        if isinstance(assignment.name, str) and '.' not in assignment.name:
            info = scope.get(assignment.name)
            if info is not None and info.owned_local:
                self.ownership_states[id(info)] = 'initialized'

    def _require_initialized(self, name: str, scope: SemanticScope) -> None:
        root = name.split('.')[0]
        info = scope.get(root)
        if info is None or not info.owned_local:
            return
        state = self.ownership_states.get(id(info), 'initialized')
        if state != 'initialized':
            description = 'possibly moved' if state == 'conditional' else state
            raise SemanticError(f'Cannot use "{root}" because it is {description}.')

    def _validate_raise(self, statement: Raise, scope: SemanticScope) -> None:
        error_type = self._expression_value_type(statement.expr, scope)
        error_name = self._validate_error_type_reference(error_type)
        self._require_error_declared(error_name, f'raise {error_name}')

    def _validate_call_raises(
        self, call_name: str, raised_errors: list[TypeReference]
    ) -> None:
        for error in raised_errors:
            self._require_error_declared(self._type_name(error), f'call to "{call_name}"')

    def _validate_rethrow(self) -> None:
        if not self.current_rethrow_errors:
            raise SemanticError('rethrow outside of a catch block.')
        error_name = self.current_rethrow_errors[-1]
        self._require_error_declared(error_name, 'rethrow')

    def _require_error_declared(
        self, error_name: str, context: str
    ) -> None:
        if self.current_raises is not None and error_name in self.current_raises:
            return
        if error_name in self.current_caught_errors:
            return
        if self.current_function_name is None:
            raise SemanticError(
                f'{context} may raise "{error_name}", but top-level code cannot propagate errors.'
            )
        raise SemanticError(
            f'{context} may raise "{error_name}", but function "{self.current_function_name}" does not declare it.'
        )

    def _validate_print(self, statement: Print, scope: SemanticScope) -> None:
        if statement.expr is None:
            self._require_initialized(statement.name, scope)
            value_type = self._resolve_name_type(statement.name, scope)
            if self._expression_reads_place(value_type):
                self._validate_read_access(statement.name, scope, f'print of "{statement.name}"')
            value_type = self._read_value_type(value_type)
        else:
            value_type = self._expression_value_type(statement.expr, scope)
        if self._is_printable_type(value_type):
            return
        raise SemanticError(f'Cannot print values of type "{self._type_name(value_type)}".')

    def _validate_return(self, statement: Return, scope: SemanticScope, allow_return: bool) -> None:
        if not allow_return:
            raise SemanticError('Return statement outside of function.')
        if self.current_return_type is None:
            raise SemanticError('Return statement without an active function.')
        if statement.expr is None:
            if not self._is_void_type(self.current_return_type):
                raise SemanticError(
                    f'Function "{self.current_function_name}" must return a value of type "{self._type_name(self.current_return_type)}".'
                )
            return
        if self._is_void_type(self.current_return_type):
            raise SemanticError(f'Void function "{self.current_function_name}" cannot return a value.')
        if self._is_borrow_type(self.current_return_type):
            expr_type = self._expression_type_for_target(
                statement.expr, self.current_return_type, scope
            )
            self._expect_assignable(
                self.current_return_type, expr_type, statement.expr, 'return value'
            )
            self._validate_borrow_return(statement.expr, self.current_return_type, scope)
            return

        expression = statement.expr
        if type(statement.expr) is VariableExpression and '.' not in statement.expr.name:
            info = scope.get(statement.expr.name)
            if info is not None and info.owned_local:
                expression = MoveExpression(statement.expr)
                expression.span = statement.expr.span
        expr_type = self._expression_type_for_target(
            expression, self.current_return_type, scope
        )
        self._expect_assignable(
            self.current_return_type, expr_type, expression, 'return value'
        )

    def _validate_if(self, statement: If, scope: SemanticScope, allow_return: bool) -> None:
        initial = dict(self.ownership_states)
        outcomes: list[dict[int, str]] = []
        for branch in statement.branches:
            self.ownership_states = dict(initial)
            self._validate_condition(branch.condition, scope, 'if condition')
            self._validate_statements(branch.body, SemanticScope(scope), allow_return)
            outcomes.append(dict(self.ownership_states))
        if statement.else_body is not None:
            self.ownership_states = dict(initial)
            self._validate_statements(statement.else_body, SemanticScope(scope), allow_return)
            outcomes.append(dict(self.ownership_states))
        else:
            outcomes.append(initial)
        self.ownership_states = self._join_ownership_states(initial, outcomes)

    def _join_ownership_states(
        self, initial: dict[int, str], outcomes: list[dict[int, str]]
    ) -> dict[int, str]:
        joined = dict(initial)
        for symbol_id in initial:
            states = {outcome.get(symbol_id, initial[symbol_id]) for outcome in outcomes}
            joined[symbol_id] = next(iter(states)) if len(states) == 1 else 'conditional'
        return joined

    def _validate_loop_back_edge(
        self, initial: dict[int, str], outcome: dict[int, str]
    ) -> None:
        for symbol_id, initial_state in initial.items():
            if initial_state == 'initialized' and outcome.get(symbol_id) != 'initialized':
                raise SemanticError(
                    'A loop-carried value moved in the loop body must be reinitialized '
                    'on every continuing path.'
                )

    def _validate_for(self, statement: For, scope: SemanticScope, allow_return: bool) -> None:
        loop_scope = SemanticScope(scope)
        if statement.initializer is not None:
            self._validate_statement(statement.initializer, loop_scope, allow_return=False)
        initial = dict(self.ownership_states)
        if statement.condition is not None:
            self._validate_condition(statement.condition, loop_scope, 'for condition')
        self._validate_statements(statement.body, SemanticScope(loop_scope), allow_return)
        if statement.update is not None:
            self._validate_statement(statement.update, loop_scope, allow_return=False)
        body_outcome = dict(self.ownership_states)
        self._validate_loop_back_edge(initial, body_outcome)
        self.ownership_states = self._join_ownership_states(
            initial, [initial, body_outcome]
        )

    def _validate_try(self, statement: Try, scope: SemanticScope, allow_return: bool) -> None:
        catch_error_names = [self._validate_catch_clause(catch) for catch in statement.catches]
        initial = dict(self.ownership_states)
        outcomes: list[dict[int, str]] = []

        previous_caught_errors = set(self.current_caught_errors)
        try:
            self.ownership_states = dict(initial)
            self.current_caught_errors = previous_caught_errors | set(catch_error_names)
            self._validate_statements(statement.body, SemanticScope(scope), allow_return)
            outcomes.append(dict(self.ownership_states))
        finally:
            self.current_caught_errors = previous_caught_errors

        for catch, error_name in zip(statement.catches, catch_error_names):
            self.ownership_states = dict(initial)
            catch_scope = SemanticScope(scope)
            if catch.name is not None:
                info = SymbolInfo(
                    'variable', catch.error_type,
                    module_name=self.current_module_name,
                    owned_local=True,
                )
                catch_scope.declare(catch.name, info)
                self.ownership_states[id(info)] = 'initialized'
            self.current_rethrow_errors.append(error_name)
            try:
                self._validate_statements(catch.body, catch_scope, allow_return)
                outcomes.append(dict(self.ownership_states))
            finally:
                self.current_rethrow_errors.pop()
        self.ownership_states = self._join_ownership_states(initial, outcomes)

    def _validate_catch_clause(self, catch: CatchClause) -> str:
        return self._validate_error_type_reference(catch.error_type)

    def _validate_condition(self, expression: Expression, scope: SemanticScope, context: str) -> None:
        condition_type = self._expression_value_type(expression, scope)
        if not self._is_bool_type(condition_type):
            raise SemanticError(f'Expected bool for {context}, got "{self._type_name(condition_type)}".')

    def _expression_type_for_target(
        self, expression: Expression, target_type: TypeReference, scope: SemanticScope
    ) -> TypeReference:
        expr_type = self._expression_type(expression, scope)
        if self._is_borrow_type(target_type):
            return expr_type
        return self._read_value_type(expr_type)

    def _expression_value_type(
        self, expression: Expression, scope: SemanticScope, check_reads: bool = True
    ) -> TypeReference:
        return self._read_value_type(
            self._expression_type(expression, scope, check_reads=check_reads)
        )

    def _read_value_type(self, type_ref: TypeReference) -> TypeReference:
        if (
            self._is_borrow_type(type_ref)
            and not self._is_array_type(type_ref)
            and not self._is_slice_type(type_ref)
            and not self._is_view_borrow_type(type_ref)
        ):
            if not borrow_mode_can_read(type_ref.borrow):
                raise SemanticError('Cannot read through a write-only borrow.')
            return self._element_type(type_ref)
        return type_ref

    def _expression_type(
        self, expression: Expression, scope: SemanticScope, check_reads: bool = True
    ) -> TypeReference:
        if type(expression) is LiteralExpression:
            return TypeReference(expression.type)
        if type(expression) is VariableExpression:
            if check_reads:
                self._require_initialized(expression.name, scope)
            value_type = self._resolve_name_type(expression.name, scope)
            if check_reads and self._expression_reads_place(value_type):
                self._validate_read_access(expression, scope, f'read of "{expression.name}"')
            return value_type
        if type(expression) is MoveExpression:
            if type(expression.expr) is not VariableExpression or '.' in expression.expr.name:
                raise SemanticError('move requires a whole local variable or owned parameter.')
            info = scope.get(expression.expr.name)
            if info is None or not info.owned_local or self._is_borrow_type(info.type_ref):
                raise SemanticError(
                    f'Cannot move "{expression.expr.name}"; only owned locals and parameters can be moved.'
                )
            self._require_initialized(expression.expr.name, scope)
            if any(
                borrow.access.path.root == expression.expr.name
                for borrow in scope.live_borrows()
            ):
                raise SemanticError(
                    f'Cannot move "{expression.expr.name}" while it is borrowed.'
                )
            self.ownership_states[id(info)] = 'moved'
            return info.type_ref
        if type(expression) is FunctionCall:
            return self._function_call_type(expression, scope)
        if type(expression) is TypeExpression:
            raise SemanticError('Type expression reached semantic validation; sizeof and alignof must be folded by the compile-time pass.')
        if type(expression) is FormattedStringExpression:
            for part in expression.parts:
                if type(part) is not str:
                    part_type = self._expression_value_type(part, scope)
                    if not self._is_printable_type(part_type):
                        raise SemanticError(
                            f'Cannot format values of type "{self._type_name(part_type)}".'
                        )
            return TypeReference('str')
        if type(expression) is StructLiteralExpression:
            return self._struct_literal_type(expression, scope)
        if type(expression) is CompositeExpression:
            return self._composite_type(expression, scope)
        if type(expression) is IndexExpression:
            index_type = self._expression_value_type(expression.index, scope)
            if not self._is_integer_type(index_type):
                raise SemanticError(f'Index must be an integer, got "{self._type_name(index_type)}".')
            target_type = self._expression_type(expression.target, scope, check_reads=False)
            if (self._is_slice_type(target_type) or self._is_borrow_type(target_type)) and not borrow_mode_can_read(target_type.borrow):
                raise SemanticError('Cannot read through a write-only borrow or slice.')
            if check_reads:
                self._validate_read_access(expression, scope, 'indexed read')
            return self._indexed_element_type(target_type)
        if type(expression) is SliceExpression:
            return self._slice_type(expression, scope)
        if type(expression) is BorrowExpression:
            inner_type = self._borrow_target_type(expression.expr, scope)
            if expression.mode not in BORROW_MODES:
                raise SemanticError(f'Unknown borrow mode "{expression.mode}".')
            if borrow_mode_can_write(expression.mode) and self._contains_non_writable_borrow(expression.expr, scope):
                raise SemanticError('Cannot create a writable borrow from a read-only borrow.')
            if borrow_mode_can_read(expression.mode) and self._contains_non_readable_borrow(expression.expr, scope):
                raise SemanticError('Cannot create a readable borrow from a write-only borrow.')
            return TypeReference(
                inner_type.name,
                copy.deepcopy(inner_type.arguments),
                array_size=copy.deepcopy(inner_type.array_size),
                is_slice=inner_type.is_slice,
                borrow=expression.mode,
            )
        raise SemanticError(f'Unknown expression type "{type(expression).__name__}".')

    def _struct_literal_type(
        self, expression: StructLiteralExpression, scope: SemanticScope
    ) -> TypeReference:
        self._validate_type_reference(expression.type_ref, allow_void=False)
        type_name = self._type_name(expression.type_ref)
        declaration = self.types.get(type_name)
        if declaration is None or declaration.extern or declaration.parameters:
            raise SemanticError(f'Struct literal type "{type_name}" must be a concrete struct type.')

        declared_fields = {field.name: field for field in declaration.fields}
        seen: set[str] = set()
        for field in expression.fields:
            if field.name in seen:
                raise SemanticError(f'Struct literal for "{type_name}" has duplicate field "{field.name}".')
            declared = declared_fields.get(field.name)
            if declared is None:
                raise SemanticError(f'Type "{type_name}" has no field "{field.name}".')
            seen.add(field.name)
            actual_type = self._expression_type_for_target(field.expr, declared.type, scope)
            self._expect_assignable(
                declared.type,
                actual_type,
                field.expr,
                f'field "{field.name}" of struct literal "{type_name}"',
            )

        missing = [name for name in declared_fields if name not in seen]
        if missing:
            raise SemanticError(f'Struct literal for "{type_name}" is missing field "{missing[0]}".')
        return copy.deepcopy(expression.type_ref)

    def _composite_type(self, expression: CompositeExpression, scope: SemanticScope) -> TypeReference:
        left_type = self._expression_value_type(expression.left, scope)
        right_type = self._expression_value_type(expression.right, scope)
        left_name = self._type_name(left_type)
        right_name = self._type_name(right_type)

        if expression.operator == '+':
            if left_name != right_name:
                raise SemanticError(f'Cannot combine values of type "{left_name}" and "{right_name}".')
            if not is_numeric_type(left_name) or is_raw_byte_type(left_name):
                raise SemanticError(f'Operator "+" is not implemented for type "{left_name}".')
            return left_type

        if expression.operator in {'==', '!=', '<', '>', '<=', '>='}:
            if left_name != right_name:
                raise SemanticError(f'Cannot compare values of type "{left_name}" and "{right_name}".')
            if self._is_str_type(left_type) and expression.operator not in {'==', '!='}:
                raise SemanticError(f'Operator "{expression.operator}" is not implemented for strings.')
            if is_bool_type(left_name) and expression.operator not in {'==', '!='}:
                raise SemanticError(f'Operator "{expression.operator}" is not implemented for bool values.')
            if is_raw_byte_type(left_name) and expression.operator not in {'==', '!='}:
                raise SemanticError(f'Operator "{expression.operator}" is not implemented for raw byte types.')
            if not (
                is_numeric_type(left_name)
                or is_bool_type(left_name)
                or is_raw_byte_type(left_name)
                or self._is_str_type(left_type)
            ):
                raise SemanticError(f'Operator "{expression.operator}" is not implemented for type "{left_name}".')
            return TypeReference('bool')

        raise SemanticError(f'Unknown operator "{expression.operator}".')

    def _function_call_type(self, call: FunctionCall, scope: SemanticScope) -> TypeReference:
        if call.function_name in {'sizeof', 'alignof'}:
            raise SemanticError(f'{call.function_name} must be folded by the compile-time pass.')
        if '.' in call.function_name:
            return self._validate_method_call(call, scope)
        if call.function_name == 'len':
            return self._validate_len_call(call, scope)
        if is_builtin_type(call.function_name):
            return self._validate_builtin_conversion(call, scope)

        declaration = self.functions.get(call.function_name)
        if declaration is None:
            raise SemanticError(f'Unknown function "{call.function_name}".')
        self._require_declaration_visible('Function', call.function_name, declaration)
        self._validate_call_arguments(call.function_name, declaration.parameters, call.parameters, scope)
        self._validate_call_raises(call.function_name, declaration.raises)
        return declaration.return_type

    def _validate_len_call(self, call: FunctionCall, scope: SemanticScope) -> TypeReference:
        if len(call.parameters) != 1:
            raise SemanticError(f'len expects 1 argument, got {len(call.parameters)}.')
        value_type = self._expression_type(call.parameters[0], scope, check_reads=False)
        if not (self._is_array_type(value_type) or self._is_slice_type(value_type)):
            raise SemanticError(f'len expects an array or slice, got "{self._type_name(value_type)}".')
        return TypeReference('i32')

    def _validate_builtin_conversion(self, call: FunctionCall, scope: SemanticScope) -> TypeReference:
        if len(call.parameters) != 1:
            raise SemanticError(
                f'Type conversion "{call.function_name}" expects 1 argument, got {len(call.parameters)}.'
            )
        target_type = call.function_name
        source_ref = self._expression_value_type(call.parameters[0], scope)
        source_type = self._type_name(source_ref)
        if not is_builtin_type(source_type):
            raise SemanticError(f'Cannot convert values of type "{source_type}" to "{target_type}".')
        if not builtin_conversion_allowed(source_type, target_type):
            raise SemanticError(f'Cannot convert {source_type} to {target_type}.')
        if type(call.parameters[0]) is LiteralExpression:
            try:
                cast_builtin_value(
                    call.parameters[0].value,
                    target_type,
                    source_type=call.parameters[0].type,
                    memory_raw=True,
                )
            except (TypeError, ValueError, OverflowError) as err:
                raise SemanticError(
                    f'Cannot convert {call.parameters[0].value!r} to type "{target_type}".'
                ) from err
        return TypeReference(target_type)

    def _validate_method_call(self, call: FunctionCall, scope: SemanticScope) -> TypeReference:
        receiver_name, method_name = call.function_name.rsplit('.', 1)
        receiver_type = self._resolve_name_type(receiver_name, scope)
        type_decl = self._type_declaration_for(receiver_type)
        method = self._visible_method_for_call(type_decl, method_name, call.interface_name)
        if method is None:
            raise SemanticError(f'Type "{type_decl.name}" has no method "{method_name}".')
        self_parameter = self._method_self_parameter(type_decl, method)
        self_argument = VariableExpression(receiver_name)
        self_argument.span = call.span
        self_borrows = self._validate_call_argument(
            f'{type_decl.name}.{method_name}', self_parameter, self_argument, scope
        )
        self._validate_call_arguments(
            f'{type_decl.name}.{method_name}',
            method.parameters,
            call.parameters,
            scope,
            temporary_borrows=[('self', access) for access in self_borrows],
        )
        self._validate_call_raises(f'{type_decl.name}.{method_name}', method.raises)
        return method.return_type

    def _visible_method_for_call(
        self,
        type_decl: TypeDeclaration,
        method_name: str,
        interface_name: str | None,
    ) -> FunctionDeclaration | None:
        candidates = [
            method for method in type_decl.methods
            if method.name == method_name
            or (
                interface_name == method.interface_name
                and method.source_name == method_name
            )
        ]
        if '$' in method_name:
            return candidates[0] if candidates else None
        if interface_name is None:
            return next(
                (method for method in candidates if method.interface_name is None),
                None,
            )
        return next(
            (
                method for method in candidates
                if method.interface_name == interface_name
            ),
            next(
                (method for method in candidates if method.interface_name is None),
                None,
            ),
        )

    def _validate_call_arguments(
        self,
        function_name: str,
        parameters: list[VariableDeclaration],
        arguments: list[Expression],
        scope: SemanticScope,
        temporary_borrows: list[tuple[str, BorrowAccess]] | None = None,
    ) -> None:
        if len(arguments) != len(parameters):
            raise SemanticError(
                f'Function "{function_name}" expects {len(parameters)} argument(s), got {len(arguments)}.'
            )
        temporary_borrows = list(temporary_borrows or [])
        for parameter, argument in zip(parameters, arguments):
            argument_borrows = self._validate_call_argument(function_name, parameter, argument, scope)
            self._check_temporary_borrow_conflicts(
                argument_borrows, temporary_borrows, f'call to function "{function_name}"'
            )
            temporary_borrows.extend((parameter.name, access) for access in argument_borrows)

    def _validate_call_argument(
        self,
        function_name: str,
        parameter: VariableDeclaration,
        argument: Expression,
        scope: SemanticScope,
    ) -> tuple[BorrowAccess, ...]:
        if type(argument) in {BorrowExpression, MoveExpression}:
            raise SemanticError(
                f'Call arguments do not accept explicit borrow or move markers; '
                f'parameter "{parameter.name}" determines its passing behavior.'
            )
        if self._is_borrow_type(parameter.type):
            synthetic = BorrowExpression(parameter.type.borrow or 'in', argument)
            synthetic.span = argument.span
            actual_type = self._expression_type(synthetic, scope)
            if not self._borrow_compatible(parameter.type, actual_type):
                raise SemanticError(
                    f'Cannot pass value of type "{self._type_name(actual_type)}" to parameter "{parameter.name}" of type "{self._type_name(parameter.type)}".'
                )
            borrow_accesses = self._borrow_accesses_for_expression(
                argument, parameter.type.borrow or 'in', scope,
                expected_type=parameter.type,
            )
            mode = parameter.type.borrow or 'in'
            if borrow_mode_can_read(mode):
                for access in borrow_accesses:
                    self._require_initialized(access.path.root, scope)
            self._check_borrow_conflicts(
                borrow_accesses,
                scope,
                f'parameter "{parameter.name}" of function "{function_name}"',
                ignore_owners=self._place_source_owners(argument, scope),
            )
            if mode == 'out' and type(argument) is VariableExpression and '.' not in argument.name:
                info = scope.get(argument.name)
                if info is not None and info.owned_local:
                    self.ownership_states[id(info)] = 'initialized'
            return borrow_accesses

        expression = argument
        if parameter.passing_mode == 'move' and type(argument) is VariableExpression:
            expression = MoveExpression(argument)
            expression.span = argument.span
        elif parameter.passing_mode == 'move' and self._raw_place_paths(argument):
            raise SemanticError(
                f'Parameter "{parameter.name}" requires ownership of a whole local, '
                'owned parameter, or temporary value.'
            )
        actual_type = self._expression_type_for_target(expression, parameter.type, scope)
        self._expect_assignable(parameter.type, actual_type, expression, f'parameter "{parameter.name}"')
        if parameter.passing_mode == 'copy' and not self._is_copyable_type(parameter.type):
            raise SemanticError(
                f'Parameter "{parameter.name}" of function "{function_name}" requires a copyable type.'
            )
        return ()

    def _validate_borrow_return(
        self, expression: Expression, return_type: TypeReference, scope: SemanticScope
    ) -> None:
        accesses = self._borrow_return_accesses(expression, return_type, scope)
        if not accesses:
            raise SemanticError(
                f'Cannot return borrow from function "{self.current_function_name}" because its origin is unknown.'
            )
        for access in accesses:
            info = scope.get(access.path.root)
            if info is None or not info.can_return_borrow:
                raise SemanticError(
                    f'Cannot return borrow of local value "{access.path.root}" from function "{self.current_function_name}".'
                )
        if self.current_borrow_return_accesses is not None:
            self.current_borrow_return_accesses.extend(accesses)

    def _borrow_return_accesses(
        self, expression: Expression, return_type: TypeReference, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        if type(expression) is BorrowExpression:
            return self._borrow_accesses_for_expression(
                expression.expr, expression.mode, scope, expected_type=return_type
            )
        if self._raw_place_paths(expression):
            return self._borrow_accesses_for_existing_borrow(
                expression, return_type.borrow or 'in', scope
            )
        if type(expression) is FunctionCall:
            return self._borrow_accesses_for_call_result(expression, return_type, scope)
        return ()

    def _expression_reads_place(self, type_ref: TypeReference) -> bool:
        return not (
            self._is_borrow_type(type_ref)
            or self._is_array_type(type_ref)
            or self._is_slice_type(type_ref)
        )

    def _validate_read_access(
        self, target: str | Expression, scope: SemanticScope, context: str
    ) -> None:
        if not self._raw_place_paths(target):
            return
        read_accesses = self._place_accesses(target, 'in', scope)
        self._check_borrow_conflicts(
            read_accesses,
            scope,
            context,
            ignore_owners=self._place_source_owners(target, scope),
        )

    def _borrow_accesses_for_existing_borrow(
        self, expression: Expression, mode: str, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        if not self._raw_place_paths(expression):
            return ()
        return self._place_accesses(expression, mode, scope)

    def _borrow_accesses_for_initializer(
        self, expression: Expression, expected_type: TypeReference, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        if type(expression) is BorrowExpression:
            return self._borrow_accesses_for_expression(
                expression.expr, expression.mode, scope, expected_type=expected_type
            )
        if type(expression) is VariableExpression and '.' not in expression.name:
            info = scope.get(expression.name)
            if info is not None and info.borrow_accesses:
                return self._place_accesses(expression, expected_type.borrow or 'in', scope)
        if type(expression) is FunctionCall:
            return self._borrow_accesses_for_call_result(expression, expected_type, scope)
        return ()

    def _view_borrow_accesses_for_initializer(
        self, expression: Expression, expected_type: TypeReference, scope: SemanticScope
    ) -> tuple[ViewBorrowAccess, ...]:
        if not self._is_view_borrow_type(expected_type):
            return ()
        if type(expression) is BorrowExpression:
            return self._view_borrow_field_accesses(
                expression.expr, expression.mode, scope, expected_type
            )
        if type(expression) is VariableExpression and '.' not in expression.name:
            info = scope.get(expression.name)
            if info is not None and info.view_borrow_accesses:
                return info.view_borrow_accesses
        return ()

    def _view_borrow_field_accesses(
        self, expression: Expression, mode: str, scope: SemanticScope, expected_type: TypeReference
    ) -> tuple[ViewBorrowAccess, ...]:
        view_name = self._type_name(self._element_type(expected_type))
        if view_name not in self.views:
            return ()
        if mode != 'inout':
            raise SemanticError(
                f'View borrow type "{self._type_name(expected_type)}" must be created with &inout; view fields carry their own access modes.'
            )
        target_type = self._borrow_target_type(expression, scope)
        if not self._view_accepts_type(view_name, target_type):
            raise SemanticError(
                f'Cannot borrow value of type "{self._type_name(target_type)}" as view "{view_name}".'
            )
        paths = self._raw_place_paths(expression)
        if not paths:
            raise SemanticError('Cannot borrow a temporary value.')

        fields: list[ViewBorrowAccess] = []
        view = self.views[view_name]
        for field in view.fields:
            accesses: list[BorrowAccess] = []
            for path in paths:
                field_path = BorrowPath(path.root, (*path.fields, field.name))
                accesses.extend(self._normalize_borrow_path(field_path, field.mode, scope))
            fields.append(ViewBorrowAccess(field.name, tuple(accesses)))
        return tuple(fields)

    def _borrow_accesses_for_call_result(
        self, call: FunctionCall, expected_type: TypeReference, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        target = self._borrow_return_call_target(call, scope)
        if target is None:
            return ()
        declaration, parameters, receiver_name, type_decl = target
        if not self._is_borrow_type(declaration.return_type):
            return ()
        if not declaration.extern:
            if type_decl is None:
                self._ensure_function_body_validated(declaration, self.global_scope)
            else:
                self._ensure_method_body_validated(type_decl, declaration)

        summary = self.borrow_return_summaries.get(id(declaration), ())
        if not summary:
            return ()

        arguments_by_parameter = {
            parameter.name: (parameter, argument)
            for parameter, argument in zip(parameters, call.parameters)
        }
        accesses: list[BorrowAccess] = []
        for access in summary:
            if receiver_name is not None and access.path.root == 'self':
                accesses.extend(
                    self._map_return_access_to_place(receiver_name, access, scope)
                )
                continue

            parameter_argument = arguments_by_parameter.get(access.path.root)
            if parameter_argument is not None:
                parameter, argument = parameter_argument
                accesses.extend(
                    self._map_return_access_to_argument(
                        parameter, argument, access, scope
                    )
                )
                continue

            accesses.extend(self._normalize_borrow_path(access.path, access.mode, scope))

        return self._unique_borrow_accesses(tuple(accesses))

    def _borrow_return_call_target(
        self, call: FunctionCall, scope: SemanticScope
    ) -> tuple[FunctionDeclaration, list[VariableDeclaration], str | None, TypeDeclaration | None] | None:
        if call.function_name in {'sizeof', 'alignof', 'len'} or is_builtin_type(call.function_name):
            return None
        if '.' in call.function_name:
            receiver_name, method_name = call.function_name.rsplit('.', 1)
            receiver_type = self._resolve_name_type(receiver_name, scope)
            type_decl = self._type_declaration_for(receiver_type)
            method = self._visible_method_for_call(
                type_decl, method_name, call.interface_name
            )
            if method is None:
                raise SemanticError(f'Type "{type_decl.name}" has no method "{method_name}".')
            return method, method.parameters, receiver_name, type_decl

        declaration = self.functions.get(call.function_name)
        if declaration is None:
            raise SemanticError(f'Unknown function "{call.function_name}".')
        return declaration, declaration.parameters, None, None

    def _map_return_access_to_place(
        self, place_name: str, access: BorrowAccess, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        base_accesses = self._place_accesses(VariableExpression(place_name), access.mode, scope)
        return self._append_return_access_fields(base_accesses, access.path.fields)

    def _map_return_access_to_argument(
        self,
        parameter: VariableDeclaration,
        argument: Expression,
        access: BorrowAccess,
        scope: SemanticScope,
    ) -> tuple[BorrowAccess, ...]:
        if not self._is_borrow_type(parameter.type):
            return ()
        if type(argument) is BorrowExpression:
            expected_type = None if access.path.fields else parameter.type
            base_accesses = self._borrow_accesses_for_expression(
                argument.expr, access.mode, scope, expected_type=expected_type
            )
        else:
            base_accesses = self._borrow_accesses_for_existing_borrow(
                argument, access.mode, scope
            )
        return self._append_return_access_fields(base_accesses, access.path.fields)

    def _append_return_access_fields(
        self, accesses: tuple[BorrowAccess, ...], fields: tuple[str, ...]
    ) -> tuple[BorrowAccess, ...]:
        if not fields:
            return accesses
        return tuple(
            BorrowAccess(self._append_path(access.path, fields), access.mode)
            for access in accesses
        )

    def _borrow_accesses_for_expression(
        self,
        expression: Expression,
        mode: str,
        scope: SemanticScope,
        expected_type: TypeReference | None = None,
    ) -> tuple[BorrowAccess, ...]:
        if mode not in BORROW_MODES:
            raise SemanticError(f'Unknown borrow mode "{mode}".')
        if expected_type is not None and self._type_name(self._element_type(expected_type)) in self.views:
            return self._view_borrow_accesses(expression, mode, scope, expected_type)
        return self._place_accesses(expression, mode, scope)

    def _view_borrow_accesses(
        self, expression: Expression, mode: str, scope: SemanticScope, expected_type: TypeReference
    ) -> tuple[BorrowAccess, ...]:
        accesses: list[BorrowAccess] = []
        for field_access in self._view_borrow_field_accesses(
            expression, mode, scope, expected_type
        ):
            accesses.extend(field_access.accesses)
        return tuple(accesses)

    def _place_accesses(
        self, target: str | Expression, mode: str, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        paths = self._raw_place_paths(target)
        if not paths:
            raise SemanticError('Cannot borrow a temporary value.')
        accesses: list[BorrowAccess] = []
        for path in paths:
            accesses.extend(self._normalize_borrow_path(path, mode, scope))
        return tuple(accesses)

    def _raw_place_paths(self, target: str | Expression) -> tuple[BorrowPath, ...]:
        if type(target) is str:
            return (self._path_from_name(target),)
        if type(target) is VariableExpression:
            return (self._path_from_name(target.name),)
        if type(target) is IndexExpression:
            return tuple(self._append_path(path, ('*',)) for path in self._raw_place_paths(target.target))
        if type(target) is SliceExpression:
            return tuple(self._append_path(path, ('*',)) for path in self._raw_place_paths(target.target))
        return ()

    def _path_from_name(self, name: str) -> BorrowPath:
        parts = name.split('.')
        if any(part == '' for part in parts):
            raise SemanticError(f'Invalid name "{name}".')
        return BorrowPath(parts[0], tuple(parts[1:]))

    def _append_path(self, path: BorrowPath, suffix: tuple[str, ...]) -> BorrowPath:
        return BorrowPath(path.root, (*path.fields, *suffix))

    def _unique_borrow_accesses(
        self, accesses: tuple[BorrowAccess, ...]
    ) -> tuple[BorrowAccess, ...]:
        seen: set[tuple[str, tuple[str, ...], str]] = set()
        unique: list[BorrowAccess] = []
        for access in accesses:
            key = (access.path.root, access.path.fields, access.mode)
            if key in seen:
                continue
            seen.add(key)
            unique.append(access)
        return tuple(unique)

    def _normalize_borrow_path(
        self, path: BorrowPath, mode: str, scope: SemanticScope
    ) -> tuple[BorrowAccess, ...]:
        info = scope.get(path.root)
        if info is None:
            return (BorrowAccess(path, mode),)

        if path.fields and info.view_borrow_accesses:
            field_name = path.fields[0]
            field_access = next(
                (field for field in info.view_borrow_accesses if field.field_name == field_name),
                None,
            )
            if field_access is not None:
                suffix = path.fields[1:]
                accesses: list[BorrowAccess] = []
                for origin in field_access.accesses:
                    if not borrow_mode_compatible(mode, origin.mode):
                        raise SemanticError(
                            f'Cannot create &{mode} access through &{origin.mode} view field "{path.root}.{field_name}".'
                        )
                    accesses.append(BorrowAccess(self._append_path(origin.path, suffix), mode))
                return tuple(accesses)

        view_field = self._view_field_for_borrow_path(info.type_ref, path)
        if view_field is not None:
            if not borrow_mode_compatible(mode, view_field.mode):
                raise SemanticError(
                    f'Cannot create &{mode} access through &{view_field.mode} view field "{path.root}.{view_field.name}".'
                )
            return (BorrowAccess(path, mode),)

        if not info.borrow_accesses:
            if info.type_ref is not None and self._is_borrow_type(info.type_ref):
                actual_mode = info.type_ref.borrow
                if not borrow_mode_compatible(mode, actual_mode):
                    raise SemanticError(
                        f'Cannot create &{mode} access through &{actual_mode} borrow "{path.root}".'
                    )
            return (BorrowAccess(path, mode),)
        accesses = []
        for origin in info.borrow_accesses:
            if not borrow_mode_compatible(mode, origin.mode):
                raise SemanticError(
                    f'Cannot create &{mode} access through &{origin.mode} borrow "{path.root}".'
                )
            accesses.append(BorrowAccess(self._append_path(origin.path, path.fields), mode))
        return tuple(accesses)

    def _borrow_source_owners(self, expression: Expression, scope: SemanticScope) -> set[str]:
        if type(expression) is BorrowExpression:
            return self._place_source_owners(expression.expr, scope)
        return self._place_source_owners(expression, scope)

    def _place_source_owners(self, target: str | Expression, scope: SemanticScope) -> set[str]:
        return {path.root for path in self._raw_place_paths(target) if self._is_tracked_borrow_owner(path.root, scope)}

    def _is_tracked_borrow_owner(self, name: str, scope: SemanticScope) -> bool:
        info = scope.get(name)
        return info is not None and bool(info.borrow_accesses)

    def _check_borrow_conflicts(
        self,
        accesses: tuple[BorrowAccess, ...],
        scope: SemanticScope,
        context: str,
        ignore_owners: set[str] | None = None,
    ) -> None:
        ignored = ignore_owners or set()
        for access in accesses:
            for live in scope.live_borrows():
                if live.owner in ignored:
                    continue
                if self._borrow_accesses_conflict(access, live.access):
                    raise SemanticError(
                        f'Cannot access "{self._borrow_path_label(access.path)}" for {context}; '
                        f'it overlaps live &{live.access.mode} borrow of '
                        f'"{self._borrow_path_label(live.access.path)}" held by "{live.owner}".'
                    )

    def _check_temporary_borrow_conflicts(
        self,
        accesses: tuple[BorrowAccess, ...],
        existing: list[tuple[str, BorrowAccess]],
        context: str,
    ) -> None:
        for access in accesses:
            for owner, existing_access in existing:
                if self._borrow_accesses_conflict(access, existing_access):
                    raise SemanticError(
                        f'Cannot access "{self._borrow_path_label(access.path)}" for {context}; '
                        f'it overlaps temporary &{existing_access.mode} borrow of '
                        f'"{self._borrow_path_label(existing_access.path)}" for parameter "{owner}".'
                    )

    def _borrow_accesses_conflict(self, left: BorrowAccess, right: BorrowAccess) -> bool:
        return (
            self._borrow_paths_overlap(left.path, right.path)
            and (borrow_mode_can_write(left.mode) or borrow_mode_can_write(right.mode))
        )

    def _borrow_paths_overlap(self, left: BorrowPath, right: BorrowPath) -> bool:
        if left.root != right.root:
            return False
        left_fields = left.fields
        right_fields = right.fields
        for index in range(min(len(left_fields), len(right_fields))):
            if left_fields[index] == '*' or right_fields[index] == '*':
                return True
            if left_fields[index] != right_fields[index]:
                return False
        return True

    def _borrow_path_label(self, path: BorrowPath) -> str:
        label = path.root
        for field in path.fields:
            if field == '*':
                label += '[..]'
            else:
                label += f'.{field}'
        return label

    def _view_accepts_type(self, view_name: str, actual: TypeReference) -> bool:
        actual_name = self._type_name(self._element_type(actual))
        if actual_name == view_name:
            return True
        declaration = self.types.get(actual_name)
        if declaration is None or declaration.extern or declaration.parameters:
            return False
        fields = {field.name: field for field in declaration.fields}
        for view_field in self.views[view_name].fields:
            actual_field = fields.get(view_field.name)
            if actual_field is None:
                return False
            if not self._view_field_type_matches(view_field.type, actual_field.type):
                return False
        return True

    def _view_field_type_matches(self, expected: TypeReference, actual: TypeReference) -> bool:
        if expected.borrow is not None or actual.borrow is not None:
            return False
        if expected.is_slice != actual.is_slice:
            return False
        if (expected.array_size is None) != (actual.array_size is None):
            return False
        if expected.array_size is not None and self._array_size_key(expected.array_size) != self._array_size_key(actual.array_size):
            return False
        return self._type_name(self._element_type(expected)) == self._type_name(self._element_type(actual))

    def _is_view_borrow_type(self, type_ref: TypeReference | None) -> bool:
        return (
            type_ref is not None
            and self._is_borrow_type(type_ref)
            and self._type_name(self._element_type(type_ref)) in self.views
        )

    def _view_field_for_type(
        self, type_ref: TypeReference | None, field_name: str
    ) -> ViewField | None:
        if not self._is_view_borrow_type(type_ref):
            return None
        view_name = self._type_name(self._element_type(type_ref))
        view = self.views[view_name]
        field = next((field for field in view.fields if field.name == field_name), None)
        if field is None:
            raise SemanticError(f'View "{view_name}" has no field "{field_name}".')
        return field

    def _view_field_for_borrow_path(
        self, type_ref: TypeReference | None, path: BorrowPath
    ) -> ViewField | None:
        if not path.fields:
            return None
        return self._view_field_for_type(type_ref, path.fields[0])

    def _borrow_target_type(self, target: Expression, scope: SemanticScope) -> TypeReference:
        if type(target) is SliceExpression:
            return self._slice_target_type(target, scope, require_readable=False)
        if type(target) is IndexExpression:
            index_type = self._expression_value_type(target.index, scope)
            if not self._is_integer_type(index_type):
                raise SemanticError(f'Index must be an integer, got "{self._type_name(index_type)}".')
            container_type = self._expression_type(target.target, scope, check_reads=False)
            return self._indexed_element_type(container_type)
        return self._expression_type(target, scope, check_reads=False)

    def _assignment_target_type(
        self, target: str | Expression, scope: SemanticScope
    ) -> TypeReference:
        if type(target) is str:
            return self._resolve_name_type(target, scope)
        if type(target) is VariableExpression:
            return self._resolve_name_type(target.name, scope)
        if type(target) is IndexExpression:
            index_type = self._expression_value_type(target.index, scope)
            if not self._is_integer_type(index_type):
                raise SemanticError(f'Index must be an integer, got "{self._type_name(index_type)}".')
            container_type = self._expression_type(target.target, scope, check_reads=False)
            if self._is_read_only_index_target(container_type):
                raise SemanticError('Cannot assign through a read-only borrow or slice.')
            return self._indexed_element_type(container_type)
        raise SemanticError(f'Unsupported assignment target "{type(target).__name__}".')

    def _indexed_element_type(self, type_ref: TypeReference) -> TypeReference:
        if self._is_array_type(type_ref) or self._is_slice_type(type_ref):
            return self._element_type(type_ref)
        raise SemanticError(f'Cannot index value of type "{self._type_name(type_ref)}".')

    def _slice_type(self, expression: SliceExpression, scope: SemanticScope) -> TypeReference:
        return self._slice_target_type(expression, scope, require_readable=True)

    def _slice_target_type(
        self, expression: SliceExpression, scope: SemanticScope, require_readable: bool
    ) -> TypeReference:
        target_type = self._expression_type(expression.target, scope, check_reads=False)
        if (
            require_readable
            and (self._is_slice_type(target_type) or self._is_borrow_type(target_type))
            and not borrow_mode_can_read(target_type.borrow)
        ):
            raise SemanticError('Cannot read through a write-only borrow or slice.')
        if not (self._is_array_type(target_type) or self._is_slice_type(target_type)):
            raise SemanticError(f'Cannot slice value of type "{self._type_name(target_type)}".')
        if expression.start is not None:
            self._expect_integer_expression(expression.start, scope, 'slice start')
        if expression.end is not None:
            self._expect_integer_expression(expression.end, scope, 'slice end')
        return TypeReference(self._element_type(target_type).name, is_slice=True)

    def _expect_integer_expression(
        self, expression: Expression, scope: SemanticScope, context: str
    ) -> None:
        expr_type = self._expression_value_type(expression, scope)
        if not self._is_integer_type(expr_type):
            raise SemanticError(f'Expected integer for {context}, got "{self._type_name(expr_type)}".')

    def _expect_assignable(
        self,
        target_type: TypeReference,
        source_type: TypeReference,
        source_expression: Expression | None,
        context: str,
    ) -> None:
        if (
            source_expression is not None
            and type(source_expression) is not MoveExpression
            and self._raw_place_paths(source_expression)
            and not self._is_borrow_type(target_type)
            and not self._is_copyable_type(target_type)
        ):
            label = (
                source_expression.name
                if type(source_expression) is VariableExpression
                else 'place expression'
            )
            raise SemanticError(
                f'Cannot copy non-copyable value "{label}"; use move on a whole local.'
            )
        if self._is_assignable(target_type, source_type, source_expression):
            return
        raise SemanticError(
            f'Cannot assign value of type "{self._type_name(source_type)}" to {context} of type "{self._type_name(target_type)}".'
        )

    def _is_assignable(
        self,
        target_type: TypeReference,
        source_type: TypeReference,
        source_expression: Expression | None,
    ) -> bool:
        if self._is_void_type(target_type) or self._is_void_type(source_type):
            return self._is_void_type(target_type) and self._is_void_type(source_type)
        if self._is_borrow_type(target_type):
            return self._borrow_compatible(target_type, source_type)
        if self._is_array_type(target_type):
            return (
                self._is_array_type(source_type)
                and target_type.array_size == source_type.array_size
                and self._same_element_type(target_type, source_type)
                and self._is_copyable_type(target_type)
            )
        if self._is_slice_type(target_type):
            return self._is_slice_type(source_type) and self._same_element_type(target_type, source_type)

        target_name = self._type_name(target_type)
        source_name = self._type_name(source_type)
        if is_builtin_type(target_name) and is_builtin_type(source_name):
            if not builtin_conversion_allowed(source_name, target_name):
                return False
            if type(source_expression) is LiteralExpression:
                try:
                    cast_builtin_value(source_expression.value, target_name, source_type=source_expression.type)
                except (TypeError, ValueError, OverflowError):
                    return False
            return True
        return target_name == source_name

    def _is_copyable_type(
        self, type_ref: TypeReference, seen: set[str] | None = None
    ) -> bool:
        if type_ref.borrow == 'in':
            return True
        if type_ref.borrow in {'out', 'inout'} or type_ref.is_slice:
            return False
        element = self._element_type(type_ref)
        name = self._type_name(element)
        if is_builtin_type(name) or name in {'str', 'c_char', 'c_void', 'type'}:
            return True
        declaration = self.types.get(name)
        if declaration is None:
            return True
        if (name, 'Copyable') in self.implementations:
            return True
        if declaration.extern or any(method.name == 'deinit' for method in declaration.methods):
            return False
        seen = set(seen or ())
        if name in seen:
            return True
        seen.add(name)
        return all(self._is_copyable_type(field.type, seen) for field in declaration.fields)

    def _borrow_compatible(self, expected: TypeReference, actual: TypeReference) -> bool:
        if not self._is_borrow_type(actual):
            return False
        if not borrow_mode_compatible(expected.borrow, actual.borrow):
            return False
        expected_name = self._type_name(self._element_type(expected))
        actual_name = self._type_name(self._element_type(actual))
        if expected_name in self.views:
            return self._view_accepts_type(expected_name, actual)
        if actual_name in self.views:
            return expected_name == actual_name
        if expected_name in {'c_char', 'c_void'}:
            return True
        if self._is_array_type(expected):
            return self._same_array_type(expected, actual)
        if self._is_slice_type(expected):
            return self._is_slice_type(actual) and self._same_element_type(expected, actual)
        return self._same_element_type(expected, actual)

    def _same_array_type(self, left: TypeReference, right: TypeReference) -> bool:
        return (
            self._is_array_type(right)
            and self._same_element_type(left, right)
            and self._array_size_key(left.array_size) == self._array_size_key(right.array_size)
        )

    def _same_element_type(self, left: TypeReference, right: TypeReference) -> bool:
        return self._type_name(self._element_type(left)) == self._type_name(self._element_type(right))

    def _resolve_name_type(self, name: str, scope: SemanticScope) -> TypeReference:
        parts = name.split('.')
        if any(part == '' for part in parts):
            raise SemanticError(f'Invalid name "{name}".')
        info = scope.get(parts[0])
        if info is None:
            raise SemanticError(f'Unknown name "{parts[0]}".')
        if info.kind != 'variable' or info.type_ref is None:
            raise SemanticError(f'Name "{parts[0]}" is not a value.')
        self._require_symbol_visible('Name', parts[0], info)
        current_type = info.type_ref
        for field_name in parts[1:]:
            view_field = self._view_field_for_type(current_type, field_name)
            if view_field is not None:
                current_type = view_field.type
                continue

            type_decl = self._type_declaration_for(current_type)
            field = next((field for field in type_decl.fields if field.name == field_name), None)
            if field is None:
                raise SemanticError(f'Type "{type_decl.name}" has no field "{field_name}".')
            if not self._is_same_module(type_decl.module_name):
                raise SemanticError(
                    f'Field "{field_name}" is private to module "{type_decl.module_name}".'
                )
            current_type = field.type
        return current_type

    def _validate_type_runtime_expressions(
        self, type_ref: TypeReference, scope: SemanticScope, context: str
    ) -> None:
        if type_ref.array_size is not None:
            self._expect_integer_expression(type_ref.array_size, scope, context)

    def _validate_variable_type(self, name: str, type_ref: TypeReference, allow_void: bool) -> None:
        self._validate_type_reference(type_ref, allow_void=allow_void)
        if self._is_void_type(type_ref) and not allow_void:
            raise SemanticError(f'Variable "{name}" cannot have type "void".')
        if self._is_type_type(type_ref):
            raise SemanticError(f'Type variable "{name}" must be consumed during the compile-time pass.')
        if self._is_opaque_extern_type(type_ref) and not self._is_borrow_type(type_ref):
            raise SemanticError(
                f'Opaque extern type "{type_ref.name}" cannot be used by value; use an explicit borrow type.'
            )

    def _validate_type_reference(self, type_ref: TypeReference, allow_void: bool) -> None:
        if type_ref.arguments:
            raise SemanticError(f'Unresolved generic type "{type_ref.name}".')
        if type_ref.borrow is not None and type_ref.borrow not in BORROW_MODES:
            raise SemanticError(f'Unknown borrow mode "{type_ref.borrow}".')
        if type_ref.array_size is not None and type_ref.is_slice:
            raise SemanticError(f'Type "{type_ref.name}" cannot be both array and slice.')
        if type_ref.name == 'void' and not allow_void:
            raise SemanticError('Only return types can be void.')
        if type_ref.name in self.views:
            if type_ref.borrow is None:
                raise SemanticError(f'View "{type_ref.name}" can only be used behind an explicit borrow type.')
            if type_ref.borrow != 'inout':
                raise SemanticError(
                    f'View borrow type "{self._type_name(type_ref)}" must use &inout; view fields carry their own access modes.'
                )
            if type_ref.array_size is not None or type_ref.is_slice:
                raise SemanticError(f'View "{type_ref.name}" cannot be used as an array or slice element type.')
            view = self.views[type_ref.name]
            self._require_declaration_visible('View', type_ref.name, view)
            return
        if type_ref.name in {'c_char', 'c_void'} and type_ref.borrow is None:
            raise SemanticError(f'{type_ref.name} can only be used behind an explicit borrow type.')
        if type_ref.name == 'type':
            raise SemanticError('Comptime type value reached semantic validation.')
        if type_ref.is_slice and type_ref.borrow is None:
            raise SemanticError(
                f'Bare slice type "{self._type_name(type_ref)}" is not allowed; use "&in {type_ref.name}[]" or "&inout {type_ref.name}[]".'
            )
        if not self._is_known_base_type(type_ref.name):
            raise SemanticError(f'Unknown type "{type_ref.name}".')
        declaration = self.types.get(type_ref.name)
        if declaration is not None:
            self._require_declaration_visible('Type', type_ref.name, declaration)
        if type_ref.array_size is not None:
            # Array sizes may be runtime expressions for local arrays/VLAs, so only check
            # simple literal shape here. Expression contexts validate names separately.
            if (
                type(type_ref.array_size) is LiteralExpression
                and not is_integer_type(type_ref.array_size.type)
            ):
                raise SemanticError('Array size literals must be integer values.')
        if (type_ref.array_size is not None or type_ref.is_slice or type_ref.borrow is not None) and type_ref.name == 'void':
            raise SemanticError('void cannot be used as an array, slice, or borrow element type.')

    def _validate_abi(self, abi: str | None, context: str) -> None:
        if abi is not None and abi != 'c':
            raise SemanticError(f'Unsupported ABI "{abi}" for {context}.')

    def _is_opaque_extern_type(self, type_ref: TypeReference) -> bool:
        declaration = self.types.get(type_ref.name)
        return declaration is not None and declaration.extern

    def _is_known_base_type(self, name: str) -> bool:
        return name in RUNTIME_SCALAR_TYPES or name == 'void' or name in self.types

    def _require_symbol_visible(self, kind: str, name: str, info: SymbolInfo) -> None:
        source_name = info.source_name or name
        if self._is_symbol_visible(source_name, info):
            return
        self._raise_visibility_error(kind, source_name, info.module_name, info.public)

    def _require_declaration_visible(
        self, kind: str, name: str, declaration: Statement
    ) -> None:
        module_name = getattr(declaration, 'module_name', None)
        public = getattr(declaration, 'public', False)
        source_name = getattr(declaration, 'source_name', None) or name
        if self._is_public_or_same_module(module_name, public, source_name):
            return
        self._raise_visibility_error(kind, source_name, module_name, public)

    def _raise_visibility_error(
        self, kind: str, name: str, module_name: str | None, public: bool
    ) -> None:
        if public:
            raise SemanticError(
                f'{kind} "{name}" from module "{module_name}" is not imported by module "{self.current_module_name}".'
            )
        raise SemanticError(f'{kind} "{name}" is private to module "{module_name}".')

    def _is_symbol_visible(self, source_name: str, info: SymbolInfo) -> bool:
        return self._is_public_or_same_module(info.module_name, info.public, source_name)

    def _is_public_or_same_module(
        self, module_name: str | None, public: bool, symbol_name: str
    ) -> bool:
        if self._is_same_module(module_name):
            return True
        return public and self._is_imported_symbol_visible(module_name, symbol_name)

    def _is_imported_symbol_visible(
        self, module_name: str | None, symbol_name: str
    ) -> bool:
        if module_name is None:
            return True
        symbol_name = self._import_symbol_name(symbol_name)
        for binding in self.current_qualified_imports:
            if binding.module_name != module_name:
                continue
            if binding.symbols is None or symbol_name in binding.symbols:
                return True
        for binding in self.current_imports:
            if binding.module_name != module_name or binding.alias is not None:
                continue
            if binding.symbols is None or symbol_name in binding.symbols:
                return True
        return False

    def _import_symbol_name(self, name: str) -> str:
        return name.split('$', 1)[0]

    def _is_same_module(self, module_name: str | None) -> bool:
        return module_name is None or self.current_module_name is None or module_name == self.current_module_name

    def _type_declaration_for(self, type_ref: TypeReference) -> TypeDeclaration:
        type_name = self._type_name(self._element_type(type_ref))
        declaration = self.types.get(type_name)
        if declaration is None:
            raise SemanticError(f'Type "{type_name}" has no fields or methods.')
        return declaration

    def _contains_non_writable_borrow(self, expression: Expression, scope: SemanticScope) -> bool:
        if type(expression) is SliceExpression:
            target_type = self._expression_type(expression.target, scope, check_reads=False)
            return target_type.borrow is not None and not borrow_mode_can_write(target_type.borrow)
        expr_type = self._expression_type(expression, scope, check_reads=False)
        return expr_type.borrow is not None and not borrow_mode_can_write(expr_type.borrow)

    def _contains_non_readable_borrow(self, expression: Expression, scope: SemanticScope) -> bool:
        if type(expression) is SliceExpression:
            target_type = self._expression_type(expression.target, scope, check_reads=False)
            return target_type.borrow is not None and not borrow_mode_can_read(target_type.borrow)
        expr_type = self._expression_type(expression, scope, check_reads=False)
        return expr_type.borrow is not None and not borrow_mode_can_read(expr_type.borrow)

    def _is_read_only_index_target(self, type_ref: TypeReference) -> bool:
        return (
            (self._is_slice_type(type_ref) or self._is_borrow_type(type_ref))
            and type_ref.borrow is not None
            and not borrow_mode_can_write(type_ref.borrow)
        )

    def _is_printable_type(self, type_ref: TypeReference) -> bool:
        type_name = self._type_name(type_ref)
        return is_builtin_type(type_name) or self._is_str_type(type_ref)

    def _is_void_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'void'

    def _is_type_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'type'

    def _is_str_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'str'

    def _is_bool_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'bool'

    def _is_integer_type(self, type_ref: TypeReference) -> bool:
        return is_integer_type(self._type_name(type_ref))

    def _is_array_type(self, type_ref: TypeReference) -> bool:
        return type_ref.array_size is not None

    def _is_slice_type(self, type_ref: TypeReference) -> bool:
        return type_ref.is_slice

    def _is_borrow_type(self, type_ref: TypeReference) -> bool:
        return type_ref.borrow is not None

    def _element_type(self, type_ref: TypeReference) -> TypeReference:
        return TypeReference(type_ref.name, copy.deepcopy(type_ref.arguments))

    def _type_name(self, type_ref: TypeReference) -> str:
        if type_ref.arguments:
            raise SemanticError(f'Unresolved generic type "{type_ref.name}".')
        name = type_ref.name
        if type_ref.array_size is not None:
            name = f'{name}[{self._array_size_key(type_ref.array_size)}]'
        elif type_ref.is_slice:
            name = f'{name}[]'
        if type_ref.borrow is not None:
            name = f'&{type_ref.borrow} {name}'
        return name

    def _array_size_key(self, expression: Expression | None) -> str:
        if expression is None:
            return '<missing>'
        if type(expression) is LiteralExpression:
            return str(expression.value)
        if type(expression) is VariableExpression:
            return expression.name
        if type(expression) is CompositeExpression:
            return f'({self._array_size_key(expression.left)}{expression.operator}{self._array_size_key(expression.right)})'
        return type(expression).__name__
