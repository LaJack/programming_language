from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import unquote, urlparse

from .ast_nodes import (
    Assignment,
    BorrowExpression,
    CatchClause,
    CompositeExpression,
    Expression,
    For,
    FormattedStringExpression,
    FunctionCall,
    FunctionDeclaration,
    If,
    ImplementationDeclaration,
    InterfaceDeclaration,
    ImportDeclaration,
    IndexExpression,
    LiteralExpression,
    ModuleDeclaration,
    MoveExpression,
    Print,
    Raise,
    Return,
    SliceExpression,
    Statement,
    StructLiteralExpression,
    Try,
    TypeDeclaration,
    TypeExpression,
    VariableDeclaration,
    VariableExpression,
    ViewDeclaration,
    While,
)
from .builtin_types import (
    BUILTIN_TYPE_SPECS,
    builtin_conversion_allowed,
    cast_builtin_value,
)
from .comptime_externs import default_comptime_externs
from .hir_lowering_pass import compile_to_hir
from .module_loader import LoadedSourceGraph, ModuleLoadError, load_source_graph
from .parser import Lexer, ParseError, Token, parse, parse_recovering
from .source_model import SourceSpan, TypeReference


JACK_SUFFIXES = {'.jack', '.jk'}
SKIPPED_DIRECTORIES = {
    '.git', '.jack', '.venv', 'venv', 'node_modules', 'build', 'dist',
    '__pycache__',
}
JACK_KEYWORDS = {
    'as', 'catch', 'comptime', 'else', 'extern', 'false', 'for', 'if',
    'implements', 'import', 'in', 'inout', 'interface', 'module', 'move', 'out',
    'print', 'pub', 'raise', 'raises', 'rethrow', 'return', 'struct', 'true',
    'try', 'use', 'view', 'while',
}
BUILTIN_TYPES = {
    'void', 'str', 'c_char', 'c_void', 'type', 'Copyable', *BUILTIN_TYPE_SPECS
}
BUILTIN_FUNCTIONS = {'len', 'sizeof', 'alignof'}


def path_from_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != 'file':
        return None
    return Path(unquote(parsed.path)).resolve()


def uri_from_path(path: Path) -> str:
    return path.resolve().as_uri()


@dataclass(frozen=True)
class SemanticSymbol:
    id: str
    name: str
    kind: str
    type_label: str | None
    signature: str
    span: SourceSpan
    selection_span: SourceSpan
    module_name: str
    public: bool = False
    extern: bool = False
    renameable: bool = True
    container_id: str | None = None
    declaration_offset: int = 0
    resolved_type: str | None = None
    parameter_labels: tuple[str, ...] = ()

    @property
    def path(self) -> Path:
        assert self.span.source_path is not None
        return Path(self.span.source_path)


@dataclass(frozen=True)
class SemanticOccurrence:
    symbol_id: str
    span: SourceSpan
    declaration: bool = False
    role: str = 'reference'

    @property
    def path(self) -> Path:
        assert self.span.source_path is not None
        return Path(self.span.source_path)


@dataclass
class SemanticScopeRecord:
    path: Path
    span: SourceSpan
    parent: 'SemanticScopeRecord | None' = None
    symbols: list[str] = field(default_factory=list)


@dataclass
class SemanticModel:
    symbols: dict[str, SemanticSymbol] = field(default_factory=dict)
    occurrences: list[SemanticOccurrence] = field(default_factory=list)
    scopes: list[SemanticScopeRecord] = field(default_factory=list)
    members: dict[str, list[str]] = field(default_factory=dict)
    sources: dict[Path, str] = field(default_factory=dict)
    modules: dict[str, Path] = field(default_factory=dict)
    versions: dict[Path, int | None] = field(default_factory=dict)
    complete: bool = True

    def merge(self, other: 'SemanticModel') -> None:
        self.symbols.update(other.symbols)
        existing = {
            (item.symbol_id, item.span.source_path, item.span.start_offset,
             item.span.end_offset, item.declaration)
            for item in self.occurrences
        }
        for item in other.occurrences:
            key = (
                item.symbol_id, item.span.source_path, item.span.start_offset,
                item.span.end_offset, item.declaration,
            )
            if key not in existing:
                existing.add(key)
                self.occurrences.append(item)
        self.scopes.extend(other.scopes)
        for owner, values in other.members.items():
            merged = self.members.setdefault(owner, [])
            merged.extend(value for value in values if value not in merged)
        self.sources.update(other.sources)
        self.modules.update(other.modules)
        self.versions.update(other.versions)
        self.complete = self.complete and other.complete

    def occurrence_at(self, path: Path, offset: int) -> SemanticOccurrence | None:
        candidates = [
            item for item in self.occurrences
            if item.span.source_path == str(path.resolve())
            and item.span.start_offset <= offset < item.span.end_offset
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda item: item.span.end_offset - item.span.start_offset)

    def scope_at(self, path: Path, offset: int) -> SemanticScopeRecord | None:
        candidates = [
            scope for scope in self.scopes
            if scope.path == path.resolve()
            and scope.span.start_offset <= offset <= scope.span.end_offset
        ]
        return min(
            candidates,
            key=lambda scope: scope.span.end_offset - scope.span.start_offset,
            default=None,
        )

    def visible_symbols(self, path: Path, offset: int) -> list[SemanticSymbol]:
        values: list[SemanticSymbol] = []
        seen: set[str] = set()
        scope = self.scope_at(path, offset)
        while scope is not None:
            for symbol_id in reversed(scope.symbols):
                symbol = self.symbols[symbol_id]
                if symbol.name in seen or symbol.declaration_offset > offset:
                    continue
                seen.add(symbol.name)
                values.append(symbol)
            scope = scope.parent
        for symbol in self.symbols.values():
            if symbol.container_id is not None or symbol.name in seen:
                continue
            if symbol.kind in {'module', 'alias'}:
                continue
            if symbol.public or symbol.path == path.resolve():
                seen.add(symbol.name)
                values.append(symbol)
        return values


@dataclass(frozen=True)
class AnalysisDiagnostic:
    message: str
    span: SourceSpan | None
    severity: int = 1
    code: str | None = None
    related: tuple[tuple[str, SourceSpan], ...] = ()


@dataclass
class ProjectAnalysis:
    model: SemanticModel
    diagnostics: list[AnalysisDiagnostic] = field(default_factory=list)
    deferred_comptime: bool = False
    syntax_incomplete: bool = False


class ProjectAnalyzer:
    def __init__(
        self,
        roots: Iterable[Path],
        *,
        module_roots: Iterable[Path] = (),
        import_overrides: Mapping[str, str] | None = None,
    ) -> None:
        self.roots = self._unique_roots([*roots, *module_roots])
        self.module_roots = self._unique_roots(module_roots)
        self.import_overrides = dict(import_overrides or {})
        self._cache: dict[tuple[Path, str, bool], ProjectAnalysis] = {}

    @staticmethod
    def _unique_roots(roots: Iterable[Path]) -> list[Path]:
        values: list[Path] = []
        for root in roots:
            resolved = root.resolve()
            if resolved not in values:
                values.append(resolved)
        return values

    def discover_files(self) -> list[Path]:
        files: set[Path] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.rglob('*'):
                if any(part in SKIPPED_DIRECTORIES for part in path.relative_to(root).parts):
                    continue
                if path.is_file() and path.suffix in JACK_SUFFIXES:
                    files.add(path.resolve())
        return sorted(files)

    def analyze(
        self,
        focus: Path,
        *,
        overlays: Mapping[Path, str] | None = None,
        versions: Mapping[Path, int | None] | None = None,
        full_comptime: bool = False,
    ) -> ProjectAnalysis:
        focus = focus.resolve()
        overlays = {path.resolve(): text for path, text in (overlays or {}).items()}
        versions = {path.resolve(): value for path, value in (versions or {}).items()}
        files = self.discover_files()
        if focus not in files:
            files.append(focus)
            files.sort()
        digest = hashlib.sha256()
        project_sources: dict[Path, str] = {}
        for path in files:
            digest.update(str(path).encode())
            source = overlays.get(path)
            if source is None:
                try:
                    source = path.read_text()
                except OSError:
                    source = ''
            digest.update(source.encode())
            project_sources[path] = source
        cache_key = (focus, digest.hexdigest(), full_comptime)
        cached = self._cache.get(cache_key)
        if cached is not None:
            cached.model.versions.update(versions)
            return cached

        model = SemanticModel(versions=dict(versions))
        diagnostics: list[AnalysisDiagnostic] = []
        declared_modules: dict[str, tuple[Path, SourceSpan | None]] = {}
        syntax_incomplete = False
        for path, source in project_sources.items():
            recovered = parse_recovering(source, source_path=path)
            statements = recovered.statements
            if recovered.diagnostics:
                syntax_incomplete = True
                diagnostics.extend(
                    AnalysisDiagnostic(item.message, item.span)
                    for item in recovered.diagnostics
                )
            declaration = next(
                (node for node in statements if isinstance(node, ModuleDeclaration)),
                None,
            )
            if declaration is None:
                continue
            previous = declared_modules.get(declaration.name)
            if previous is not None and previous[0] != path:
                diagnostics.append(AnalysisDiagnostic(
                    f'Module "{declaration.name}" is declared by both '
                    f'"{previous[0]}" and "{path}".',
                    declaration.span,
                ))
            else:
                declared_modules[declaration.name] = (path, declaration.span)
        if syntax_incomplete:
            result = ProjectAnalysis(
                model,
                _deduplicate_diagnostics(diagnostics),
                syntax_incomplete=True,
            )
            self._cache[cache_key] = result
            if len(self._cache) > 16:
                self._cache.pop(next(iter(self._cache)))
            return result
        deferred = False
        seen_graphs: set[tuple[tuple[str, str], ...]] = set()
        for entry in [focus, *(path for path in files if path != focus)]:
            try:
                graph = load_source_graph(
                    entry,
                    import_overrides=self.import_overrides,
                    search_roots=self.module_roots or self.roots,
                    source_overlays=overlays,
                )
            except (ParseError, ModuleLoadError) as err:
                diagnostics.append(AnalysisDiagnostic(str(err), getattr(err, 'span', None)))
                continue
            graph_key = tuple(sorted(
                (name, str(module.path)) for name, module in graph.modules.items()
            ))
            builder = _GraphIndexBuilder(graph, overlays)
            graph_model = builder.build()
            model.merge(graph_model)
            diagnostics.extend(builder.diagnostics)
            if graph_key in seen_graphs:
                continue
            seen_graphs.add(graph_key)
            try:
                compile_to_hir(
                    copy.deepcopy(graph.ast),
                    print_handler=None,
                    externs=default_comptime_externs() if full_comptime else {},
                )
            except Exception as err:
                message = str(err)
                if (
                    not full_comptime
                    and ('No comptime extern binding registered' in message
                         or 'without a registered C host binding' in message)
                ):
                    deferred = True
                else:
                    diagnostics.append(AnalysisDiagnostic(
                        message, getattr(err, 'span', None)
                    ))

        diagnostics = _cap_diagnostics(_deduplicate_diagnostics(diagnostics))
        result = ProjectAnalysis(model, diagnostics, deferred)
        self._cache[cache_key] = result
        if len(self._cache) > 16:
            self._cache.pop(next(iter(self._cache)))
        return result


class _GraphIndexBuilder:
    def __init__(
        self, graph: LoadedSourceGraph, overlays: Mapping[Path, str]
    ) -> None:
        self.graph = graph
        self.overlays = overlays
        self.model = SemanticModel()
        self.top_by_internal: dict[str, str] = {}
        self.type_declarations: dict[str, TypeDeclaration | ViewDeclaration] = {}
        self.functions: dict[str, FunctionDeclaration] = {}
        self.tokens: dict[Path, list[Token]] = {}
        self.diagnostics: list[AnalysisDiagnostic] = []
        self.type_parameters: set[str] = set()

    def build(self) -> SemanticModel:
        for module_name, module in sorted(self.graph.modules.items()):
            source = self.overlays.get(module.path)
            if source is None:
                source = module.path.read_text()
            self.model.sources[module.path] = source
            self.model.modules[module_name] = module.path
            self.tokens[module.path] = Lexer(source, source_path=module.path).tokenize()
            for statement in module.ast:
                if type(statement) in {
                    TypeDeclaration, ViewDeclaration, InterfaceDeclaration,
                    FunctionDeclaration,
                    VariableDeclaration,
                }:
                    self._declare_top(module_name, statement)
        for module_name, module in sorted(self.graph.modules.items()):
            root_span = _source_span(module.path, self.model.sources[module.path])
            root_scope = SemanticScopeRecord(module.path, root_span)
            self.model.scopes.append(root_scope)
            env = dict(self.top_by_internal)
            for statement in module.ast:
                self._statement(statement, module_name, env, root_scope)
            self._import_occurrences(module_name, module.ast)
        return self.model

    def _declare_top(self, module_name: str, node: Statement) -> None:
        source_name = node.source_name or getattr(node, 'name')
        kind = _declaration_kind(node)
        symbol_id = f'module::{module_name}::{kind}::{source_name}'
        selection = self._declaration_selection(node, source_name)
        type_label = _declaration_type_label(node)
        symbol = SemanticSymbol(
            symbol_id, source_name, kind, type_label,
            _declaration_signature(node, source_name), node.span, selection,
            module_name, node.public, bool(getattr(node, 'extern', False)),
            not bool(getattr(node, 'extern', False)),
            declaration_offset=selection.start_offset,
            resolved_type=(
                node.return_type.name if isinstance(node, FunctionDeclaration)
                else node.type.name if isinstance(node, VariableDeclaration)
                else getattr(node, 'name', None)
            ),
            parameter_labels=_parameter_labels(node),
        )
        self._add_symbol(symbol)
        if isinstance(node, TypeDeclaration):
            self.type_parameters.update(
                parameter.name for parameter in node.parameters
                if parameter.type.name == 'type'
            )
        self.top_by_internal[getattr(node, 'name')] = symbol_id
        if isinstance(node, (TypeDeclaration, ViewDeclaration)):
            self.type_declarations[getattr(node, 'name')] = node
            self._declare_members(symbol, node)
        elif isinstance(node, InterfaceDeclaration):
            self._declare_interface_members(symbol, node)
        elif isinstance(node, FunctionDeclaration):
            self.functions[node.name] = node

    def _declare_members(
        self, owner: SemanticSymbol, node: TypeDeclaration | ViewDeclaration
    ) -> None:
        fields = node.fields
        for field in fields:
            name = field.name
            kind = 'field'
            symbol_id = f'{owner.id}::{kind}::{name}'
            selection = self._declaration_selection(field, name)
            type_ref = field.type
            symbol = SemanticSymbol(
                symbol_id, name, kind, _type_label(type_ref),
                f'field {_type_label(type_ref)} {name}', field.span, selection,
                owner.module_name, owner.public, False, True, owner.id,
                selection.start_offset, resolved_type=type_ref.name,
            )
            self._add_symbol(symbol)
            self.model.members.setdefault(owner.id, []).append(symbol_id)
        if isinstance(node, TypeDeclaration):
            for method in node.methods:
                name = method.name
                symbol_id = f'{owner.id}::method::{name}'
                selection = self._declaration_selection(method, name)
                symbol = SemanticSymbol(
                    symbol_id, name, 'method', _type_label(method.return_type),
                    _function_signature(method, name), method.span, selection,
                    owner.module_name, owner.public, method.extern,
                    not method.extern, owner.id, selection.start_offset,
                    resolved_type=method.return_type.name,
                    parameter_labels=_parameter_labels(method, omit_self=True),
                )
                self._add_symbol(symbol)
                self.model.members.setdefault(owner.id, []).append(symbol_id)

    def _declare_interface_members(
        self, owner: SemanticSymbol, node: InterfaceDeclaration
    ) -> None:
        for method in node.methods:
            selection = self._declaration_selection(method, method.name)
            symbol_id = f'{owner.id}::method::{method.name}'
            symbol = SemanticSymbol(
                symbol_id, method.name, 'method', _type_label(method.return_type),
                _function_signature(method, method.name), method.span, selection,
                owner.module_name, owner.public, False, True, owner.id,
                selection.start_offset, resolved_type=method.return_type.name,
                parameter_labels=_parameter_labels(method, omit_self=True),
            )
            self._add_symbol(symbol)
            self.model.members.setdefault(owner.id, []).append(symbol_id)

    def _add_symbol(self, symbol: SemanticSymbol) -> None:
        self.model.symbols[symbol.id] = symbol
        self.model.occurrences.append(SemanticOccurrence(
            symbol.id, symbol.selection_span, True, 'declaration'
        ))

    def _statement(
        self,
        node: Statement,
        module_name: str,
        env: dict[str, str],
        scope: SemanticScopeRecord,
        owner_type: str | None = None,
    ) -> None:
        if isinstance(node, ModuleDeclaration):
            return
        if isinstance(node, ImportDeclaration):
            return
        if isinstance(node, TypeDeclaration):
            owner_id = self.top_by_internal.get(node.name)
            type_scope = self._child_scope(node.span, scope)
            type_env = dict(env)
            for parameter in node.parameters:
                self._type_reference(parameter.type, type_env)
                for constraint in parameter.constraints:
                    self._type_reference(constraint, type_env)
                symbol = self._declare_local(
                    parameter, module_name, type_scope, 'parameter'
                )
                type_env[parameter.name] = symbol.id
            for field in node.fields:
                self._type_reference(field.type, type_env)
                if field.expr is not None:
                    self._expression(field.expr, type_env)
            for method in node.methods:
                self._function(method, module_name, type_env, owner_id)
            return
        if isinstance(node, InterfaceDeclaration):
            owner_id = self.top_by_internal.get(node.name)
            for method in node.methods:
                self._function(method, module_name, dict(env), owner_id)
            return
        if isinstance(node, ImplementationDeclaration):
            self._type_reference(node.interface, env)
            impl_env = dict(env)
            for parameter in node.parameters:
                self._type_reference(parameter.type, impl_env)
                for constraint in parameter.constraints:
                    self._type_reference(constraint, impl_env)
                symbol = self._declare_local(parameter, module_name, scope, 'parameter')
                impl_env[parameter.name] = symbol.id
            owner_id = self.top_by_internal.get(node.type_name)
            interface_id = self.top_by_internal.get(node.interface.name)
            for use in node.uses:
                if owner_id is not None:
                    target_id = f'{owner_id}::method::{use.name}'
                    if target_id in self.model.symbols:
                        span = self._declaration_selection(use, use.name)
                        self.model.occurrences.append(
                            SemanticOccurrence(target_id, span, False, 'reference')
                        )
            for method in node.methods:
                if interface_id is not None:
                    requirement_id = f'{interface_id}::method::{method.name}'
                    if requirement_id in self.model.symbols:
                        span = self._declaration_selection(method, method.name)
                        self.model.occurrences.append(
                            SemanticOccurrence(
                                requirement_id, span, False, 'definition'
                            )
                        )
                self._function(method, module_name, impl_env, owner_id)
            return
        if isinstance(node, ViewDeclaration):
            for field in node.fields:
                self._type_reference(field.type, env)
            return
        if isinstance(node, FunctionDeclaration):
            self._function(node, module_name, env, owner_type)
            return
        if isinstance(node, VariableDeclaration):
            self._type_reference(node.type, env)
            if node.expr is not None:
                actual = self._expression(node.expr, env)
                self._check_type(
                    node.type, actual, node.expr.span, 'initializer', node.expr
                )
            for argument in node.constructor_args:
                self._expression(argument, env)
            if node.name in self.top_by_internal:
                return
            symbol = self._declare_local(node, module_name, scope, 'variable')
            env[node.name] = symbol.id
            return
        if isinstance(node, Assignment):
            if isinstance(node.name, str):
                expected = self._name_occurrences(node.name, node.span, env)
            else:
                expected = self._expression(node.name, env)
            actual = self._expression(node.expr, env)
            self._check_type(
                expected, actual, node.expr.span, 'assignment', node.expr
            )
            return
        if isinstance(node, FunctionCall):
            self._call(node, env)
            return
        if isinstance(node, Print):
            if node.name and node.expr is None:
                self._name_occurrences(node.name, node.span, env)
            if node.expr is not None:
                self._expression(node.expr, env)
            return
        if isinstance(node, Return) and node.expr is not None:
            self._expression(node.expr, env)
            return
        if isinstance(node, Raise):
            self._expression(node.expr, env)
            return
        if isinstance(node, If):
            for branch in node.branches:
                condition = self._expression(branch.condition, env)
                self._check_type(TypeReference('bool'), condition, branch.condition.span, 'condition')
                child = self._child_scope(branch.span or node.span, scope)
                child_env = dict(env)
                for statement in branch.body:
                    self._statement(statement, module_name, child_env, child, owner_type)
            if node.else_body is not None:
                child = self._child_scope(node.span, scope)
                child_env = dict(env)
                for statement in node.else_body:
                    self._statement(statement, module_name, child_env, child, owner_type)
            return
        if isinstance(node, While):
            condition = self._expression(node.condition, env)
            self._check_type(TypeReference('bool'), condition, node.condition.span, 'condition')
            child = self._child_scope(node.span, scope)
            child_env = dict(env)
            for statement in node.body:
                self._statement(statement, module_name, child_env, child, owner_type)
            return
        if isinstance(node, For):
            child = self._child_scope(node.span, scope)
            child_env = dict(env)
            if node.initializer is not None:
                self._statement(node.initializer, module_name, child_env, child, owner_type)
            if node.condition is not None:
                self._expression(node.condition, child_env)
            if node.update is not None:
                self._statement(node.update, module_name, child_env, child, owner_type)
            for statement in node.body:
                self._statement(statement, module_name, child_env, child, owner_type)
            return
        if isinstance(node, Try):
            child = self._child_scope(node.span, scope)
            child_env = dict(env)
            for statement in node.body:
                self._statement(statement, module_name, child_env, child, owner_type)
            for catch in node.catches:
                catch_scope = self._child_scope(catch.span or node.span, scope)
                catch_env = dict(env)
                self._type_reference(catch.error_type, catch_env)
                if catch.name is not None:
                    symbol = self._declare_catch(catch, module_name, catch_scope)
                    catch_env[catch.name] = symbol.id
                for statement in catch.body:
                    self._statement(statement, module_name, catch_env, catch_scope, owner_type)

    def _function(
        self,
        node: FunctionDeclaration,
        module_name: str,
        env: dict[str, str],
        owner_id: str | None,
    ) -> None:
        self._type_reference(node.return_type, env)
        for error in node.raises:
            self._type_reference(error, env)
        scope = self._child_scope(node.span, None)
        function_env = dict(env)
        parameters = []
        if node.self_parameter is not None:
            parameters.append(node.self_parameter)
        parameters.extend(node.parameters)
        for parameter in parameters:
            self._type_reference(parameter.type, function_env)
            symbol = self._declare_local(parameter, module_name, scope, 'parameter')
            if parameter.name == 'self' and owner_id is not None:
                owner = self.model.symbols.get(owner_id)
                if owner is not None:
                    symbol = SemanticSymbol(
                        **{
                            **symbol.__dict__,
                            'resolved_type': owner.resolved_type,
                        }
                    )
                    self.model.symbols[symbol.id] = symbol
            function_env[parameter.name] = symbol.id
        for statement in node.body:
            self._statement(statement, module_name, function_env, scope, owner_id)

    def _declare_local(
        self, node: VariableDeclaration, module_name: str,
        scope: SemanticScopeRecord, kind: str,
    ) -> SemanticSymbol:
        selection = self._declaration_selection(node, node.name)
        symbol_id = (
            f'local::{node.span.source_path}::{selection.start_offset}::{kind}::{node.name}'
        )
        symbol = SemanticSymbol(
            symbol_id, node.name, kind, _type_label(node.type),
            f'{kind} {_type_label(node.type)} {node.name}', node.span, selection,
            module_name, declaration_offset=selection.start_offset,
            resolved_type=node.type.name,
        )
        self._add_symbol(symbol)
        if node.type.name == 'type':
            self.type_parameters.add(node.name)
        scope.symbols.append(symbol_id)
        return symbol

    def _declare_catch(
        self, node: CatchClause, module_name: str, scope: SemanticScopeRecord
    ) -> SemanticSymbol:
        assert node.name is not None
        source_span = node.span or scope.span
        selection = self._identifier_span(source_span, node.name) or source_span
        symbol_id = f'local::{source_span.source_path}::{selection.start_offset}::catch::{node.name}'
        symbol = SemanticSymbol(
            symbol_id, node.name, 'catch', _type_label(node.error_type),
            f'catch {_type_label(node.error_type)} {node.name}', source_span,
            selection, module_name, declaration_offset=selection.start_offset,
            resolved_type=node.error_type.name,
        )
        self._add_symbol(symbol)
        scope.symbols.append(symbol_id)
        return symbol

    def _child_scope(
        self, span: SourceSpan | None, parent: SemanticScopeRecord | None
    ) -> SemanticScopeRecord:
        assert span is not None and span.source_path is not None
        scope = SemanticScopeRecord(Path(span.source_path), span, parent)
        self.model.scopes.append(scope)
        return scope

    def _expression(
        self, node: Expression, env: dict[str, str]
    ) -> TypeReference | None:
        if isinstance(node, LiteralExpression):
            return TypeReference(node.type)
        if isinstance(node, VariableExpression):
            return self._name_occurrences(node.name, node.span, env)
        if isinstance(node, FunctionCall):
            return self._call(node, env)
        if isinstance(node, CompositeExpression):
            left = self._expression(node.left, env)
            self._expression(node.right, env)
            return TypeReference('bool') if node.operator in {'==', '!=', '<', '<=', '>', '>='} else left
        if isinstance(node, BorrowExpression):
            value = self._expression(node.expr, env)
            if value is not None:
                value.borrow = node.mode
            return value
        if isinstance(node, MoveExpression):
            return self._expression(node.expr, env)
        if isinstance(node, IndexExpression):
            value = self._expression(node.target, env)
            self._expression(node.index, env)
            if value is not None:
                return TypeReference(value.name, list(value.arguments))
            return None
        if isinstance(node, SliceExpression):
            value = self._expression(node.target, env)
            if node.start is not None:
                self._expression(node.start, env)
            if node.end is not None:
                self._expression(node.end, env)
            if value is not None:
                return TypeReference(value.name, list(value.arguments), is_slice=True)
            return None
        if isinstance(node, FormattedStringExpression):
            for part in node.parts:
                if isinstance(part, Expression):
                    self._expression(part, env)
            return TypeReference('str')
        if isinstance(node, StructLiteralExpression):
            self._type_reference(node.type_ref)
            owner_id = self._type_symbol_id(node.type_ref.name)
            for field in node.fields:
                if owner_id is not None:
                    member = self._member(owner_id, field.name, 'field')
                    if member is not None:
                        span = self._identifier_span(field.span, field.name)
                        if span is not None:
                            self.model.occurrences.append(SemanticOccurrence(member.id, span))
                self._expression(field.expr, env)
            return node.type_ref
        if isinstance(node, TypeExpression):
            self._type_reference(node.type_ref)
            return TypeReference('type')
        return None

    def _call(self, node: FunctionCall, env: dict[str, str]) -> TypeReference | None:
        for argument in node.parameters:
            self._expression(argument, env)
        name = node.function_name
        if name in BUILTIN_TYPES:
            return TypeReference(name)
        parts = name.split('.')
        direct = self.top_by_internal.get(parts[0])
        symbol = self.model.symbols.get(direct or '')
        if symbol is not None and symbol.kind == 'function':
            self._occurrence_for_symbol(node.span, symbol, prefer_last=len(parts) > 1)
            self._check_arity(node, symbol)
            declaration = self.functions.get(parts[0])
            if declaration is not None:
                self._check_arguments(node, declaration.parameters, env)
            return declaration.return_type if declaration is not None else None
        receiver_type = self._name_occurrences('.'.join(parts[:-1]), node.span, env) if len(parts) > 1 else None
        owner_id = self._type_symbol_id(receiver_type.name) if receiver_type is not None else None
        if owner_id is not None:
            member = self._member(owner_id, parts[-1], 'method')
            if member is not None:
                self._occurrence_for_symbol(node.span, member, prefer_last=True)
                self._check_arity(node, member)
                owner = self.type_declarations.get(receiver_type.name)
                if isinstance(owner, TypeDeclaration):
                    declaration = next((method for method in owner.methods if method.name == parts[-1]), None)
                    if declaration is not None:
                        self._check_arguments(node, declaration.parameters, env)
                    return declaration.return_type if declaration is not None else None
        if (
            node.span is not None and name not in BUILTIN_TYPES
            and name not in BUILTIN_FUNCTIONS and len(parts) == 1
        ):
            self.diagnostics.append(AnalysisDiagnostic(
                f'Unknown function "{parts[-1]}".',
                self._identifier_span(node.span, parts[-1], last=True) or node.span,
                code='unknown-function',
            ))
        return None

    def _name_occurrences(
        self, name: str, span: SourceSpan | None, env: dict[str, str]
    ) -> TypeReference | None:
        if span is None:
            return None
        parts = name.split('.')
        symbol_id = env.get(parts[0]) or self.top_by_internal.get(parts[0])
        symbol = self.model.symbols.get(symbol_id or '')
        if symbol is None:
            self.diagnostics.append(AnalysisDiagnostic(
                f'Unknown name "{parts[0]}".',
                self._identifier_span(span, parts[0]) or span,
                code='unknown-name',
            ))
            return None
        self._occurrence_for_symbol(span, symbol, prefer_last=parts[0] not in env)
        current_type = (
            TypeReference(symbol.resolved_type)
            if symbol.resolved_type is not None
            else _parse_type_label(symbol.type_label)
        )
        for member_name in parts[1:]:
            owner_id = self._type_symbol_id(current_type.name) if current_type is not None else None
            member = self._member(owner_id, member_name) if owner_id is not None else None
            if member is None:
                member_span = self._identifier_span(span, member_name, last=True) or span
                self.diagnostics.append(AnalysisDiagnostic(
                    f'Unknown member "{member_name}".', member_span,
                    code='unknown-member',
                ))
                break
            self._occurrence_for_symbol(span, member, prefer_last=True)
            current_type = (
                TypeReference(member.resolved_type)
                if member.resolved_type is not None
                else _parse_type_label(member.type_label)
            )
        return current_type

    def _check_arity(self, node: FunctionCall, symbol: SemanticSymbol) -> None:
        expected = len(symbol.parameter_labels)
        actual = len(node.parameters)
        if expected != actual and node.span is not None:
            self.diagnostics.append(AnalysisDiagnostic(
                f'Function "{symbol.name}" expects {expected} arguments, got {actual}.',
                self._identifier_span(node.span, symbol.name, last=True) or node.span,
                code='argument-count',
            ))

    def _check_arguments(
        self, node: FunctionCall, parameters: list[VariableDeclaration],
        env: dict[str, str],
    ) -> None:
        for argument, parameter in zip(node.parameters, parameters):
            actual = self._expression(argument, env)
            self._check_type(
                parameter.type, actual, argument.span, 'argument', argument
            )

    def _check_type(
        self, expected: TypeReference | None, actual: TypeReference | None,
        span: SourceSpan | None, context: str,
        expression: Expression | None = None,
    ) -> None:
        if expected is None or actual is None or span is None:
            return
        if expected.name in self.type_parameters or actual.name in self.type_parameters:
            return
        if expected.name == actual.name and (
            expected.borrow is None or expected.borrow == actual.borrow
        ):
            return
        if expected.borrow is None and actual.borrow is None:
            expected_name = expected.name.split('$')[-1]
            actual_name = actual.name.split('$')[-1]
            if expected_name in BUILTIN_TYPE_SPECS and actual_name in BUILTIN_TYPE_SPECS:
                if builtin_conversion_allowed(actual_name, expected_name):
                    if isinstance(expression, LiteralExpression):
                        try:
                            cast_builtin_value(
                                expression.value,
                                expected_name,
                                source_type=expression.type,
                            )
                        except (TypeError, ValueError, OverflowError):
                            pass
                        else:
                            return
                    else:
                        return
        if expected.borrow is not None and not isinstance(expression, BorrowExpression):
            return
        self.diagnostics.append(AnalysisDiagnostic(
            f'Incompatible {context}: expected {_type_label(expected)}, got {_type_label(actual)}.',
            span, code='type-mismatch',
        ))

    def _type_reference(
        self, type_ref: TypeReference | None, env: Mapping[str, str] | None = None
    ) -> None:
        if type_ref is None:
            return
        symbol_id = (env or {}).get(type_ref.name) or self.top_by_internal.get(type_ref.name)
        symbol = self.model.symbols.get(symbol_id or '')
        if symbol is not None and (
            symbol.kind in {'type', 'view', 'interface'} or symbol.resolved_type == 'type'
        ):
            self._occurrence_for_symbol(type_ref.span, symbol, prefer_last=True)
        elif (
            type_ref.name not in BUILTIN_TYPES
            and type_ref.name not in self.type_parameters
            and type_ref.name != 'self'
            and type_ref.span is not None
        ):
            self.diagnostics.append(AnalysisDiagnostic(
                f'Unknown type "{type_ref.name.split("$")[-1]}".',
                type_ref.span,
                code='unknown-type',
            ))
        for argument in type_ref.arguments:
            if isinstance(argument, TypeReference):
                self._type_reference(argument, env)

    def _type_symbol_id(self, name: str) -> str | None:
        symbol_id = self.top_by_internal.get(name)
        symbol = self.model.symbols.get(symbol_id or '')
        return symbol_id if symbol is not None and symbol.kind in {'type', 'view', 'interface'} else None

    def _member(
        self, owner_id: str, name: str, kind: str | None = None
    ) -> SemanticSymbol | None:
        for symbol_id in self.model.members.get(owner_id, []):
            symbol = self.model.symbols[symbol_id]
            if symbol.name == name and (kind is None or symbol.kind == kind):
                return symbol
        return None

    def _occurrence_for_symbol(
        self, span: SourceSpan | None, symbol: SemanticSymbol, *, prefer_last: bool
    ) -> None:
        if span is None:
            return
        found = self._identifier_span(span, symbol.name, last=prefer_last)
        if found is not None:
            self.model.occurrences.append(SemanticOccurrence(symbol.id, found))

    def _import_occurrences(
        self, module_name: str, statements: Iterable[Statement]
    ) -> None:
        imports = [node for node in statements if isinstance(node, ImportDeclaration)]
        dependencies = self.graph.dependencies.get(module_name, ())
        for index, node in enumerate(imports):
            if node.symbols is None:
                continue
            effective = dependencies[index] if index < len(dependencies) else node.module_name
            for name in node.symbols:
                target_id = next((
                    symbol.id for symbol in self.model.symbols.values()
                    if symbol.module_name == effective and symbol.name == name
                    and symbol.container_id is None
                ), None)
                if target_id is None:
                    continue
                span = self._identifier_span(node.span, name, last=True)
                if span is not None:
                    self.model.occurrences.append(SemanticOccurrence(
                        target_id, span, role='import'
                    ))

    def _declaration_selection(self, node, name: str) -> SourceSpan:
        span = self._identifier_span(node.span, name)
        return span or node.span

    def _identifier_span(
        self, span: SourceSpan | None, name: str, *, last: bool = False
    ) -> SourceSpan | None:
        if span is None or span.source_path is None:
            return None
        tokens = self.tokens.get(Path(span.source_path))
        if tokens is None:
            return None
        candidates = [
            token.span for token in tokens
            if token.kind == 'IDENT' and token.value == name
            and span.start_offset <= token.span.start_offset
            and token.span.end_offset <= span.end_offset
        ]
        if not candidates:
            return None
        return candidates[-1] if last else candidates[0]


def _source_span(path: Path, source: str) -> SourceSpan:
    lines = source.splitlines() or ['']
    return SourceSpan(
        1, 1, len(lines), len(lines[-1]) + 1, 0, len(source), str(path.resolve())
    )


def _declaration_kind(node: Statement) -> str:
    if isinstance(node, TypeDeclaration):
        return 'type'
    if isinstance(node, ViewDeclaration):
        return 'view'
    if isinstance(node, InterfaceDeclaration):
        return 'interface'
    if isinstance(node, FunctionDeclaration):
        return 'function'
    return 'global'


def _declaration_type_label(node: Statement) -> str | None:
    if isinstance(node, FunctionDeclaration):
        return _type_label(node.return_type)
    if isinstance(node, VariableDeclaration):
        return _type_label(node.type)
    if isinstance(node, (TypeDeclaration, ViewDeclaration, InterfaceDeclaration)):
        return node.source_name or node.name
    return None


def _declaration_signature(node: Statement, name: str) -> str:
    if isinstance(node, TypeDeclaration):
        return f'{'pub ' if node.public else ''}struct {name}'
    if isinstance(node, ViewDeclaration):
        return f'{'pub ' if node.public else ''}view {name}'
    if isinstance(node, InterfaceDeclaration):
        return f'{'pub ' if node.public else ''}interface {name}'
    if isinstance(node, FunctionDeclaration):
        return _function_signature(node, name)
    if isinstance(node, VariableDeclaration):
        return f'{_type_label(node.type)} {name}'
    return name


def _function_signature(node: FunctionDeclaration, name: str) -> str:
    parameters = [*([node.self_parameter] if node.self_parameter is not None else []), *node.parameters]
    values = ', '.join(
        f'{'comptime ' if parameter.comptime else ''}'
        f'{'move ' if parameter.passing_mode == "move" else ""}'
        f'{_type_label(parameter.type)} {parameter.name}'
        f'{_constraint_label(parameter)}'
        for parameter in parameters
    )
    raises = ''
    if node.raises:
        raises = ' raises ' + ', '.join(_type_label(error) for error in node.raises)
    prefix = 'pub ' if node.public else ''
    return f'{prefix}{_type_label(node.return_type)} {name}({values}){raises}'


def _parameter_labels(
    node: Statement, *, omit_self: bool = False
) -> tuple[str, ...]:
    if isinstance(node, FunctionDeclaration):
        parameters = [
            *([] if omit_self or node.self_parameter is None else [node.self_parameter]),
            *node.parameters,
        ]
    elif isinstance(node, TypeDeclaration):
        parameters = node.parameters
    else:
        return ()
    return tuple(
        f'{'comptime ' if item.comptime else ''}'
        f'{'move ' if item.passing_mode == "move" else ""}'
        f'{_type_label(item.type)} {item.name}'
        f'{_constraint_label(item)}'
        for item in parameters
    )


def _constraint_label(parameter: VariableDeclaration) -> str:
    if not parameter.constraints:
        return ''
    return ': ' + ' + '.join(_type_label(item) or item.name for item in parameter.constraints)


def _type_label(type_ref: TypeReference | None) -> str | None:
    if type_ref is None:
        return None
    name = type_ref.name.split('$')[-1]
    if type_ref.arguments:
        name += '(' + ', '.join(
            _type_label(value) if isinstance(value, TypeReference) else '<expr>'
            for value in type_ref.arguments
        ) + ')'
    if type_ref.array_size is not None:
        name += '[...]'
    elif type_ref.is_slice:
        name += '[]'
    if type_ref.borrow is not None:
        name = f'&{type_ref.borrow} {name}'
    return name


def _parse_type_label(label: str | None) -> TypeReference | None:
    if not label:
        return None
    value = label
    borrow = None
    if value.startswith('&'):
        mode, _, value = value.partition(' ')
        borrow = mode[1:]
    value = value.split('(', 1)[0].split('[', 1)[0]
    return TypeReference(value, borrow=borrow)


def _deduplicate_diagnostics(
    diagnostics: Iterable[AnalysisDiagnostic],
) -> list[AnalysisDiagnostic]:
    result = []
    seen = set()
    for diagnostic in diagnostics:
        span = diagnostic.span
        key = (
            diagnostic.message,
            getattr(span, 'source_path', None),
            getattr(span, 'start_offset', None),
        )
        if key not in seen:
            seen.add(key)
            result.append(diagnostic)
    return result


def _cap_diagnostics(
    diagnostics: list[AnalysisDiagnostic], limit: int = 50
) -> list[AnalysisDiagnostic]:
    grouped: dict[str | None, list[AnalysisDiagnostic]] = {}
    for item in diagnostics:
        path = item.span.source_path if item.span is not None else None
        grouped.setdefault(path, []).append(item)
    result = []
    for path in sorted(grouped, key=lambda value: value or ''):
        values = grouped[path]
        result.extend(values[:limit])
        if len(values) > limit:
            span = values[limit - 1].span
            result.append(AnalysisDiagnostic(
                f'Too many semantic errors; stopped after {limit} diagnostics.',
                span, code='too-many-errors',
            ))
    return result


def offset_at_position(source: str, line: int, character: int) -> int:
    lines = source.splitlines(keepends=True)
    if line < 0:
        return 0
    if line >= len(lines):
        return len(source)
    return sum(len(value) for value in lines[:line]) + min(character, len(lines[line]))


def identifier_prefix(source: str, offset: int) -> tuple[str, int]:
    start = offset
    while start > 0 and (source[start - 1].isalnum() or source[start - 1] == '_'):
        start -= 1
    return source[start:offset], start


def valid_identifier(value: str) -> bool:
    return bool(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value)) and value not in JACK_KEYWORDS
