import copy
from dataclasses import dataclass
from typing import Callable, Iterable

try:
    from .borrow_modes import borrow_mode_can_write, borrow_mode_compatible
    from .builtin_types import (
        BUILTIN_TYPE_SPECS,
        cast_builtin_value,
        default_builtin_value,
        format_builtin_value,
        is_bool_type,
        is_builtin_type,
        is_numeric_type,
        is_raw_byte_type,
    )
    from .ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        DereferenceExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        IfBranch,
        ImplementationDeclaration,
        InterfaceDeclaration,
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
        StructLiteralField,
        Statement,
        Try,
        UnsafeBlock,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
    from .execution import ExecutionEngine
except ImportError:
    from borrow_modes import borrow_mode_can_write, borrow_mode_compatible
    from builtin_types import (
        BUILTIN_TYPE_SPECS,
        cast_builtin_value,
        default_builtin_value,
        format_builtin_value,
        is_bool_type,
        is_builtin_type,
        is_numeric_type,
        is_raw_byte_type,
    )
    from ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        DereferenceExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        IfBranch,
        ImplementationDeclaration,
        InterfaceDeclaration,
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
        StructLiteralField,
        Statement,
        Try,
        UnsafeBlock,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
    from execution import ExecutionEngine


class CompileTimeError(Exception):
    def __init__(self, message: str, span: SourceSpan | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.span = span


class CompileTimeFeatureNotImplemented(CompileTimeError):
    pass


class ComptimeRaisedError(Exception):
    def __init__(self, type_name: str, payload: LiteralExpression) -> None:
        super().__init__(type_name)
        self.type_name = type_name
        self.payload = payload


@dataclass(frozen=True)
class TypeLayout:
    size: int
    align: int


@dataclass
class ComptimeStructValue:
    type_ref: TypeReference
    fields: dict[str, LiteralExpression]


@dataclass
class ComptimeOpaqueValue:
    type_ref: TypeReference
    value: object = None

    def __deepcopy__(self, memo):
        return ComptimeOpaqueValue(copy.deepcopy(self.type_ref, memo), self.value)


@dataclass
class ComptimeArrayValue:
    element_type: TypeReference
    elements: list[LiteralExpression]


@dataclass
class ComptimeBorrowValue:
    type_ref: TypeReference
    mutable: bool
    cell: LiteralExpression | None = None
    array: ComptimeArrayValue | None = None
    start: int = 0
    length: int | None = None

    def __deepcopy__(self, memo):
        return ComptimeBorrowValue(
            copy.deepcopy(self.type_ref, memo),
            self.mutable,
            self.cell,
            self.array,
            self.start,
            self.length,
        )

    def window_length(self) -> int:
        if self.array is not None:
            available = len(self.array.elements) - self.start
            return available if self.length is None else self.length
        return 1

    def element_cell(self, index: int) -> LiteralExpression:
        if index < 0 or index >= self.window_length():
            raise CompileTimeError(
                f'Comptime borrow index {index} is out of bounds for length {self.window_length()}.'
            )
        if self.array is not None:
            return self.array.elements[self.start + index]
        if self.cell is None:
            raise CompileTimeError('Comptime borrow has no target.')
        if index != 0:
            raise CompileTimeError('Cannot index past a scalar comptime borrow.')
        return self.cell

    def as_type(self, type_ref: TypeReference) -> 'ComptimeBorrowValue':
        return ComptimeBorrowValue(
            copy.deepcopy(type_ref),
            self.mutable and borrow_mode_can_write(type_ref.borrow),
            self.cell,
            self.array,
            self.start,
            self.length,
        )


class CompileTimeScope:
    def __init__(self, parent: "CompileTimeScope | None" = None) -> None:
        self.parent = parent
        self.variables: dict[str, LiteralExpression] = {}

    def declare(self, name: str, value: LiteralExpression) -> None:
        if "." in name:
            raise CompileTimeError(f'Cannot declare dotted comptime name "{name}".')
        if name in self.variables:
            raise CompileTimeError(f'Comptime variable "{name}" is already declared in this scope.')
        self.variables[name] = value

    def assign(self, name: str, value: LiteralExpression) -> bool:
        parts = name.split('.')
        scope = self._scope_containing(parts[0])
        if scope is None:
            return False

        if len(parts) == 1:
            current = scope.variables[parts[0]]
            current.value = value.value
            current.type = value.type
            return True

        current = scope.variables[parts[0]]
        current_path = parts[0]
        for field_name in parts[1:-1]:
            current = self._field_value(current, field_name, current_path)
            current_path = f'{current_path}.{field_name}'

        struct_value = self._struct_value(current, current_path)
        field_name = parts[-1]
        if field_name not in struct_value.fields:
            raise CompileTimeError(
                f'Comptime value "{current_path}" has no field "{field_name}".'
            )
        current_field = struct_value.fields[field_name]
        current_field.value = value.value
        current_field.type = value.type
        return True

    def get(self, name: str) -> LiteralExpression | None:
        parts = name.split('.')
        scope = self._scope_containing(parts[0])
        if scope is None:
            return None

        current = scope.variables[parts[0]]
        current_path = parts[0]
        for field_name in parts[1:]:
            current = self._field_value(current, field_name, current_path)
            current_path = f'{current_path}.{field_name}'

        return current

    def _field_value(
        self, value: LiteralExpression, field_name: str, current_path: str
    ) -> LiteralExpression:
        struct_value = self._struct_value(value, current_path)
        if field_name not in struct_value.fields:
            raise CompileTimeError(
                f'Comptime value "{current_path}" has no field "{field_name}".'
            )
        return struct_value.fields[field_name]

    def _struct_value(self, value: LiteralExpression, current_path: str) -> ComptimeStructValue:
        if type(value.value) is not ComptimeStructValue:
            raise CompileTimeError(f'Comptime value "{current_path}" is not a struct value.')
        return value.value

    def contains(self, name: str) -> bool:
        return self.get(name) is not None

    def contains_root(self, name: str) -> bool:
        root = name.split('.', 1)[0]
        return self._scope_containing(root) is not None

    def _scope_containing(self, name: str) -> "CompileTimeScope | None":
        if name in self.variables:
            return self
        if self.parent is not None:
            return self.parent._scope_containing(name)
        return None


ComptimePrintHandler = Callable[[str], None]
ComptimeExternHandler = Callable[..., object]


def apply_compile_time_pass(
    ast: list[Statement],
    print_handler: ComptimePrintHandler | None = print,
    externs: dict[str, ComptimeExternHandler] | None = None,
) -> list[Statement]:
    return CompileTimePass(print_handler, externs=externs).apply(ast)


class CompileTimePass:
    LOOP_ITERATION_LIMIT = 10000
    TYPE_LAYOUT_QUERY_FUNCTIONS = {'sizeof', 'alignof'}

    def __init__(
        self,
        print_handler: ComptimePrintHandler | None = print,
        externs: dict[str, ComptimeExternHandler] | None = None,
    ) -> None:
        self.print_handler = print_handler
        self.comptime_externs = externs or {}
        self.executor = CompileTimeExecutor(self)
        self.functions: dict[str, FunctionDeclaration] = {}
        self.types: dict[str, TypeDeclaration] = {}
        self.interfaces: dict[str, InterfaceDeclaration] = {}
        self.implementations: list[ImplementationDeclaration] = []
        self.variant_names: dict[tuple[str, tuple[tuple[str, object, str], ...]], str] = {}
        self.type_variant_names: dict[tuple[str, tuple[tuple[str, object, str], ...]], str] = {}
        self.synthetic_counter = 0
        self.generated_variants: list[FunctionDeclaration] = []
        self.generated_types: list[TypeDeclaration] = []
        self.active_interface_dispatch: dict[str, set[str]] = {}

    def apply(self, ast: list[Statement]) -> list[Statement]:
        self._register_declarations(ast)
        self._validate_interface_contracts()
        self._validate_generic_copy_contracts(ast)
        lowered = self._apply_statements(ast, CompileTimeScope())
        lowered_types = [node for node in lowered if type(node) is TypeDeclaration]
        lowered_rest = [node for node in lowered if type(node) is not TypeDeclaration]
        runtime_ast = [
            *lowered_types,
            *self.generated_types,
            *self.generated_variants,
            *lowered_rest,
        ]
        self._infer_all_raises(runtime_ast)
        return runtime_ast

    def _register_declarations(self, ast: Iterable[Statement]) -> None:
        nodes = list(ast)
        for node in nodes:
            if type(node) is FunctionDeclaration:
                self.functions[node.name] = node
            elif type(node) is TypeDeclaration:
                self.types[node.name] = node
            elif type(node) is InterfaceDeclaration:
                self.interfaces[node.name] = node
            elif type(node) is ImplementationDeclaration:
                self.implementations.append(node)
        for implementation in self.implementations:
            declaration = self.types.get(implementation.type_name)
            if declaration is None or declaration.parameters or implementation.parameters:
                continue
            for method in implementation.methods:
                concrete = copy.deepcopy(method)
                concrete.interface_name = implementation.interface.name
                concrete.source_name = method.source_name or method.name
                concrete.name = f'{implementation.interface.name}${method.name}'
                self._mark_implementation_calls(
                    concrete, implementation.interface.name
                )
                self._replace_self_in_function(concrete, declaration.name)
                declaration.methods.append(concrete)

    def _replace_self_in_function(
        self, declaration: FunctionDeclaration, self_name: str
    ) -> None:
        refs = [declaration.return_type, *declaration.raises]
        if declaration.self_parameter is not None:
            refs.append(declaration.self_parameter.type)
        refs.extend(parameter.type for parameter in declaration.parameters)
        for type_ref in refs:
            self._replace_self_type(type_ref, self_name)

    def _replace_self_type(self, type_ref: TypeReference, self_name: str) -> None:
        if type_ref.name in {'Self', 'self'}:
            type_ref.name = self_name
        for argument in type_ref.arguments:
            if type(argument) is TypeReference:
                self._replace_self_type(argument, self_name)

    def _mark_implementation_calls(
        self, declaration: FunctionDeclaration, interface_name: str
    ) -> None:
        interface = self.interfaces.get(interface_name)
        method_names = (
            {method.name for method in interface.methods}
            if interface is not None else {'init'} if interface_name == 'Copyable' else set()
        )
        seen: set[int] = set()

        def visit(value: object) -> None:
            if value is None or isinstance(value, (str, int, float, bool, SourceSpan)):
                return
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            if type(value) is FunctionCall and '.' in value.function_name:
                if value.function_name.rsplit('.', 1)[1] in method_names:
                    value.interface_name = interface_name
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
            elif hasattr(value, '__dict__'):
                for item in vars(value).values():
                    visit(item)

        visit(declaration.body)

    def _validate_generic_copy_contracts(self, ast: Iterable[Statement]) -> None:
        for node in ast:
            functions: list[
                tuple[FunctionDeclaration, dict[str, VariableDeclaration]]
            ] = []
            if type(node) is FunctionDeclaration:
                functions.append((node, {}))
            elif type(node) is TypeDeclaration:
                inherited = {
                    parameter.name: parameter
                    for parameter in node.parameters
                    if parameter.comptime and parameter.type.name == 'type'
                }
                functions.extend((method, inherited) for method in node.methods)
            elif type(node) is ImplementationDeclaration:
                inherited = {
                    parameter.name: parameter
                    for parameter in node.parameters
                    if parameter.comptime and parameter.type.name == 'type'
                }
                functions.extend((method, inherited) for method in node.methods)
            for declaration, inherited in functions:
                type_parameters = {
                    **inherited,
                    **{
                    parameter.name: parameter
                    for parameter in declaration.parameters
                    if parameter.comptime and parameter.type.name == 'type'
                    },
                }
                for parameter in declaration.parameters:
                    generic = type_parameters.get(parameter.type.name)
                    if (
                        generic is not None
                        and not parameter.comptime
                        and parameter.type.borrow is None
                        and parameter.passing_mode == 'copy'
                        and not self._parameter_has_constraint(generic, 'Copyable')
                    ):
                        raise CompileTimeError(
                            f'Generic parameter "{parameter.name}" copies type '
                            f'"{generic.name}"; declare "{generic.name}: Copyable".',
                            parameter.span,
                        )

    def _parameter_has_constraint(
        self, parameter: VariableDeclaration, interface_name: str
    ) -> bool:
        return any(
            constraint.name == interface_name
            or constraint.name.rsplit('$', 1)[-1] == interface_name
            for constraint in parameter.constraints
        )

    def _validate_interface_contracts(self) -> None:
        seen: set[tuple[str, str]] = set()
        for implementation in self.implementations:
            type_decl = self.types.get(implementation.type_name)
            interface = self.interfaces.get(implementation.interface.name)
            if type_decl is None:
                raise CompileTimeError(
                    f'Unknown implementation type "{implementation.type_name}".',
                    implementation.span,
                )
            if interface is None and implementation.interface.name != 'Copyable':
                raise CompileTimeError(
                    f'Unknown interface "{implementation.interface.name}".',
                    implementation.span,
                )
            key = (implementation.type_name, implementation.interface.name)
            if key in seen:
                raise CompileTimeError(
                    f'Duplicate implementation of "{implementation.interface.name}" '
                    f'for "{implementation.type_name}".',
                    implementation.span,
                )
            seen.add(key)
            interface_module = interface.module_name if interface is not None else None
            if implementation.module_name not in {type_decl.module_name, interface_module}:
                raise CompileTimeError(
                    'An implementation must be declared in the module owning its type or interface.',
                    implementation.span,
                )
            requirements = (
                {method.name: method for method in interface.methods}
                if interface is not None else {'init': self._copyable_requirement()}
            )
            entries: dict[str, FunctionDeclaration | None] = {}
            for use in implementation.uses:
                if use.name in entries:
                    raise CompileTimeError(f'Duplicate implementation entry "{use.name}".', use.span)
                entries[use.name] = None
            for method in implementation.methods:
                if method.name in entries:
                    raise CompileTimeError(f'Duplicate implementation entry "{method.name}".', method.span)
                entries[method.name] = method
            unknown = sorted(set(entries) - set(requirements))
            missing = sorted(set(requirements) - set(entries))
            if unknown:
                raise CompileTimeError(
                    f'Interface "{implementation.interface.name}" has no method "{unknown[0]}".',
                    implementation.span,
                )
            if missing:
                raise CompileTimeError(
                    f'Implementation of "{implementation.interface.name}" is missing method "{missing[0]}".',
                    implementation.span,
                )
            inherent = {method.name: method for method in type_decl.methods}
            for name, supplied in entries.items():
                candidate = supplied or inherent.get(name)
                if candidate is None:
                    raise CompileTimeError(
                        f'No visible inherent method "{name}" exists for use.', implementation.span
                    )
                if self._interface_signature_key(requirements[name]) != self._interface_signature_key(candidate):
                    raise CompileTimeError(
                        f'Method "{name}" does not match interface '
                        f'"{implementation.interface.name}".', candidate.span,
                    )

    def _copyable_requirement(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            'init', [VariableDeclaration('other', TypeReference('Self', borrow='in'))],
            [], TypeReference('void'),
            self_parameter=VariableDeclaration('self', TypeReference('Self', borrow='out')),
        )

    def _interface_signature_key(self, method: FunctionDeclaration) -> tuple[object, ...]:
        parameters = [method.self_parameter, *method.parameters]
        return (
            self._constraint_type_key(method.return_type),
            tuple(
                (
                    parameter.comptime,
                    parameter.passing_mode,
                    self._constraint_type_key(parameter.type),
                )
                for parameter in parameters if parameter is not None
            ),
            tuple(self._constraint_type_key(error) for error in method.raises),
            method.raises_inferred,
            method.unsafe,
        )

    def _constraint_type_key(self, type_ref: TypeReference) -> str:
        name = 'Self' if type_ref.name in {'self', 'Self'} else type_ref.name
        if type_ref.arguments:
            name += '(' + ','.join(
                self._constraint_type_key(argument)
                if type(argument) is TypeReference else repr(argument)
                for argument in type_ref.arguments
            ) + ')'
        if type_ref.array_size is not None:
            size = (
                type_ref.array_size.value
                if type(type_ref.array_size) is LiteralExpression
                else type_ref.array_size
            )
            name += f'[{size}]'
        elif type_ref.is_slice:
            name += '[]'
        if type_ref.borrow is not None:
            name = f'&{type_ref.borrow} {name}'
        return name

    def _apply_statements(
        self, ast: Iterable[Statement], scope: CompileTimeScope
    ) -> list[Statement]:
        lowered: list[Statement] = []

        for node in ast:
            try:
                statements = self._apply_statement(node, scope)
            except CompileTimeError as err:
                self._attach_error_span(err, node)
                raise
            self._inherit_statement_metadata(node, statements)
            lowered.extend(statements)

        return lowered

    def _attach_error_span(self, err: CompileTimeError, node: object) -> None:
        if err.span is None:
            err.span = getattr(node, 'span', None)

    def _inherit_statement_metadata(
        self, source: Statement, statements: Iterable[Statement]
    ) -> None:
        for statement in statements:
            if getattr(statement, 'span', None) is None:
                statement.span = getattr(source, 'span', None)
            if getattr(statement, 'module_name', None) is None:
                statement.module_name = getattr(source, 'module_name', None)
            if getattr(statement, 'source_name', None) is None:
                statement.source_name = getattr(source, 'source_name', None)
            if not getattr(statement, 'imports', []):
                statement.imports = list(getattr(source, 'imports', []))
            if not getattr(statement, 'qualified_imports', []):
                statement.qualified_imports = list(getattr(source, 'qualified_imports', []))

    def _apply_statement(self, node: Statement, scope: CompileTimeScope) -> list[Statement]:
        if type(node) in {ModuleDeclaration, ImportDeclaration}:
            return []
        if type(node) is InterfaceDeclaration:
            return [copy.deepcopy(node)]
        if type(node) is ImplementationDeclaration:
            return [] if node.parameters else [copy.deepcopy(node)]
        if type(node) is ViewDeclaration:
            return [copy.deepcopy(node)]
        if type(node) is VariableDeclaration:
            return self._apply_variable_declaration(node, scope)
        if type(node) is Assignment:
            return self._apply_assignment(node, scope)
        if type(node) is If:
            return self._apply_if(node, scope)
        if type(node) is While:
            return self._apply_while(node, scope)
        if type(node) is For:
            return self._apply_for(node, scope)
        if type(node) is Try:
            return self._apply_try(node, scope)
        if type(node) is UnsafeBlock:
            if node.comptime:
                raise CompileTimeError('unsafe blocks cannot be comptime.')
            return [UnsafeBlock(self._apply_statements(node.body, CompileTimeScope(scope)))]
        if type(node) is Print:
            return self._apply_print_statement(node, scope)
        if type(node) is FunctionDeclaration:
            return self._apply_function_declaration(node, scope)
        if type(node) is FunctionCall:
            if node.comptime:
                self._eval_comptime_method_call(node, scope)
                return []
            prelude, call = self._apply_function_call(node, scope)
            return [*prelude, call]
        if type(node) is Raise:
            if node.comptime:
                returned = self._eval_comptime_statement(node, scope)
                if returned is not None:
                    raise CompileTimeError('Return is not allowed in a comptime raise statement.')
                return []
            prelude, expr = self._apply_expression(node.expr, scope)
            self._reject_runtime_formatted_string_expression(expr, 'raise payload')
            return [*prelude, Raise(expr)]
        if type(node) is Rethrow:
            if node.comptime:
                returned = self._eval_comptime_statement(node, scope)
                if returned is not None:
                    raise CompileTimeError('Return is not allowed in a comptime rethrow statement.')
                return []
            return [copy.deepcopy(node)]
        if node.comptime:
            raise CompileTimeFeatureNotImplemented(
                f'Comptime {type(node).__name__} statements are not implemented yet.'
            )
        if type(node) is TypeDeclaration:
            return self._apply_type_declaration(node, scope)
        if type(node) is Return:
            if node.expr is None:
                return [Return()]
            prelude, expr = self._apply_expression(node.expr, scope)
            self._reject_runtime_formatted_string_expression(expr, 'return value')
            return [*prelude, Return(expr)]

        raise CompileTimeError(f'Unknown statement type "{type(node).__name__}".')

    def _apply_variable_declaration(
        self, declaration: VariableDeclaration, scope: CompileTimeScope
    ) -> list[Statement]:
        runtime_type = self._apply_type_reference(declaration.type, scope)
        if declaration.extern:
            if declaration.comptime:
                raise CompileTimeError(f'Extern variable "{declaration.name}" cannot be comptime.')
            if declaration.expr is not None or declaration.constructor_args:
                raise CompileTimeError(
                    f'Extern variable "{declaration.name}" cannot have an initializer or constructor arguments.'
                )
            return [
                VariableDeclaration(
                    declaration.name,
                    runtime_type,
                    public=declaration.public,
                    extern=True,
                    abi=declaration.abi,
                )
            ]
        if self._is_void_type(runtime_type):
            raise CompileTimeError(f'Variable "{declaration.name}" cannot have type "void".')
        if declaration.expr is not None and declaration.constructor_args:
            raise CompileTimeError(
                f'Variable "{declaration.name}" cannot have both an initializer and constructor arguments.'
            )
        if declaration.comptime:
            if declaration.expr is None:
                value = self._default_literal(runtime_type, scope)
            else:
                value = self._eval_comptime_expression(declaration.expr, scope)
                value = LiteralExpression(
                    self._cast_comptime(value.value, runtime_type, source_type=value.type),
                    self._type_name(runtime_type),
                )
            scope.declare(declaration.name, value)
            if declaration.constructor_args:
                self._eval_comptime_method_call(
                    FunctionCall(f'{declaration.name}.init', declaration.constructor_args),
                    scope,
                )
            return []

        constructor_prelude, constructor_args = self._apply_constructor_args(
            declaration.constructor_args, scope
        )
        if declaration.expr is None:
            return [
                *constructor_prelude,
                VariableDeclaration(
                    declaration.name,
                    runtime_type,
                    constructor_args=constructor_args,
                    public=declaration.public,
                ),
            ]

        prelude, expr = self._apply_expression(declaration.expr, scope)
        self._reject_runtime_formatted_string_expression(expr, f'initializer for "{declaration.name}"')
        return [
            *constructor_prelude,
            *prelude,
            VariableDeclaration(
                declaration.name, runtime_type, expr, public=declaration.public
            ),
        ]

    def _apply_assignment(
        self, assignment: Assignment, scope: CompileTimeScope
    ) -> list[Statement]:
        if assignment.comptime:
            returned = self._eval_comptime_statement(assignment, scope)
            if returned is not None:
                raise CompileTimeError('Return is not allowed in a comptime assignment.')
            return []

        if type(assignment.name) is str:
            if scope.contains(assignment.name):
                raise CompileTimeError(
                    f'Assignment to comptime variable "{assignment.name}" must be marked comptime.'
                )

            if scope.contains_root(assignment.name):
                raise CompileTimeError(
                    f'Assignment to comptime value "{assignment.name}" must be marked comptime.'
                )
            target: str | Expression = assignment.name
            target_label = assignment.name
        else:
            target_prelude, target_expr = self._apply_expression(assignment.name, scope)
            if target_prelude:
                raise CompileTimeError('Function specialization inside assignment targets is not implemented yet.')
            target = target_expr
            target_label = self._expression_label(target_expr)

        prelude, expr = self._apply_expression(assignment.expr, scope)
        self._reject_runtime_formatted_string_expression(expr, f'assignment to "{target_label}"')
        return [*prelude, Assignment(target, expr)]

    def _apply_if(self, statement: If, scope: CompileTimeScope) -> list[Statement]:
        if statement.comptime:
            for branch in statement.branches:
                if self._is_truthy(self._eval_comptime_expression(branch.condition, scope)):
                    return self._apply_statements(branch.body, CompileTimeScope(scope))
            if statement.else_body is not None:
                return self._apply_statements(statement.else_body, CompileTimeScope(scope))
            return []

        prelude: list[Statement] = []
        branches: list[IfBranch] = []
        for branch in statement.branches:
            condition_prelude, condition = self._apply_expression(branch.condition, scope)
            self._reject_runtime_formatted_string_expression(condition, 'if condition')
            prelude.extend(condition_prelude)
            branches.append(
                IfBranch(condition, self._apply_statements(branch.body, CompileTimeScope(scope)))
            )

        else_body = None
        if statement.else_body is not None:
            else_body = self._apply_statements(statement.else_body, CompileTimeScope(scope))

        return [*prelude, If(branches, else_body)]

    def _apply_while(self, statement: While, scope: CompileTimeScope) -> list[Statement]:
        if statement.comptime:
            lowered: list[Statement] = []
            iterations = 0
            while self._is_truthy(self._eval_comptime_expression(statement.condition, scope)):
                self._check_loop_limit(iterations, 'while')
                lowered.extend(self._apply_statements(statement.body, CompileTimeScope(scope)))
                iterations += 1
            return lowered

        prelude, condition = self._apply_expression(statement.condition, scope)
        self._reject_runtime_formatted_string_expression(condition, 'while condition')
        body = self._apply_statements(statement.body, CompileTimeScope(scope))
        return [*prelude, While(condition, body)]

    def _apply_for(self, statement: For, scope: CompileTimeScope) -> list[Statement]:
        if statement.comptime:
            loop_scope = CompileTimeScope(scope)
            if statement.initializer is not None:
                returned = self._eval_comptime_statement(statement.initializer, loop_scope)
                if returned is not None:
                    raise CompileTimeError('Return is not allowed in a comptime for initializer.')

            lowered: list[Statement] = []
            iterations = 0
            while self._comptime_for_condition(statement.condition, loop_scope):
                self._check_loop_limit(iterations, 'for')
                lowered.extend(self._apply_statements(statement.body, CompileTimeScope(loop_scope)))
                if statement.update is not None:
                    returned = self._eval_comptime_statement(statement.update, loop_scope)
                    if returned is not None:
                        raise CompileTimeError('Return is not allowed in a comptime for update.')
                iterations += 1
            return lowered

        loop_scope = CompileTimeScope(scope)
        initializer = self._apply_for_part(statement.initializer, loop_scope, 'initializer')
        condition = None
        prelude: list[Statement] = []
        if statement.condition is not None:
            prelude, condition = self._apply_expression(statement.condition, loop_scope)
            self._reject_runtime_formatted_string_expression(condition, 'for condition')
        update = self._apply_for_part(statement.update, loop_scope, 'update')
        body = self._apply_statements(statement.body, CompileTimeScope(loop_scope))
        return [*prelude, For(initializer, condition, update, body)]

    def _apply_try(self, statement: Try, scope: CompileTimeScope) -> list[Statement]:
        if statement.comptime:
            returned = self._eval_comptime_statement(statement, scope)
            if returned is not None:
                raise CompileTimeError('Return is not allowed in a comptime try statement.')
            return []

        body = self._apply_statements(statement.body, CompileTimeScope(scope))
        catches = [
            CatchClause(
                self._apply_type_reference(catch.error_type, scope),
                catch.name,
                self._apply_statements(catch.body, CompileTimeScope(scope)),
            )
            for catch in statement.catches
        ]
        return [Try(body, catches)]

    def _apply_for_part(
        self, statement: Statement | None, scope: CompileTimeScope, label: str
    ) -> Statement | None:
        if statement is None:
            return None

        lowered = self._apply_statement(statement, scope)
        if not lowered:
            return None
        if len(lowered) != 1:
            raise CompileTimeError(f'For {label} produced multiple runtime statements.')
        return lowered[0]

    def _comptime_for_condition(
        self, condition: Expression | None, scope: CompileTimeScope
    ) -> bool:
        if condition is None:
            return True
        return self._is_truthy(self._eval_comptime_expression(condition, scope))

    def _check_loop_limit(self, iterations: int, loop_name: str) -> None:
        if iterations >= self.LOOP_ITERATION_LIMIT:
            raise CompileTimeError(
                f'Comptime {loop_name} loop exceeded {self.LOOP_ITERATION_LIMIT} iterations.'
            )

    def _apply_type_declaration(
        self, declaration: TypeDeclaration, scope: CompileTimeScope
    ) -> list[Statement]:
        if declaration.extern:
            if declaration.comptime:
                raise CompileTimeError(f'Extern type "{declaration.name}" cannot be comptime.')
            if declaration.fields or declaration.parameters or declaration.methods:
                raise CompileTimeError(f'Extern type "{declaration.name}" must be opaque.')
            return [
                TypeDeclaration(
                    declaration.name,
                    [],
                    public=declaration.public,
                    extern=True,
                    abi=declaration.abi,
                )
            ]
        if declaration.comptime:
            raise CompileTimeFeatureNotImplemented('Comptime classes/types are not implemented yet.')
        if declaration.parameters:
            return []
        self._reject_unsupported_comptime_type_features(declaration)
        return [self._runtime_type_declaration(declaration, scope)]

    def _apply_function_declaration(
        self, declaration: FunctionDeclaration, scope: CompileTimeScope
    ) -> list[Statement]:
        if any(
            parameter.comptime and parameter.passing_mode == 'move'
            for parameter in declaration.parameters
        ):
            raise CompileTimeError('Comptime parameters cannot use move.')
        if declaration.extern:
            if any(parameter.comptime for parameter in declaration.parameters):
                raise CompileTimeError('Extern function parameters cannot be marked comptime.')
            if declaration.comptime:
                return []
            return [self._runtime_extern_function_declaration(declaration, scope)]

        if declaration.comptime:
            raise CompileTimeFeatureNotImplemented('Comptime functions are not implemented yet.')

        if any(parameter.comptime for parameter in declaration.parameters):
            return []

        body_scope = CompileTimeScope(scope)
        return_type = self._apply_type_reference(declaration.return_type, body_scope)
        body = self._apply_statements(declaration.body, body_scope)
        self._validate_returns(declaration.name, return_type, body)
        return [
            FunctionDeclaration(
                declaration.name,
                [self._runtime_variable_declaration(parameter, scope) for parameter in declaration.parameters],
                body,
                return_type=return_type,
                public=declaration.public,
                raises=self._apply_raises_clause(declaration.raises, body_scope),
                raises_inferred=declaration.raises_inferred,
                unsafe=declaration.unsafe,
            )
        ]

    def _runtime_extern_function_declaration(
        self, declaration: FunctionDeclaration, scope: CompileTimeScope
    ) -> FunctionDeclaration:
        if declaration.raises_inferred:
            raise CompileTimeError(
                f'Extern function "{declaration.name}" cannot use inferred raises.'
            )
        body_scope = CompileTimeScope(scope)
        return_type = self._apply_type_reference(declaration.return_type, body_scope)
        return FunctionDeclaration(
            declaration.name,
            [self._runtime_variable_declaration(parameter, scope) for parameter in declaration.parameters],
            [],
            return_type=return_type,
            comptime=declaration.comptime,
            public=declaration.public,
            extern=True,
            abi=declaration.abi,
            raises=self._apply_raises_clause(declaration.raises, body_scope),
            unsafe=declaration.unsafe,
        )

    def _apply_print_statement(self, prt: Print, scope: CompileTimeScope) -> list[Statement]:
        if prt.comptime:
            self._eval_comptime_print(prt, scope)
            return []
        return [self._apply_print(prt, scope)]

    def _apply_print(self, prt: Print, scope: CompileTimeScope) -> Print:
        if prt.expr is not None:
            prelude, expr = self._apply_expression(prt.expr, scope)
            if prelude:
                raise CompileTimeError(
                    'Function specialization inside print expressions is not implemented yet.'
                )
            return Print(prt.name, expr)

        value = scope.get(prt.name)
        if value is not None:
            if self._is_comptime_struct_literal(value):
                raise CompileTimeError(
                    f'Cannot print comptime struct value "{prt.name}" at runtime; mark the print comptime or access a field instead.'
                )
            if self._is_comptime_opaque_literal(value):
                raise CompileTimeError(
                    f'Cannot print opaque comptime value "{prt.name}" at runtime; mark the print comptime or derive plain data instead.'
                )
            if self._is_comptime_array_literal(value) or self._is_comptime_borrow_literal(value):
                raise CompileTimeError(
                    f'Cannot print comptime memory value "{prt.name}" at runtime; mark the print comptime or access an element instead.'
                )
            return Print(prt.name, copy.deepcopy(value))
        if scope.contains_root(prt.name):
            raise CompileTimeError(
                f'Cannot print comptime value "{prt.name}" at runtime; mark the print comptime or access a field instead.'
            )
        return Print(prt.name)

    def _eval_comptime_print(self, prt: Print, scope: CompileTimeScope) -> None:
        if prt.expr is None:
            value = self._eval_comptime_expression(VariableExpression(prt.name), scope)
        else:
            value = self._eval_comptime_expression(prt.expr, scope)

        if self.print_handler is not None:
            formatted = self._format_comptime_value(value)
            if prt.name == '':
                self.print_handler(formatted)
            else:
                self.print_handler(f'{prt.name} = {formatted}')

    def _format_comptime_value(self, value: LiteralExpression) -> str:
        if value.type == 'type' and type(value.value) is TypeReference:
            return self._type_key(value.value)
        if is_builtin_type(value.type):
            return format_builtin_value(value.value, value.type)
        if type(value.value) is ComptimeStructValue:
            fields = ', '.join(
                f'{name} = {self._format_comptime_value(field)}'
                for name, field in value.value.fields.items()
            )
            return f'{self._type_name(value.value.type_ref)}{{{fields}}}'
        if type(value.value) is ComptimeOpaqueValue:
            return f'<opaque {self._type_name(value.value.type_ref)}>'
        if type(value.value) is ComptimeArrayValue:
            items = ', '.join(self._format_comptime_value(item) for item in value.value.elements)
            return f'[{items}]'
        if type(value.value) is ComptimeBorrowValue:
            return f'<borrow {self._type_name(value.value.type_ref)}>'
        return str(value.value)

    def _apply_expression(
        self, expression: Expression, scope: CompileTimeScope
    ) -> tuple[list[Statement], Expression]:
        try:
            prelude, lowered = self._apply_expression_inner(expression, scope)
        except CompileTimeError as err:
            self._attach_error_span(err, expression)
            raise
        if lowered.span is None:
            lowered.span = expression.span
        for statement in prelude:
            if statement.span is None:
                statement.span = expression.span
        return prelude, lowered

    def _apply_expression_inner(
        self, expression: Expression, scope: CompileTimeScope
    ) -> tuple[list[Statement], Expression]:
        if type(expression) is LiteralExpression:
            return [], copy.deepcopy(expression)
        if type(expression) is FormattedStringExpression:
            return self._apply_formatted_string_expression(expression, scope)
        if type(expression) is StructLiteralExpression:
            return self._apply_struct_literal_expression(expression, scope)
        if type(expression) is VariableExpression:
            value = scope.get(expression.name)
            if value is not None:
                if value.type == 'type':
                    raise CompileTimeError(f'Cannot use type "{expression.name}" as a runtime value.')
                if self._is_comptime_struct_literal(value):
                    raise CompileTimeError(
                        f'Cannot use comptime struct value "{expression.name}" as a runtime value; access a field instead.'
                    )
                if self._is_comptime_opaque_literal(value):
                    raise CompileTimeError(
                        f'Cannot use opaque comptime value "{expression.name}" of type "{value.type}" as a runtime value.'
                    )
                if self._is_comptime_array_literal(value) or self._is_comptime_borrow_literal(value):
                    raise CompileTimeError(
                        f'Cannot use comptime memory value "{expression.name}" of type "{value.type}" as a runtime value; access an element or call len at comptime instead.'
                    )
                return [], copy.deepcopy(value)
            return [], copy.deepcopy(expression)
        if type(expression) is CompositeExpression:
            left_prelude, left = self._apply_expression(expression.left, scope)
            right_prelude, right = self._apply_expression(expression.right, scope)
            return (
                [*left_prelude, *right_prelude],
                CompositeExpression(left, right, expression.operator),
            )
        if type(expression) is FunctionCall:
            if expression.function_name == 'Layout.of':
                expression.function_name = 'layout_of'
            elif expression.function_name.endswith('$Layout.of'):
                expression.function_name = (
                    expression.function_name[:-len('$Layout.of')] + '$layout_of'
                )
            elif expression.function_name == 'Layout.array':
                expression.function_name = 'layout_array'
            elif expression.function_name.endswith('$Layout.array'):
                expression.function_name = (
                    expression.function_name[:-len('$Layout.array')] + '$layout_array'
                )
            if expression.function_name in self.TYPE_LAYOUT_QUERY_FUNCTIONS:
                return [], self._eval_comptime_layout_function_call(expression, scope)
            if (
                expression.function_name == 'len'
                and len(expression.parameters) == 1
                and self._expression_has_comptime_root(expression.parameters[0], scope)
            ):
                return [], self._eval_comptime_expression(expression, scope)
            return self._apply_function_call(expression, scope)
        if type(expression) is TypeExpression:
            raise CompileTimeError('Type expressions can only be used by sizeof and alignof.')
        if type(expression) is BorrowExpression:
            prelude, expr = self._apply_expression(expression.expr, scope)
            return prelude, BorrowExpression(expression.mode, expr)
        if type(expression) is MoveExpression:
            prelude, expr = self._apply_expression(expression.expr, scope)
            return prelude, MoveExpression(expr)
        if type(expression) is DereferenceExpression:
            prelude, expr = self._apply_expression(expression.expr, scope)
            return prelude, DereferenceExpression(expr)
        if type(expression) is IndexExpression:
            if self._expression_has_comptime_root(expression.target, scope):
                return [], self._eval_comptime_expression(expression, scope)
            target_prelude, target = self._apply_expression(expression.target, scope)
            index_prelude, index = self._apply_expression(expression.index, scope)
            return [*target_prelude, *index_prelude], IndexExpression(target, index)
        if type(expression) is SliceExpression:
            target_prelude, target = self._apply_expression(expression.target, scope)
            prelude = list(target_prelude)
            start = None
            end = None
            if expression.start is not None:
                start_prelude, start = self._apply_expression(expression.start, scope)
                prelude.extend(start_prelude)
            if expression.end is not None:
                end_prelude, end = self._apply_expression(expression.end, scope)
                prelude.extend(end_prelude)
            return prelude, SliceExpression(target, start, end)

        raise CompileTimeError(f'Unknown expression type "{type(expression).__name__}".')

    def _apply_struct_literal_expression(
        self, expression: StructLiteralExpression, scope: CompileTimeScope
    ) -> tuple[list[Statement], Expression]:
        prelude: list[Statement] = []
        fields: list[StructLiteralField] = []
        for field in expression.fields:
            field_prelude, field_expr = self._apply_expression(field.expr, scope)
            prelude.extend(field_prelude)
            fields.append(
                StructLiteralField(field.name, field_expr, span=field.span)
            )
        return prelude, StructLiteralExpression(
            self._apply_type_reference(expression.type_ref, scope),
            fields,
        )

    def _apply_formatted_string_expression(
        self, expression: FormattedStringExpression, scope: CompileTimeScope
    ) -> tuple[list[Statement], Expression]:
        prelude: list[Statement] = []
        parts: list[object] = []
        can_fold = True

        for part in expression.parts:
            if type(part) is str:
                parts.append(part)
                continue

            part_prelude, lowered = self._apply_expression(part, scope)
            prelude.extend(part_prelude)
            parts.append(lowered)
            if type(lowered) is not LiteralExpression:
                can_fold = False

        if can_fold:
            return prelude, LiteralExpression(
                ''.join(
                    part
                    if type(part) is str
                    else self._format_comptime_value(part)
                    for part in parts
                ),
                'str',
            )

        return prelude, FormattedStringExpression(parts)

    def _apply_function_call(
        self, call: FunctionCall, scope: CompileTimeScope
    ) -> tuple[list[Statement], FunctionCall]:
        if call.function_name in self.TYPE_LAYOUT_QUERY_FUNCTIONS:
            raise CompileTimeError(f'{call.function_name} must be used as an expression.')
        if call.function_name.endswith('.cast'):
            if len(call.parameters) != 1 or type(call.parameters[0]) is not TypeExpression:
                raise CompileTimeError('Raw pointer cast expects one type argument.')
            return [], FunctionCall(
                call.function_name,
                [TypeExpression(self._apply_type_reference(call.parameters[0].type_ref, scope))],
            )

        declaration = self.functions.get(call.function_name)
        argument_preludes: list[Statement] = []
        arguments: list[Expression] = []
        for index, argument in enumerate(call.parameters):
            parameter = (
                declaration.parameters[index]
                if declaration is not None and index < len(declaration.parameters)
                else None
            )
            if (
                parameter is not None
                and parameter.comptime
                and parameter.type.name == 'type'
                and type(argument) is VariableExpression
            ):
                value = scope.get(argument.name)
                if value is not None and value.type == 'type':
                    prelude, expression = [], copy.deepcopy(value)
                else:
                    prelude, expression = [], LiteralExpression(
                        self._apply_type_reference(TypeReference(argument.name), scope),
                        'type',
                    )
            else:
                prelude, expression = self._apply_expression_for_argument(argument, scope)
            argument_preludes.extend(prelude)
            arguments.append(expression)

        comptime_method_call = self._apply_comptime_receiver_method_call(call, arguments, scope)
        if comptime_method_call is not None:
            receiver_prelude, receiver_call = comptime_method_call
            return [*argument_preludes, *receiver_prelude], receiver_call

        if declaration is not None and declaration.extern and declaration.comptime:
            raise CompileTimeError(
                f'Comptime extern function "{call.function_name}" cannot be called at runtime.'
            )
        if declaration is None or not any(parameter.comptime for parameter in declaration.parameters):
            self._reject_runtime_comptime_struct_literals(arguments, call.function_name)
            for argument in arguments:
                self._reject_runtime_formatted_string_expression(argument, f'call to "{call.function_name}"')
            return argument_preludes, FunctionCall(
                call.function_name,
                arguments,
                interface_name=self._interface_for_call(call.function_name),
            )

        expected = len(declaration.parameters)
        actual = len(arguments)
        if actual != expected:
            raise CompileTimeError(
                f'Function "{call.function_name}" expects {expected} argument(s), got {actual}.'
            )

        comptime_key: list[tuple[str, object, str]] = []
        runtime_parameters: list[VariableDeclaration] = []
        runtime_arguments: list[Expression] = []
        body_scope = CompileTimeScope(scope)

        for parameter, argument in zip(declaration.parameters, arguments):
            if parameter.comptime:
                value = self._eval_comptime_argument(argument, parameter.type, body_scope)
                self._validate_constraints(parameter, value, body_scope)
                comptime_key.append((parameter.name, self._key_value(value.value), value.type))
                body_scope.declare(parameter.name, value)
            else:
                if type(argument) is LiteralExpression and (
                    self._is_comptime_struct_literal(argument)
                    or self._is_comptime_array_literal(argument)
                    or self._is_comptime_borrow_literal(argument)
                    or self._is_comptime_opaque_literal(argument)
                ):
                    raise CompileTimeError(
                        f'Runtime parameter "{parameter.name}" of function "{call.function_name}" cannot receive a comptime value.'
                    )
                self._reject_runtime_formatted_string_expression(argument, f'call to "{call.function_name}"')
                runtime_parameters.append(self._runtime_variable_declaration(parameter, body_scope))
                runtime_arguments.append(argument)

        key = (call.function_name, tuple(comptime_key))
        variant_name = self.variant_names.get(key)

        if variant_name is None:
            variant_name = self._variant_name(call.function_name, key[1])
            self.variant_names[key] = variant_name
            return_type = self._apply_type_reference(declaration.return_type, body_scope)
            previous_dispatch = self.active_interface_dispatch
            self.active_interface_dispatch = self._generic_interface_dispatch(declaration)
            try:
                body = self._apply_statements(declaration.body, body_scope)
            finally:
                self.active_interface_dispatch = previous_dispatch
            self._validate_returns(call.function_name, return_type, body)
            variant = FunctionDeclaration(
                variant_name,
                runtime_parameters,
                body,
                return_type=return_type,
                public=declaration.public,
                module_name=declaration.module_name,
                source_name=declaration.source_name,
                imports=list(declaration.imports),
                qualified_imports=list(declaration.qualified_imports),
                raises=self._apply_raises_clause(declaration.raises, body_scope),
                raises_inferred=declaration.raises_inferred,
                unsafe=declaration.unsafe,
                span=declaration.span,
            )
            self.generated_variants.append(variant)
            self.functions[variant_name] = variant

        return argument_preludes, FunctionCall(variant_name, runtime_arguments)

    def _generic_interface_dispatch(
        self, declaration: FunctionDeclaration
    ) -> dict[str, set[str]]:
        constraints = {
            parameter.name: {constraint.name for constraint in parameter.constraints}
            for parameter in declaration.parameters
            if parameter.comptime and parameter.type.name == 'type'
        }
        return {
            parameter.name: set(constraints[parameter.type.name])
            for parameter in declaration.parameters
            if not parameter.comptime and parameter.type.name in constraints
        }

    def _interface_for_call(self, function_name: str) -> str | None:
        if '.' not in function_name:
            return None
        receiver_name, method_name = function_name.rsplit('.', 1)
        interfaces = self.active_interface_dispatch.get(receiver_name, set())
        if not interfaces:
            interfaces = self.active_interface_dispatch.get(
                receiver_name.split('.', 1)[0], set()
            )
        matches = []
        for interface_name in sorted(interfaces):
            declaration = self.interfaces.get(interface_name)
            if declaration is not None and any(
                method.name == method_name for method in declaration.methods
            ):
                matches.append(interface_name)
            elif interface_name == 'Copyable' and method_name == 'init':
                matches.append(interface_name)
        if len(matches) > 1:
            raise CompileTimeError(
                f'Ambiguous constrained method "{method_name}" from interfaces '
                + ', '.join(f'"{name}"' for name in matches) + '.'
            )
        return matches[0] if matches else None

    def _apply_comptime_receiver_method_call(
        self, call: FunctionCall, arguments: list[Expression], scope: CompileTimeScope
    ) -> tuple[list[Statement], FunctionCall] | None:
        if '.' not in call.function_name:
            return None

        receiver_name, method_name = call.function_name.rsplit('.', 1)
        receiver = scope.get(receiver_name)
        if receiver is None:
            return None
        if not self._is_comptime_struct_literal(receiver):
            raise CompileTimeError(
                f'Comptime value "{receiver_name}" cannot be used as a method receiver.'
            )

        return self._lower_comptime_receiver_method_call(
            receiver_name, receiver, method_name, arguments, scope
        )

    def _lower_comptime_receiver_method_call(
        self,
        receiver_name: str,
        receiver: LiteralExpression,
        method_name: str,
        arguments: list[Expression],
        scope: CompileTimeScope,
    ) -> tuple[list[Statement], FunctionCall]:
        receiver_value = self._comptime_struct_value(receiver, receiver_name)
        receiver_type_name = self._type_name(receiver_value.type_ref)
        type_decl = self._type_declaration_for(receiver_value.type_ref)
        method = self._method_declaration_for(type_decl, method_name)

        expected = len(method.parameters)
        actual = len(arguments)
        if actual != expected:
            raise CompileTimeError(
                f'Method "{receiver_type_name}.{method_name}" expects {expected} argument(s), got {actual}.'
            )

        if any(parameter.comptime for parameter in method.parameters):
            raise CompileTimeFeatureNotImplemented(
                'Comptime method parameters are not implemented yet.'
            )
        self._reject_runtime_comptime_struct_literals(
            arguments, f'{receiver_type_name}.{method_name}'
        )
        for argument in arguments:
            self._reject_runtime_formatted_string_expression(
                argument, f'call to "{receiver_type_name}.{method_name}"'
            )

        method_scope = CompileTimeScope(scope)
        method_scope.declare('self', copy.deepcopy(receiver))
        for parameter in method.parameters:
            self._runtime_variable_declaration(parameter, method_scope)

        runtime_receiver_name = self._next_synthetic_name(f'{receiver_name}$runtime')
        prelude = self._materialize_comptime_struct_value(
            runtime_receiver_name, receiver_value, source_name=receiver_name
        )
        return prelude, FunctionCall(
            f'{runtime_receiver_name}.{method_name}',
            copy.deepcopy(arguments),
        )

    def _materialize_comptime_struct_value(
        self, name: str, value: ComptimeStructValue, source_name: str | None = None
    ) -> list[Statement]:
        statements: list[Statement] = [
            VariableDeclaration(name, copy.deepcopy(value.type_ref))
        ]
        statements.extend(self._materialize_comptime_struct_fields(name, value))
        return statements

    def _materialize_comptime_struct_fields(
        self, root_name: str, value: ComptimeStructValue
    ) -> list[Statement]:
        statements: list[Statement] = []
        for field_name, field_value in value.fields.items():
            field_path = f'{root_name}.{field_name}'
            if type(field_value.value) is ComptimeStructValue:
                statements.extend(
                    self._materialize_comptime_struct_fields(field_path, field_value.value)
                )
            elif field_value.type == 'type':
                raise CompileTimeError(
                    f'Cannot materialize type field "{field_path}" as a runtime value.'
                )
            elif type(field_value.value) is ComptimeOpaqueValue:
                raise CompileTimeError(
                    f'Cannot materialize opaque comptime field "{field_path}" of type "{field_value.type}" as a runtime value.'
                )
            elif type(field_value.value) in {ComptimeArrayValue, ComptimeBorrowValue}:
                raise CompileTimeError(
                    f'Cannot materialize comptime memory field "{field_path}" of type "{field_value.type}" as a runtime value.'
                )
            else:
                statements.append(Assignment(field_path, copy.deepcopy(field_value)))
        return statements

    def _next_synthetic_name(self, base_name: str) -> str:
        self.synthetic_counter += 1
        return f'{base_name}${self.synthetic_counter}'

    def _apply_raises_clause(
        self, raises: Iterable[TypeReference], scope: CompileTimeScope
    ) -> list[TypeReference]:
        return [copy.deepcopy(error) for error in raises]

    def _apply_type_reference(self, type_ref: TypeReference, scope: CompileTimeScope) -> TypeReference:
        base_ref = TypeReference(type_ref.name, copy.deepcopy(type_ref.arguments))
        if base_ref.arguments:
            lowered = self._apply_generic_type_reference(base_ref, scope)
        else:
            value = scope.get(base_ref.name)
            if value is not None and value.type == 'type':
                lowered = copy.deepcopy(value.value)
            else:
                declaration = self.types.get(base_ref.name)
                if declaration is not None and declaration.parameters:
                    raise CompileTimeError(
                        f'Generic type "{base_ref.name}" requires comptime arguments.'
                    )
                lowered = copy.deepcopy(base_ref)

        lowered.array_size = self._apply_array_size(type_ref.array_size, scope)
        lowered.is_slice = type_ref.is_slice
        lowered.borrow = type_ref.borrow
        lowered.pointer_mode = type_ref.pointer_mode
        lowered.nullable = type_ref.nullable
        lowered.span = type_ref.span
        return lowered

    def _apply_generic_type_reference(
        self, type_ref: TypeReference, scope: CompileTimeScope
    ) -> TypeReference:
        if type_ref.name == 'MaybeUninit':
            if len(type_ref.arguments) != 1:
                raise CompileTimeError(
                    f'MaybeUninit expects 1 type argument, got {len(type_ref.arguments)}.'
                )
            value = self._eval_comptime_argument(
                type_ref.arguments[0], TypeReference('type'), scope
            )
            if value.type != 'type' or type(value.value) is not TypeReference:
                raise CompileTimeError('MaybeUninit requires a type argument.')
            element = self._apply_type_reference(value.value, scope)
            if self._type_name(element) in {'void', 'type', 'c_void'}:
                raise CompileTimeError(
                    f'MaybeUninit cannot contain "{self._type_name(element)}".'
                )
            key = (
                'MaybeUninit',
                (('T', self._key_value(element), 'type'),),
            )
            variant_name = self.type_variant_names.get(key)
            if variant_name is None:
                variant_name = self._variant_name('MaybeUninit', key[1])
                self.type_variant_names[key] = variant_name
                generated_type = TypeDeclaration(
                    variant_name,
                    [],
                    language_item='MaybeUninit',
                    language_item_type=copy.deepcopy(element),
                    public=True,
                    source_name='MaybeUninit',
                    span=type_ref.span,
                )
                self.generated_types.append(generated_type)
                self.types[variant_name] = generated_type
            return TypeReference(variant_name, span=type_ref.span)
        declaration = self.types.get(type_ref.name)
        if declaration is None:
            raise CompileTimeError(f'Unknown generic type "{type_ref.name}".')
        if not declaration.parameters:
            raise CompileTimeError(f'Type "{type_ref.name}" does not take comptime arguments.')
        if len(type_ref.arguments) != len(declaration.parameters):
            raise CompileTimeError(
                f'Type "{type_ref.name}" expects {len(declaration.parameters)} argument(s), got {len(type_ref.arguments)}.'
            )

        field_scope = CompileTimeScope(scope)
        comptime_key: list[tuple[str, object, str]] = []
        for parameter, argument in zip(declaration.parameters, type_ref.arguments):
            if not parameter.comptime:
                raise CompileTimeFeatureNotImplemented('Runtime type parameters are not implemented yet.')
            value = self._eval_comptime_argument(argument, parameter.type, field_scope)
            self._validate_constraints(parameter, value, field_scope)
            comptime_key.append((parameter.name, self._key_value(value.value), value.type))
            field_scope.declare(parameter.name, value)

        key = (type_ref.name, tuple(comptime_key))
        variant_name = self.type_variant_names.get(key)
        if variant_name is None:
            variant_name = self._variant_name(type_ref.name, key[1])
            self.type_variant_names[key] = variant_name
            self._reject_unsupported_comptime_type_features(declaration)
            fields = [self._runtime_variable_declaration(field, field_scope) for field in declaration.fields]
            constraints = {
                parameter.name: {constraint.name for constraint in parameter.constraints}
                for parameter in declaration.parameters
                if parameter.comptime and parameter.type.name == 'type'
            }
            previous_dispatch = self.active_interface_dispatch
            self.active_interface_dispatch = {
                f'self.{field.name}': set(constraints[field.type.name])
                for field in declaration.fields
                if field.type.name in constraints
            }
            try:
                methods = [
                    self._runtime_method_declaration(
                        method,
                        field_scope,
                        owner_type=TypeReference(variant_name),
                        owner_source_name=declaration.name,
                    )
                    for method in declaration.methods
                ]
            finally:
                self.active_interface_dispatch = previous_dispatch
            for implementation in self.implementations:
                if implementation.type_name != declaration.name:
                    continue
                for method in implementation.methods:
                    concrete = copy.deepcopy(method)
                    concrete.interface_name = implementation.interface.name
                    concrete.source_name = method.source_name or method.name
                    concrete.name = f'{implementation.interface.name}${method.name}'
                    self._mark_implementation_calls(
                        concrete, implementation.interface.name
                    )
                    self._replace_self_in_function(concrete, variant_name)
                    methods.append(self._runtime_method_declaration(
                        concrete,
                        field_scope,
                        owner_type=TypeReference(variant_name),
                        owner_source_name=declaration.name,
                    ))
            generated_type = TypeDeclaration(
                variant_name,
                fields,
                methods=methods,
                public=declaration.public,
                module_name=declaration.module_name,
                source_name=declaration.source_name,
                imports=list(declaration.imports),
                qualified_imports=list(declaration.qualified_imports),
                span=declaration.span,
            )
            self.generated_types.append(generated_type)
            self.types[variant_name] = generated_type

        return TypeReference(variant_name, span=type_ref.span)

    def _validate_constraints(
        self,
        parameter: VariableDeclaration,
        value: LiteralExpression,
        scope: CompileTimeScope,
    ) -> None:
        if not parameter.constraints:
            return
        if value.type != 'type' or type(value.value) is not TypeReference:
            raise CompileTimeError(
                f'Constraints on "{parameter.name}" require a type argument.'
            )
        concrete = self._apply_type_reference(value.value, scope)
        for constraint in parameter.constraints:
            constraint_ref = self._apply_type_reference(constraint, scope)
            if not self._type_satisfies_constraint(concrete, constraint_ref.name):
                raise CompileTimeError(
                    f'Type "{self._type_name(concrete)}" does not implement '
                    f'"{constraint_ref.name}" required by "{parameter.name}".'
                )

    def _type_satisfies_constraint(self, type_ref: TypeReference, interface: str) -> bool:
        name = self._type_name(type_ref)
        declaration = self.types.get(name)
        source_name = (
            (declaration.source_name or self._generic_source_name(name))
            if declaration is not None else name
        )
        if any(
            implementation.interface.name == interface
            and implementation.type_name in {
                name,
                source_name,
                self._generic_source_name(name),
            }
            for implementation in self.implementations
        ):
            return True
        if interface != 'Copyable':
            return False
        if type_ref.borrow == 'in':
            return True
        if type_ref.borrow in {'out', 'inout'} or type_ref.is_slice:
            return False
        if is_builtin_type(name) or name in {'str', 'c_char', 'c_void', 'type'}:
            return True
        if declaration is None or declaration.extern:
            return False
        if any(method.name == 'deinit' for method in declaration.methods):
            return False
        return all(self._type_satisfies_constraint(field.type, 'Copyable')
                   for field in declaration.fields)

    def _generic_source_name(self, concrete_name: str) -> str:
        return concrete_name.split('$comptime$', 1)[0]

    def _apply_array_size(
        self, expression: Expression | None, scope: CompileTimeScope
    ) -> Expression | None:
        if expression is None:
            return None
        prelude, lowered = self._apply_expression(expression, scope)
        if prelude:
            raise CompileTimeError('Function specialization inside array sizes is not implemented yet.')
        self._reject_runtime_formatted_string_expression(lowered, 'array size')
        return lowered

    def _eval_comptime_argument(
        self, argument: object, expected_type: TypeReference, scope: CompileTimeScope
    ) -> LiteralExpression:
        if self._is_type_type(expected_type):
            return LiteralExpression(self._eval_comptime_type_argument(argument, scope), 'type')
        expression = self._argument_as_expression(argument)
        if expected_type.borrow is not None:
            if type(expression) is BorrowExpression:
                raise CompileTimeError(
                    'Call arguments do not accept explicit borrow markers; '
                    'the parameter determines its passing behavior.'
                )
            expression = BorrowExpression(expected_type.borrow, expression)
        value = self._eval_comptime_expression(expression, scope)
        runtime_type = self._apply_type_reference(expected_type, scope)
        return LiteralExpression(
            self._cast_comptime(value.value, runtime_type, source_type=value.type),
            self._type_name(runtime_type),
        )

    def _eval_comptime_type_argument(self, argument: object, scope: CompileTimeScope) -> TypeReference:
        if (
            type(argument) is LiteralExpression
            and argument.type == 'type'
            and type(argument.value) is TypeReference
        ):
            return self._apply_type_reference(argument.value, scope)
        if type(argument) is TypeReference:
            return self._apply_type_reference(argument, scope)
        if type(argument) is FunctionCall:
            return self._apply_type_reference(
                TypeReference(argument.function_name, list(argument.parameters)), scope
            )
        if type(argument) is VariableExpression:
            value = scope.get(argument.name)
            if value is not None and value.type == 'type':
                return copy.deepcopy(value.value)
            return TypeReference(argument.name)
        raise CompileTimeError(f'Expected type argument, got {type(argument).__name__}.')

    def _argument_as_expression(self, argument: object) -> Expression:
        if isinstance(argument, Expression):
            return argument
        if type(argument) is TypeReference and not argument.arguments:
            return VariableExpression(argument.name)
        raise CompileTimeError(f'Expected value argument, got {type(argument).__name__}.')

    def _eval_comptime_expression(
        self, expression: Expression, scope: CompileTimeScope
    ) -> LiteralExpression:
        return self.executor._eval_expression(expression, scope)

    def _eval_comptime_function_call(
        self, call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        if call.function_name in self.TYPE_LAYOUT_QUERY_FUNCTIONS:
            return self._eval_comptime_layout_function_call(call, scope)
        if call.function_name == 'len':
            return self._eval_comptime_len_function_call(call, scope)
        if is_builtin_type(call.function_name):
            return self._eval_comptime_builtin_conversion(call, scope)

        declaration = self.functions.get(call.function_name)
        if declaration is None:
            raise CompileTimeError(f'Unknown function "{call.function_name}".')
        if declaration.extern:
            return self._eval_comptime_extern_function_call(declaration, call, scope)
        if declaration.comptime:
            raise CompileTimeFeatureNotImplemented('Comptime functions are not implemented yet.')

        expected = len(declaration.parameters)
        actual = len(call.parameters)
        if actual != expected:
            raise CompileTimeError(
                f'Function "{call.function_name}" expects {expected} argument(s), got {actual}.'
            )

        function_scope = CompileTimeScope(scope)
        for parameter, argument in zip(declaration.parameters, call.parameters):
            value = self._eval_comptime_argument(argument, parameter.type, function_scope)
            function_scope.declare(parameter.name, value)

        return_type = self._apply_type_reference(declaration.return_type, function_scope)
        returned = self._eval_comptime_statements(declaration.body, function_scope)
        if returned is None:
            if self._is_void_type(return_type):
                return LiteralExpression(None, 'void')
            raise CompileTimeError(
                f'Comptime function call "{call.function_name}" did not return a value.'
            )
        return LiteralExpression(
            self._cast_comptime(returned.value, return_type, source_type=returned.type),
            self._type_name(return_type),
        )

    def _eval_comptime_layout_function_call(
        self, call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        if len(call.parameters) != 1:
            raise CompileTimeError(
                f'{call.function_name} expects 1 type argument, got {len(call.parameters)}.'
            )
        parameter = call.parameters[0]
        if type(parameter) is not TypeExpression:
            raise CompileTimeError(f'{call.function_name} expects a type argument.')

        type_ref = self._apply_type_reference(parameter.type_ref, scope)
        layout = self._layout_of_type(type_ref, scope)
        if call.function_name == 'sizeof':
            return LiteralExpression(layout.size, 'usize')
        if call.function_name == 'alignof':
            return LiteralExpression(layout.align, 'usize')
        raise CompileTimeError(f'Unknown layout query "{call.function_name}".')

    def _eval_comptime_len_function_call(
        self, call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        if len(call.parameters) != 1:
            raise CompileTimeError(f'len expects 1 argument, got {len(call.parameters)}.')
        value = self._eval_comptime_expression(call.parameters[0], scope)
        if type(value.value) is ComptimeArrayValue:
            return LiteralExpression(len(value.value.elements), 'i32')
        if type(value.value) is ComptimeBorrowValue:
            return LiteralExpression(value.value.window_length(), 'i32')
        raise CompileTimeError(
            f'len expects a comptime array or slice, got "{value.type}".'
        )

    def _eval_comptime_extern_function_call(
        self, declaration: FunctionDeclaration, call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        handler = self.comptime_externs.get(declaration.name)
        if not declaration.comptime:
            if declaration.abi != 'c':
                raise CompileTimeError(
                    f'Extern function "{declaration.name}" cannot be called at comptime; mark it comptime extern and provide a host binding.'
                )
            if handler is None:
                raise CompileTimeError(
                    f'Extern function "{declaration.name}" cannot be called at comptime without a registered C host binding.'
                )

        if handler is None:
            raise CompileTimeError(
                f'No comptime extern binding registered for "{declaration.name}".'
            )

        expected = len(declaration.parameters)
        actual = len(call.parameters)
        if actual != expected:
            raise CompileTimeError(
                f'Function "{call.function_name}" expects {expected} argument(s), got {actual}.'
            )

        values: list[LiteralExpression] = []
        call_scope = CompileTimeScope(scope)
        for parameter, argument in zip(declaration.parameters, call.parameters):
            value = self._eval_comptime_argument(argument, parameter.type, call_scope)
            call_scope.declare(parameter.name, value)
            values.append(value)

        try:
            result = handler(*(self._comptime_extern_argument(value) for value in values))
        except Exception as err:
            raise CompileTimeError(
                f'Comptime extern function "{declaration.name}" failed: {err}'
            ) from err

        return_type = self._apply_type_reference(declaration.return_type, call_scope)
        if self._is_void_type(return_type):
            if result is not None:
                raise CompileTimeError(
                    f'Comptime extern function "{declaration.name}" returned a value for void.'
                )
            return LiteralExpression(None, 'void')
        return LiteralExpression(
            self._cast_comptime(result, return_type),
            self._type_name(return_type),
        )

    def _comptime_extern_argument(self, value: LiteralExpression) -> object:
        if type(value.value) is ComptimeOpaqueValue:
            return value.value.value
        if type(value.value) is ComptimeBorrowValue:
            target: object = value.value
            seen: set[int] = set()
            while type(target) is ComptimeBorrowValue and target.cell is not None:
                if id(target) in seen:
                    break
                seen.add(id(target))
                target = target.cell.value
            if type(target) is ComptimeOpaqueValue:
                return target.value
        return value.value

    def _eval_comptime_builtin_conversion(
        self, call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        if len(call.parameters) != 1:
            raise CompileTimeError(
                f'Type conversion "{call.function_name}" expects 1 argument, '
                f'got {len(call.parameters)}.'
            )

        value = self._eval_comptime_expression(call.parameters[0], scope)
        target_type = TypeReference(call.function_name)
        return LiteralExpression(
            self._cast_comptime(
                value.value, target_type, source_type=value.type, memory_raw=True
            ),
            call.function_name,
        )

    def _eval_comptime_method_call(
        self, call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        if '.' not in call.function_name:
            return self._eval_comptime_function_call(call, scope)

        receiver_name, method_name = call.function_name.rsplit('.', 1)
        receiver = scope.get(receiver_name)
        if receiver is None:
            raise CompileTimeError(f'Unknown comptime receiver "{receiver_name}".')
        receiver_value = self._comptime_struct_value(receiver, receiver_name)
        type_decl = self._type_declaration_for(receiver_value.type_ref)
        method = self._method_declaration_for(type_decl, method_name)

        expected = len(method.parameters)
        actual = len(call.parameters)
        if actual != expected:
            raise CompileTimeError(
                f'Method "{self._type_name(receiver_value.type_ref)}.{method_name}" expects {expected} argument(s), got {actual}.'
            )

        method_scope = CompileTimeScope(scope)
        method_scope.declare('self', receiver)
        for parameter, argument in zip(method.parameters, call.parameters):
            value = self._eval_comptime_argument(argument, parameter.type, method_scope)
            method_scope.declare(parameter.name, value)

        return_type = self._apply_type_reference(method.return_type, method_scope)
        returned = self._eval_comptime_statements(method.body, method_scope)
        if returned is None:
            if self._is_void_type(return_type):
                return LiteralExpression(None, 'void')
            raise CompileTimeError(
                f'Comptime method call "{call.function_name}" did not return a value.'
            )
        return LiteralExpression(
            self._cast_comptime(returned.value, return_type, source_type=returned.type),
            self._type_name(return_type),
        )

    def _eval_comptime_statements(
        self, statements: Iterable[Statement], scope: CompileTimeScope
    ) -> LiteralExpression | None:
        try:
            returned = self.executor._execute_statements(statements, scope, allow_return=True)
        except ComptimeRaisedError as err:
            raise CompileTimeError(
                f'Unhandled comptime error "{err.type_name}".'
            ) from err
        if returned is None:
            return None
        return returned.value

    def _eval_comptime_statement(
        self, statement: Statement, scope: CompileTimeScope
    ) -> LiteralExpression | None:
        try:
            returned = self.executor._execute_statement(statement, scope, allow_return=True)
        except ComptimeRaisedError as err:
            raise CompileTimeError(
                f'Unhandled comptime error "{err.type_name}".'
            ) from err
        if returned is None:
            return None
        return returned.value

    def _compare_comptime_values(
        self, left: LiteralExpression, right: LiteralExpression, operator: str
    ) -> bool:
        merged_type = self._merge_types(left, right)
        if merged_type == 'str' and operator not in {'==', '!='}:
            raise CompileTimeError(f'Operator "{operator}" is not implemented for comptime strings.')
        if is_bool_type(merged_type) and operator not in {'==', '!='}:
            raise CompileTimeError(f'Operator "{operator}" is not implemented for comptime bool values.')
        if is_raw_byte_type(merged_type) and operator not in {'==', '!='}:
            raise CompileTimeError(f'Operator "{operator}" is not implemented for comptime raw byte values.')
        if operator == '==':
            return left.value == right.value
        if operator == '!=':
            return left.value != right.value
        if operator == '<':
            return left.value < right.value
        if operator == '>':
            return left.value > right.value
        if operator == '<=':
            return left.value <= right.value
        if operator == '>=':
            return left.value >= right.value
        raise CompileTimeError(f'Unknown comptime comparison operator "{operator}".')

    def _is_truthy(self, value: LiteralExpression) -> bool:
        if is_bool_type(value.type):
            return bool(value.value)
        raise CompileTimeError(f'Cannot use comptime value of type "{value.type}" as a condition.')

    def _default_literal(
        self, type_ref: TypeReference, scope: CompileTimeScope
    ) -> LiteralExpression:
        return LiteralExpression(self._default_value(type_ref, scope), self._type_name(type_ref))

    def _is_comptime_struct_literal(self, value: LiteralExpression) -> bool:
        return type(value.value) is ComptimeStructValue

    def _is_comptime_opaque_literal(self, value: LiteralExpression) -> bool:
        return type(value.value) is ComptimeOpaqueValue

    def _is_comptime_array_literal(self, value: LiteralExpression) -> bool:
        return type(value.value) is ComptimeArrayValue

    def _is_comptime_borrow_literal(self, value: LiteralExpression) -> bool:
        return type(value.value) is ComptimeBorrowValue

    def _comptime_struct_value(
        self, value: LiteralExpression, name: str
    ) -> ComptimeStructValue:
        if type(value.value) is not ComptimeStructValue:
            raise CompileTimeError(f'Comptime value "{name}" is not a struct value.')
        return value.value

    def _is_comptime_struct_expression(self, expression: Expression) -> bool:
        return type(expression) is LiteralExpression and self._is_comptime_struct_literal(expression)

    def _reject_runtime_comptime_struct_literals(
        self, expressions: Iterable[Expression], function_name: str
    ) -> None:
        for expression in expressions:
            if type(expression) is not LiteralExpression:
                continue
            if self._is_comptime_struct_literal(expression):
                raise CompileTimeError(
                    f'Runtime call to function "{function_name}" cannot receive a comptime struct value.'
                )
            if (
                self._is_comptime_array_literal(expression)
                or self._is_comptime_borrow_literal(expression)
                or self._is_comptime_opaque_literal(expression)
            ):
                raise CompileTimeError(
                    f'Runtime call to function "{function_name}" cannot receive a comptime memory value.'
                )

    def _apply_expression_for_argument(
        self, expression: Expression, scope: CompileTimeScope
    ) -> tuple[list[Statement], Expression]:
        if type(expression) is TypeExpression:
            raise CompileTimeError('Type expressions can only be used by sizeof and alignof.')
        if type(expression) is VariableExpression:
            value = scope.get(expression.name)
            if value is not None:
                if value.type == 'type':
                    raise CompileTimeError(f'Cannot use type "{expression.name}" as a value.')
                if self._is_comptime_opaque_literal(value):
                    raise CompileTimeError(
                        f'Cannot pass opaque comptime value "{expression.name}" of type "{value.type}" to runtime code.'
                    )
                return [], copy.deepcopy(value)
            return [], copy.deepcopy(expression)
        return self._apply_expression(expression, scope)

    def _apply_constructor_args(
        self, arguments: Iterable[Expression], scope: CompileTimeScope
    ) -> tuple[list[Statement], list[Expression]]:
        prelude: list[Statement] = []
        lowered_arguments: list[Expression] = []
        for argument in arguments:
            argument_prelude, lowered_argument = self._apply_expression(argument, scope)
            self._reject_runtime_formatted_string_expression(lowered_argument, 'constructor argument')
            prelude.extend(argument_prelude)
            lowered_arguments.append(lowered_argument)
        return prelude, lowered_arguments

    def _reject_runtime_formatted_string_expression(
        self, expression: Expression, context: str
    ) -> None:
        if self._contains_runtime_formatted_string_expression(expression):
            raise CompileTimeError(
                f'Runtime formatted strings are only supported directly in print statements; found in {context}.'
            )

    def _contains_runtime_formatted_string_expression(self, expression: Expression) -> bool:
        if type(expression) is FormattedStringExpression:
            return True
        if type(expression) is CompositeExpression:
            return (
                self._contains_runtime_formatted_string_expression(expression.left)
                or self._contains_runtime_formatted_string_expression(expression.right)
            )
        if type(expression) is FunctionCall:
            return any(
                self._contains_runtime_formatted_string_expression(argument)
                for argument in expression.parameters
                if type(argument) is not TypeExpression
            )
        if type(expression) is TypeExpression:
            return False
        if type(expression) is StructLiteralExpression:
            return any(
                self._contains_runtime_formatted_string_expression(field.expr)
                for field in expression.fields
            )
        if type(expression) is BorrowExpression:
            return self._contains_runtime_formatted_string_expression(expression.expr)
        if type(expression) is IndexExpression:
            return (
                self._contains_runtime_formatted_string_expression(expression.target)
                or self._contains_runtime_formatted_string_expression(expression.index)
            )
        if type(expression) is SliceExpression:
            return (
                self._contains_runtime_formatted_string_expression(expression.target)
                or (expression.start is not None and self._contains_runtime_formatted_string_expression(expression.start))
                or (expression.end is not None and self._contains_runtime_formatted_string_expression(expression.end))
            )
        return False

    def _cast_comptime(
        self,
        value: object,
        type_ref: TypeReference,
        source_type: str | None = None,
        memory_raw: bool = False,
    ) -> object:
        if self._is_type_type(type_ref):
            if type(value) is TypeReference:
                return copy.deepcopy(value)
            if type(value) is str:
                return TypeReference(value)
            raise CompileTimeError(f'Cannot convert {value!r} to type "type".')
        if self._is_void_type(type_ref):
            if value is None:
                return None
            raise CompileTimeError(f'Cannot convert {value!r} to type "void".')
        if self._is_str_type(type_ref):
            if type(value) is str:
                return value
            raise CompileTimeError(f'Cannot convert {value!r} to type "str".')
        if type(value) is ComptimeOpaqueValue:
            if self._type_name(value.type_ref) == self._type_name(type_ref):
                return copy.deepcopy(value)
            raise CompileTimeError(
                f'Cannot convert opaque comptime value of type "{self._type_name(value.type_ref)}" to type "{self._type_name(type_ref)}".'
            )
        if self._is_borrow_type(type_ref):
            if type(value) is ComptimeBorrowValue:
                return self._cast_comptime_borrow(value, type_ref)
            if self._is_opaque_comptime_type(type_ref):
                return ComptimeOpaqueValue(copy.deepcopy(type_ref), value)
            return self._cast_comptime_borrow(value, type_ref)
        if self._is_opaque_comptime_type(type_ref):
            return ComptimeOpaqueValue(copy.deepcopy(type_ref), value)
        if self._is_array_type(type_ref):
            return self._cast_comptime_array(value, type_ref, source_type=source_type)
        if self._is_slice_type(type_ref):
            raise CompileTimeError(f'Cannot convert {value!r} to bare slice type "{self._type_name(type_ref)}".')
        type_name = self._type_name(type_ref)
        if is_builtin_type(type_name):
            try:
                return cast_builtin_value(
                    value, type_name, source_type=source_type, memory_raw=memory_raw
                )
            except (TypeError, ValueError, OverflowError) as err:
                raise CompileTimeError(f'Cannot convert {value!r} to type "{type_name}".') from err
        if self._is_struct_type(type_ref):
            if type(value) is not ComptimeStructValue:
                raise CompileTimeError(
                    f'Cannot convert {value!r} to type "{self._type_name(type_ref)}".'
                )
            if self._type_name(value.type_ref) != self._type_name(type_ref):
                raise CompileTimeError(
                    f'Cannot convert comptime value of type "{self._type_name(value.type_ref)}" to type "{self._type_name(type_ref)}".'
                )
            return copy.deepcopy(value)
        raise CompileTimeFeatureNotImplemented(
            f'Comptime type "{self._type_name(type_ref)}" is not implemented yet.'
        )

    def _default_value(
        self, type_ref: TypeReference, scope: CompileTimeScope | None = None
    ) -> object:
        type_name = self._type_name(type_ref)
        if is_builtin_type(type_name):
            return default_builtin_value(type_name)
        if self._is_str_type(type_ref):
            return str()
        if self._is_array_type(type_ref):
            size = self._array_size_value(type_ref.array_size, scope or CompileTimeScope())
            element_type = self._element_type(type_ref)
            return ComptimeArrayValue(
                copy.deepcopy(element_type),
                [self._default_literal(element_type, scope or CompileTimeScope()) for _ in range(size)],
            )
        if self._is_borrow_type(type_ref) or self._is_slice_type(type_ref):
            raise CompileTimeFeatureNotImplemented(
                f'Comptime type "{self._type_name(type_ref)}" is not implemented yet.'
            )
        if self._is_void_type(type_ref):
            raise CompileTimeError('Variables of type "void" are not allowed.')
        if self._is_type_type(type_ref):
            raise CompileTimeError('Comptime type variables must have an initializer.')
        if self._is_struct_type(type_ref):
            return self._default_struct_value(type_ref, scope or CompileTimeScope())
        raise CompileTimeFeatureNotImplemented(
            f'Comptime type "{self._type_name(type_ref)}" is not implemented yet.'
        )

    def _default_struct_value(
        self, type_ref: TypeReference, scope: CompileTimeScope
    ) -> ComptimeStructValue:
        type_decl = self._type_declaration_for(type_ref)
        fields: dict[str, LiteralExpression] = {}
        for field in type_decl.fields:
            field_type = self._apply_type_reference(field.type, scope)
            if self._is_void_type(field_type):
                raise CompileTimeError(f'Field "{field.name}" cannot have type "void".')
            try:
                fields[field.name] = self._default_literal(field_type, scope)
            except CompileTimeFeatureNotImplemented:
                if not self._is_opaque_comptime_type(field_type):
                    raise
                fields[field.name] = LiteralExpression(
                    ComptimeOpaqueValue(copy.deepcopy(field_type)),
                    self._type_name(field_type),
                )
        return ComptimeStructValue(copy.deepcopy(type_ref), fields)

    def _merge_types(self, left: LiteralExpression, right: LiteralExpression) -> str:
        if left.type == right.type:
            return left.type
        raise CompileTimeError(
            f'Cannot combine comptime values of type "{left.type}" and "{right.type}".'
        )

    def _variant_name(
        self, base_name: str, comptime_key: tuple[tuple[str, object, str], ...]
    ) -> str:
        suffix = '$'.join(
            f'{name}${self._sanitize_value(value)}' for name, value, _ in comptime_key
        )
        return f'{base_name}$comptime${suffix}'

    def _sanitize_value(self, value: object) -> str:
        if type(value) is bool:
            return 'true' if value else 'false'
        if type(value) is TypeReference:
            return self._sanitize_value(self._type_key(value))
        if type(value) is ComptimeStructValue:
            return self._sanitize_value(self._key_value(value))
        if type(value) is ComptimeArrayValue:
            return self._sanitize_value(self._key_value(value))
        if type(value) is ComptimeBorrowValue:
            raise CompileTimeError(
                f'Comptime borrow of type "{self._type_name(value.type_ref)}" cannot be used as a specialization key.'
            )
        if type(value) is ComptimeOpaqueValue:
            raise CompileTimeError(
                f'Opaque comptime value of type "{self._type_name(value.type_ref)}" cannot be used as a specialization key.'
            )
        if isinstance(value, tuple):
            return '_'.join(self._sanitize_value(item) for item in value)

        chars: list[str] = []
        previous_was_separator = False
        for char in str(value):
            if char.isascii() and (char.isalnum() or char == '_'):
                chars.append(char)
                previous_was_separator = False
            elif char == '-':
                chars.append('minus_')
                previous_was_separator = True
            elif not previous_was_separator:
                chars.append('_')
                previous_was_separator = True

        candidate = ''.join(chars).strip('_')
        return candidate or 'value'

    def _key_value(self, value: object) -> object:
        if type(value) is TypeReference:
            return self._type_key(value)
        if type(value) is ComptimeStructValue:
            return (
                self._type_key(value.type_ref),
                tuple((name, self._key_value(field.value), field.type) for name, field in value.fields.items()),
            )
        if type(value) is ComptimeArrayValue:
            return (
                self._type_key(value.element_type),
                tuple((self._key_value(item.value), item.type) for item in value.elements),
            )
        if type(value) is ComptimeBorrowValue:
            raise CompileTimeError(
                f'Comptime borrow of type "{self._type_name(value.type_ref)}" cannot be used as a specialization key.'
            )
        if type(value) is ComptimeOpaqueValue:
            raise CompileTimeError(
                f'Opaque comptime value of type "{self._type_name(value.type_ref)}" cannot be used as a specialization key.'
            )
        return value

    def _type_key(self, type_ref: TypeReference) -> str:
        if type_ref.arguments:
            args = ','.join(str(self._key_value(arg)) for arg in type_ref.arguments)
            name = f'{type_ref.name}({args})'
        else:
            name = type_ref.name
        if type_ref.array_size is not None:
            name = f'{name}[{self._expression_key(type_ref.array_size)}]'
        elif type_ref.is_slice:
            name = f'{name}[]'
        if type_ref.borrow is not None:
            name = f'&{type_ref.borrow} {name}'
        return name

    def _expression_key(self, expression: Expression) -> str:
        if type(expression) is LiteralExpression:
            return str(self._key_value(expression.value))
        if type(expression) is VariableExpression:
            return expression.name
        if type(expression) is CompositeExpression:
            return f'({self._expression_key(expression.left)}{expression.operator}{self._expression_key(expression.right)})'
        if type(expression) is FunctionCall:
            args = ','.join(self._expression_key(argument) for argument in expression.parameters)
            return f'{expression.function_name}({args})'
        if type(expression) is TypeExpression:
            return self._type_key(expression.type_ref)
        return type(expression).__name__

    def _cast_comptime_borrow(self, value: object, type_ref: TypeReference) -> ComptimeBorrowValue | ComptimeOpaqueValue:
        if type(value) is ComptimeOpaqueValue:
            if self._type_name(value.type_ref) == self._type_name(type_ref):
                return copy.deepcopy(value)
            raise CompileTimeError(
                f'Cannot convert opaque comptime value of type "{self._type_name(value.type_ref)}" to type "{self._type_name(type_ref)}".'
            )
        if type(value) is not ComptimeBorrowValue:
            raise CompileTimeError(f'Cannot convert {value!r} to type "{self._type_name(type_ref)}".')
        actual_mode = value.type_ref.borrow or ('inout' if value.mutable else 'in')
        if not borrow_mode_compatible(type_ref.borrow, actual_mode):
            raise CompileTimeError(f'Cannot convert borrow to type "{self._type_name(type_ref)}".')
        if not self._borrow_compatible(type_ref, value.type_ref):
            raise CompileTimeError(
                f'Cannot convert comptime borrow of type "{self._type_name(value.type_ref)}" to type "{self._type_name(type_ref)}".'
            )
        return value.as_type(type_ref)

    def _cast_comptime_array(
        self, value: object, type_ref: TypeReference, source_type: str | None = None
    ) -> ComptimeArrayValue:
        if type(value) is not ComptimeArrayValue:
            raise CompileTimeError(f'Cannot convert {value!r} to type "{self._type_name(type_ref)}".')
        expected_size = self._literal_array_size(type_ref)
        if len(value.elements) != expected_size:
            raise CompileTimeError(
                f'Cannot convert comptime array of length {len(value.elements)} to type "{self._type_name(type_ref)}".'
            )
        expected_element_type = self._element_type(type_ref)
        if self._type_name(value.element_type) != self._type_name(expected_element_type):
            raise CompileTimeError(f'Cannot convert comptime array to type "{self._type_name(type_ref)}".')
        return ComptimeArrayValue(
            copy.deepcopy(expected_element_type),
            [
                LiteralExpression(
                    self._cast_comptime(item.value, expected_element_type, source_type=item.type),
                    self._type_name(expected_element_type),
                )
                for item in value.elements
            ],
        )

    def _borrow_compatible(self, expected: TypeReference, actual: TypeReference) -> bool:
        if actual.borrow is None:
            return False
        if not borrow_mode_compatible(expected.borrow, actual.borrow):
            return False
        if self._type_name(self._element_type(expected)) in {'c_char', 'c_void'}:
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
            and self._literal_array_size(left) == self._literal_array_size(right)
        )

    def _same_element_type(self, left: TypeReference, right: TypeReference) -> bool:
        return self._type_name(self._element_type(left)) == self._type_name(self._element_type(right))

    def _literal_type_reference(self, value: LiteralExpression) -> TypeReference:
        if type(value.value) is ComptimeArrayValue:
            return self._array_type(value.value)
        if type(value.value) is ComptimeBorrowValue:
            return copy.deepcopy(value.value.type_ref)
        if type(value.value) is ComptimeOpaqueValue:
            return copy.deepcopy(value.value.type_ref)
        return TypeReference(value.type)

    def _array_type(self, value: ComptimeArrayValue) -> TypeReference:
        return TypeReference(
            value.element_type.name,
            copy.deepcopy(value.element_type.arguments),
            array_size=LiteralExpression(len(value.elements), 'i32'),
        )

    def _slice_type(self, element_type: TypeReference, borrow: str) -> TypeReference:
        return TypeReference(
            element_type.name,
            copy.deepcopy(element_type.arguments),
            is_slice=True,
            borrow=borrow,
        )

    def _element_borrow_type(self, element_type: TypeReference, borrow: str) -> TypeReference:
        return TypeReference(
            element_type.name,
            copy.deepcopy(element_type.arguments),
            borrow=borrow,
        )

    def _element_type(self, type_ref: TypeReference) -> TypeReference:
        return TypeReference(type_ref.name, copy.deepcopy(type_ref.arguments))

    def _is_array_type(self, type_ref: TypeReference) -> bool:
        return type_ref.array_size is not None

    def _is_slice_type(self, type_ref: TypeReference) -> bool:
        return type_ref.is_slice

    def _is_borrow_type(self, type_ref: TypeReference) -> bool:
        return type_ref.borrow is not None

    def _array_size_value(self, expression: Expression | None, scope: CompileTimeScope) -> int:
        if expression is None:
            raise CompileTimeError('Array type is missing a size.')
        value = self._eval_comptime_expression(expression, scope)
        if type(value.value) is bool or type(value.value) is not int:
            raise CompileTimeError(f'Array size must be an integer, got "{value.type}".')
        if value.value < 0:
            raise CompileTimeError(f'Array size must be non-negative, got {value.value}.')
        return value.value

    def _layout_of_type(self, type_ref: TypeReference, scope: CompileTimeScope) -> TypeLayout:
        if self._is_borrow_type(type_ref) and self._is_slice_type(type_ref):
            self._validate_layout_indirection_target(self._element_type(type_ref), scope)
            return self._slice_layout()
        if self._is_borrow_type(type_ref):
            self._validate_layout_indirection_target(self._element_type(type_ref), scope)
            return self._pointer_layout()
        if self._is_slice_type(type_ref):
            self._validate_layout_indirection_target(self._element_type(type_ref), scope)
            return self._slice_layout()
        if self._is_array_type(type_ref):
            count = self._array_size_value(type_ref.array_size, scope)
            element_layout = self._layout_of_type(self._element_type(type_ref), scope)
            return TypeLayout(element_layout.size * count, element_layout.align)

        type_name = self._type_name(type_ref)
        if type_name in {'void', 'type', 'c_void'}:
            raise CompileTimeError(f'Cannot query layout of type "{type_name}".')
        if type_name == 'c_char':
            return TypeLayout(1, 1)
        if type_name == 'str':
            return self._aggregate_layout([self._pointer_layout(), self._builtin_layout('i32')])
        if is_builtin_type(type_name):
            return self._builtin_layout(type_name)

        declaration = self.types.get(type_name)
        if declaration is None:
            raise CompileTimeError(f'Unknown type "{type_name}" in {self._type_name(type_ref)} layout query.')
        if declaration.extern:
            raise CompileTimeError(
                f'Cannot query layout of opaque extern type "{type_name}" by value; use an explicit borrow type.'
            )
        if declaration.language_item == 'MaybeUninit':
            assert declaration.language_item_type is not None
            return self._layout_of_type(declaration.language_item_type, scope)
        if declaration.parameters:
            raise CompileTimeError(f'Generic type "{type_name}" requires comptime arguments.')
        if not declaration.fields:
            return TypeLayout(1, 1)

        field_layouts = [
            self._layout_of_type(self._apply_type_reference(field.type, scope), scope)
            for field in declaration.fields
        ]
        return self._aggregate_layout(field_layouts)

    def _validate_layout_indirection_target(
        self, type_ref: TypeReference, scope: CompileTimeScope
    ) -> None:
        target = self._apply_type_reference(type_ref, scope)
        type_name = self._type_name(target)
        if type_name in {'void', 'type'}:
            raise CompileTimeError(f'Cannot query layout of type "{type_name}".')
        if type_name in {'str', 'c_char', 'c_void'} or is_builtin_type(type_name):
            return

        declaration = self.types.get(type_name)
        if declaration is None:
            raise CompileTimeError(f'Unknown type "{type_name}" in {self._type_name(type_ref)} layout query.')
        if declaration.parameters:
            raise CompileTimeError(f'Generic type "{type_name}" requires comptime arguments.')

    def _builtin_layout(self, type_name: str) -> TypeLayout:
        spec = BUILTIN_TYPE_SPECS[type_name]
        size = max(1, (spec.bits + 7) // 8)
        return TypeLayout(size, size)

    def _pointer_layout(self) -> TypeLayout:
        return self._builtin_layout('usize')

    def _slice_layout(self) -> TypeLayout:
        return self._aggregate_layout([self._pointer_layout(), self._builtin_layout('i32')])

    def _aggregate_layout(self, fields: Iterable[TypeLayout]) -> TypeLayout:
        offset = 0
        aggregate_align = 1
        has_fields = False
        for field in fields:
            has_fields = True
            offset = self._round_up(offset, field.align)
            offset += field.size
            aggregate_align = max(aggregate_align, field.align)
        if not has_fields:
            return TypeLayout(1, 1)
        return TypeLayout(self._round_up(offset, aggregate_align), aggregate_align)

    def _round_up(self, value: int, alignment: int) -> int:
        if alignment <= 0:
            raise CompileTimeError(f'Invalid alignment {alignment}.')
        return ((value + alignment - 1) // alignment) * alignment

    def _literal_array_size(self, type_ref: TypeReference) -> int:
        if type(type_ref.array_size) is not LiteralExpression:
            raise CompileTimeError(
                f'Array type "{self._type_name(type_ref)}" needs a literal size in this context.'
            )
        return int(type_ref.array_size.value)

    def _expression_has_comptime_root(self, expression: Expression, scope: CompileTimeScope) -> bool:
        if type(expression) is VariableExpression:
            return scope.contains_root(expression.name)
        if type(expression) is IndexExpression:
            return self._expression_has_comptime_root(expression.target, scope)
        if type(expression) is SliceExpression:
            return self._expression_has_comptime_root(expression.target, scope)
        if type(expression) is BorrowExpression:
            return self._expression_has_comptime_root(expression.expr, scope)
        if type(expression) is MoveExpression:
            return self._expression_has_comptime_root(expression.expr, scope)
        if type(expression) is CompositeExpression:
            return (
                self._expression_has_comptime_root(expression.left, scope)
                or self._expression_has_comptime_root(expression.right, scope)
            )
        if type(expression) is FunctionCall:
            return any(self._expression_has_comptime_root(argument, scope) for argument in expression.parameters)
        return False

    def _infer_all_raises(self, ast: list[Statement]) -> None:
        functions = {
            node.name: node
            for node in ast
            if type(node) is FunctionDeclaration
        }
        types = {
            node.name: node
            for node in ast
            if type(node) is TypeDeclaration
        }
        methods = [
            (type_decl, method)
            for type_decl in types.values()
            for method in type_decl.methods
        ]

        for declaration in [*functions.values(), *(method for _, method in methods)]:
            if declaration.extern and declaration.raises_inferred:
                raise CompileTimeError(
                    f'Extern function "{declaration.name}" cannot use inferred raises.'
                )

        changed = True
        while changed:
            changed = False
            for declaration in functions.values():
                if not declaration.raises_inferred:
                    continue
                env = {parameter.name: parameter.type for parameter in declaration.parameters}
                inferred = self._infer_raises_from_statements(
                    declaration.body, env, functions, types
                )
                if self._raise_names(declaration.raises) != self._raise_names(inferred):
                    declaration.raises = inferred
                    changed = True

            for type_decl, method in methods:
                if not method.raises_inferred:
                    continue
                env = {'self': self._method_self_type(type_decl, method)}
                env.update({parameter.name: parameter.type for parameter in method.parameters})
                inferred = self._infer_raises_from_statements(
                    method.body, env, functions, types
                )
                if self._raise_names(method.raises) != self._raise_names(inferred):
                    method.raises = inferred
                    changed = True

        for declaration in [*functions.values(), *(method for _, method in methods)]:
            if declaration.raises_inferred:
                declaration.raises_inferred = False

    def _method_self_type(
        self, type_decl: TypeDeclaration, method: FunctionDeclaration
    ) -> TypeReference:
        if method.self_parameter is None:
            raise CompileTimeError(f'Method "{type_decl.name}.{method.name}" must declare an explicit self parameter.')
        parameter_type = copy.deepcopy(method.self_parameter.type)
        if parameter_type.name == 'self':
            parameter_type.name = type_decl.name
        return parameter_type

    def _infer_raises_from_statements(
        self,
        statements: Iterable[Statement],
        env: dict[str, TypeReference],
        functions: dict[str, FunctionDeclaration],
        types: dict[str, TypeDeclaration],
        rethrow_errors: Iterable[TypeReference] = (),
    ) -> list[TypeReference]:
        errors: list[TypeReference] = []
        for statement in statements:
            if type(statement) is VariableDeclaration:
                if statement.expr is not None:
                    self._infer_raises_from_expression(
                        statement.expr, env, functions, types, errors
                    )
                env[statement.name] = statement.type
                if statement.constructor_args:
                    self._infer_raises_from_call(
                        FunctionCall(f'{statement.name}.init', statement.constructor_args),
                        env,
                        functions,
                        types,
                        errors,
                    )
            elif type(statement) is Assignment:
                if type(statement.name) is not str:
                    self._infer_raises_from_expression(
                        statement.name, env, functions, types, errors
                    )
                self._infer_raises_from_expression(
                    statement.expr, env, functions, types, errors
                )
            elif type(statement) is FunctionCall:
                self._infer_raises_from_call(statement, env, functions, types, errors)
            elif type(statement) is Raise:
                self._infer_raises_from_expression(
                    statement.expr, env, functions, types, errors
                )
                error_type = self._infer_expression_type(statement.expr, env, functions, types)
                if error_type is not None:
                    self._add_inferred_error(errors, self._type_name(error_type))
            elif type(statement) is Rethrow:
                self._merge_inferred_errors(errors, rethrow_errors)
            elif type(statement) is Print:
                if statement.expr is not None:
                    self._infer_raises_from_expression(
                        statement.expr, env, functions, types, errors
                    )
            elif type(statement) is Return:
                if statement.expr is not None:
                    self._infer_raises_from_expression(
                        statement.expr, env, functions, types, errors
                    )
            elif type(statement) is If:
                for branch in statement.branches:
                    self._infer_raises_from_expression(
                        branch.condition, env, functions, types, errors
                    )
                    self._merge_inferred_errors(
                        errors,
                        self._infer_raises_from_statements(
                            branch.body, dict(env), functions, types, rethrow_errors
                        ),
                    )
                if statement.else_body is not None:
                    self._merge_inferred_errors(
                        errors,
                        self._infer_raises_from_statements(
                            statement.else_body, dict(env), functions, types, rethrow_errors
                        ),
                    )
            elif type(statement) is While:
                self._infer_raises_from_expression(
                    statement.condition, env, functions, types, errors
                )
                self._merge_inferred_errors(
                    errors,
                    self._infer_raises_from_statements(
                        statement.body, dict(env), functions, types, rethrow_errors
                    ),
                )
            elif type(statement) is For:
                loop_env = dict(env)
                if statement.initializer is not None:
                    self._merge_inferred_errors(
                        errors,
                        self._infer_raises_from_statements(
                            [statement.initializer], loop_env, functions, types, rethrow_errors
                        ),
                    )
                if statement.condition is not None:
                    self._infer_raises_from_expression(
                        statement.condition, loop_env, functions, types, errors
                    )
                if statement.update is not None:
                    self._merge_inferred_errors(
                        errors,
                        self._infer_raises_from_statements(
                            [statement.update], loop_env, functions, types, rethrow_errors
                        ),
                    )
                self._merge_inferred_errors(
                    errors,
                    self._infer_raises_from_statements(
                        statement.body, dict(loop_env), functions, types, rethrow_errors
                    ),
                )
            elif type(statement) is Try:
                try_errors = self._infer_raises_from_statements(
                    statement.body, dict(env), functions, types, rethrow_errors
                )
                caught_error_names = {
                    self._type_name(catch.error_type) for catch in statement.catches
                }
                self._merge_inferred_errors(
                    errors,
                    [
                        error for error in try_errors
                        if self._type_name(error) not in caught_error_names
                    ],
                )
                for catch in statement.catches:
                    catch_env = dict(env)
                    if catch.name is not None:
                        catch_env[catch.name] = catch.error_type
                    self._merge_inferred_errors(
                        errors,
                        self._infer_raises_from_statements(
                            catch.body,
                            catch_env,
                            functions,
                            types,
                            [copy.deepcopy(catch.error_type)],
                        ),
                    )
        return errors

    def _infer_expression_type(
        self,
        expression: Expression,
        env: dict[str, TypeReference],
        functions: dict[str, FunctionDeclaration],
        types: dict[str, TypeDeclaration],
    ) -> TypeReference | None:
        if type(expression) is LiteralExpression:
            return TypeReference(expression.type)
        if type(expression) is VariableExpression:
            return self._infer_name_type(expression.name, env, types)
        if type(expression) is FunctionCall:
            if expression.function_name == 'len':
                return TypeReference('i32')
            if expression.function_name in {'sizeof', 'alignof'}:
                return TypeReference('usize')
            if is_builtin_type(expression.function_name):
                return TypeReference(expression.function_name)
            if '.' in expression.function_name:
                receiver_name, method_name = expression.function_name.rsplit('.', 1)
                receiver_type = self._infer_name_type(receiver_name, env, types)
                if receiver_type is None:
                    return None
                type_decl = types.get(self._type_name(self._element_type(receiver_type)))
                if type_decl is None:
                    return None
                method = next((method for method in type_decl.methods if method.name == method_name), None)
                return None if method is None else method.return_type
            declaration = functions.get(expression.function_name)
            return None if declaration is None else declaration.return_type
        if type(expression) is IndexExpression:
            target_type = self._infer_expression_type(expression.target, env, functions, types)
            return None if target_type is None else self._element_type(target_type)
        if type(expression) is SliceExpression:
            target_type = self._infer_expression_type(expression.target, env, functions, types)
            return None if target_type is None else TypeReference(self._element_type(target_type).name, is_slice=True)
        if type(expression) is BorrowExpression:
            inner_type = self._infer_expression_type(expression.expr, env, functions, types)
            if inner_type is None:
                return None
            borrowed = copy.deepcopy(inner_type)
            borrowed.borrow = expression.mode
            return borrowed
        if type(expression) is MoveExpression:
            return self._infer_expression_type(expression.expr, env, functions, types)
        if type(expression) is CompositeExpression:
            if expression.operator in {'==', '!=', '<', '>', '<=', '>='}:
                return TypeReference('bool')
            return self._infer_expression_type(expression.left, env, functions, types)
        if type(expression) is FormattedStringExpression:
            return TypeReference('str')
        if type(expression) is StructLiteralExpression:
            return expression.type_ref
        return None

    def _infer_raises_from_expression(
        self,
        expression: Expression,
        env: dict[str, TypeReference],
        functions: dict[str, FunctionDeclaration],
        types: dict[str, TypeDeclaration],
        errors: list[TypeReference],
    ) -> None:
        if type(expression) is FunctionCall:
            self._infer_raises_from_call(expression, env, functions, types, errors)
        elif type(expression) is CompositeExpression:
            self._infer_raises_from_expression(expression.left, env, functions, types, errors)
            self._infer_raises_from_expression(expression.right, env, functions, types, errors)
        elif type(expression) is FormattedStringExpression:
            for part in expression.parts:
                if isinstance(part, Expression):
                    self._infer_raises_from_expression(part, env, functions, types, errors)
        elif type(expression) is StructLiteralExpression:
            for field in expression.fields:
                self._infer_raises_from_expression(field.expr, env, functions, types, errors)
        elif type(expression) is BorrowExpression:
            self._infer_raises_from_expression(expression.expr, env, functions, types, errors)
        elif type(expression) is MoveExpression:
            self._infer_raises_from_expression(expression.expr, env, functions, types, errors)
        elif type(expression) is IndexExpression:
            self._infer_raises_from_expression(expression.target, env, functions, types, errors)
            self._infer_raises_from_expression(expression.index, env, functions, types, errors)
        elif type(expression) is SliceExpression:
            self._infer_raises_from_expression(expression.target, env, functions, types, errors)
            if expression.start is not None:
                self._infer_raises_from_expression(expression.start, env, functions, types, errors)
            if expression.end is not None:
                self._infer_raises_from_expression(expression.end, env, functions, types, errors)

    def _infer_raises_from_call(
        self,
        call: FunctionCall,
        env: dict[str, TypeReference],
        functions: dict[str, FunctionDeclaration],
        types: dict[str, TypeDeclaration],
        errors: list[TypeReference],
    ) -> None:
        for argument in call.parameters:
            self._infer_raises_from_expression(argument, env, functions, types, errors)

        if call.function_name in {'sizeof', 'alignof', 'len'} or is_builtin_type(call.function_name):
            return
        if '.' in call.function_name:
            receiver_name, method_name = call.function_name.rsplit('.', 1)
            receiver_type = self._infer_name_type(receiver_name, env, types)
            if receiver_type is None:
                return
            type_decl = types.get(self._type_name(self._element_type(receiver_type)))
            if type_decl is None:
                return
            method = next(
                (
                    method for method in type_decl.methods
                    if method.name == method_name
                    or (
                        call.interface_name == method.interface_name
                        and method.source_name == method_name
                    )
                ),
                None,
            )
            if method is not None:
                self._merge_inferred_errors(errors, method.raises)
            return

        declaration = functions.get(call.function_name)
        if declaration is not None:
            self._merge_inferred_errors(errors, declaration.raises)

    def _infer_name_type(
        self,
        name: str,
        env: dict[str, TypeReference],
        types: dict[str, TypeDeclaration],
    ) -> TypeReference | None:
        parts = name.split('.')
        if not parts:
            return None
        current = env.get(parts[0])
        if current is None:
            return None
        for field_name in parts[1:]:
            type_decl = types.get(self._type_name(self._element_type(current)))
            if type_decl is None:
                return None
            field = next((field for field in type_decl.fields if field.name == field_name), None)
            if field is None:
                return None
            current = field.type
        return current

    def _merge_inferred_errors(
        self, target: list[TypeReference], source: Iterable[TypeReference]
    ) -> None:
        for error in source:
            self._add_inferred_error(target, self._type_name(error))

    def _add_inferred_error(self, target: list[TypeReference], name: str) -> None:
        if name not in self._raise_names(target):
            target.append(TypeReference(name))

    def _raise_names(self, raises: Iterable[TypeReference]) -> list[str]:
        return [self._type_name(error) for error in raises]

    def _runtime_variable_declaration(
        self, declaration: VariableDeclaration, scope: CompileTimeScope
    ) -> VariableDeclaration:
        runtime_type = self._apply_type_reference(declaration.type, scope)
        if self._is_type_type(runtime_type):
            raise CompileTimeError(f'Type variable "{declaration.name}" must be marked comptime.')
        if self._is_void_type(runtime_type):
            raise CompileTimeError(f'Variable "{declaration.name}" cannot have type "void".')
        return VariableDeclaration(
            declaration.name,
            runtime_type,
            declaration.expr,
            constructor_args=copy.deepcopy(declaration.constructor_args),
            public=declaration.public,
            passing_mode=declaration.passing_mode,
        )

    def _runtime_type_declaration(self, declaration: TypeDeclaration, scope: CompileTimeScope) -> TypeDeclaration:
        return TypeDeclaration(
            declaration.name,
            [self._runtime_variable_declaration(field, scope) for field in declaration.fields],
            methods=[
                self._runtime_method_declaration(
                    method,
                    scope,
                    owner_type=TypeReference(declaration.name),
                    owner_source_name=declaration.name,
                )
                for method in declaration.methods
            ],
            public=declaration.public,
        )

    def _runtime_method_declaration(
        self,
        declaration: FunctionDeclaration,
        scope: CompileTimeScope,
        owner_type: TypeReference | None = None,
        owner_source_name: str | None = None,
    ) -> FunctionDeclaration:
        if declaration.comptime:
            raise CompileTimeFeatureNotImplemented('Comptime methods are not implemented yet.')
        if any(parameter.comptime for parameter in declaration.parameters):
            raise CompileTimeFeatureNotImplemented(
                'Comptime method parameters are not implemented yet.'
            )
        if declaration.name in {'init', 'deinit'} and not self._is_void_type(declaration.return_type):
            raise CompileTimeError(f'Method "{declaration.name}" must have type "void".')
        if declaration.name == 'deinit' and declaration.parameters:
            raise CompileTimeError('Method "deinit" cannot have parameters.')
        if declaration.name == 'deinit' and (declaration.raises or declaration.raises_inferred):
            raise CompileTimeError('Method "deinit" cannot raise errors.')

        body_scope = CompileTimeScope(scope)
        return_type = self._apply_type_reference(declaration.return_type, body_scope)
        body = self._apply_statements(declaration.body, body_scope)
        self._validate_returns(declaration.name, return_type, body)
        return FunctionDeclaration(
            declaration.name,
            [self._runtime_variable_declaration(parameter, scope) for parameter in declaration.parameters],
            body,
            return_type=return_type,
            raises=self._apply_raises_clause(declaration.raises, body_scope),
            raises_inferred=declaration.raises_inferred,
            self_parameter=self._runtime_method_self_parameter(
                declaration.self_parameter, scope, owner_type, owner_source_name
            ),
            public=declaration.public,
            module_name=declaration.module_name,
            source_name=declaration.source_name,
            imports=list(declaration.imports),
            qualified_imports=list(declaration.qualified_imports),
            interface_name=declaration.interface_name,
            synthetic=declaration.synthetic,
            unsafe=declaration.unsafe,
            span=declaration.span,
        )

    def _runtime_method_self_parameter(
        self,
        parameter: VariableDeclaration | None,
        scope: CompileTimeScope,
        owner_type: TypeReference | None,
        owner_source_name: str | None,
    ) -> VariableDeclaration | None:
        if parameter is None:
            raise CompileTimeError('Method declarations must declare an explicit self parameter.')
        parameter = copy.deepcopy(parameter)
        if (
            owner_type is not None
            and owner_source_name is not None
            and parameter.type.name in {'self', owner_source_name}
            and not parameter.type.arguments
            and parameter.type.array_size is None
            and not parameter.type.is_slice
        ):
            parameter.type.name = owner_type.name
            parameter.type.arguments = list(owner_type.arguments)
        return self._runtime_variable_declaration(parameter, scope)

    def _validate_returns(
        self, function_name: str, return_type: TypeReference, body: Iterable[Statement]
    ) -> None:
        for statement in body:
            if type(statement) is Return:
                if statement.expr is None and not self._is_void_type(return_type):
                    raise CompileTimeError(
                        f'Function "{function_name}" must return a value of type "{self._type_name(return_type)}".'
                    )
                if statement.expr is not None and self._is_void_type(return_type):
                    raise CompileTimeError(
                        f'Void function "{function_name}" cannot return a value.'
                    )
            elif type(statement) is If:
                for branch in statement.branches:
                    self._validate_returns(function_name, return_type, branch.body)
                if statement.else_body is not None:
                    self._validate_returns(function_name, return_type, statement.else_body)
            elif type(statement) is While:
                self._validate_returns(function_name, return_type, statement.body)
            elif type(statement) is For:
                self._validate_returns(function_name, return_type, statement.body)
            elif type(statement) is Try:
                self._validate_returns(function_name, return_type, statement.body)
                for catch in statement.catches:
                    self._validate_returns(function_name, return_type, catch.body)

    def _reject_unsupported_comptime_type_features(self, declaration: TypeDeclaration) -> None:
        comptime_fields = [field.name for field in declaration.fields if field.comptime]
        if comptime_fields:
            names = ', '.join(comptime_fields)
            raise CompileTimeFeatureNotImplemented(
                f'Comptime fields are not implemented yet: {names}.'
            )

        comptime_methods = [method.name for method in declaration.methods if method.comptime]
        if comptime_methods:
            names = ', '.join(comptime_methods)
            raise CompileTimeFeatureNotImplemented(
                f'Comptime methods are not implemented yet: {names}.'
            )

        methods_with_comptime_parameters = [
            method.name
            for method in declaration.methods
            if any(parameter.comptime for parameter in method.parameters)
        ]
        if methods_with_comptime_parameters:
            names = ', '.join(methods_with_comptime_parameters)
            raise CompileTimeFeatureNotImplemented(
                f'Comptime method parameters are not implemented yet: {names}.'
            )

    def _type_declaration_for(self, type_ref: TypeReference) -> TypeDeclaration:
        type_name = self._type_name(type_ref)
        declaration = self.types.get(type_name)
        if declaration is None or declaration.parameters:
            raise CompileTimeFeatureNotImplemented(
                f'Comptime type "{type_name}" is not implemented yet.'
            )
        return declaration

    def _method_declaration_for(
        self, type_decl: TypeDeclaration, method_name: str
    ) -> FunctionDeclaration:
        method = next((method for method in type_decl.methods if method.name == method_name), None)
        if method is None:
            raise CompileTimeError(f'Type "{type_decl.name}" has no method "{method_name}".')
        return method

    def _type_name(self, type_ref: TypeReference) -> str:
        if type_ref.arguments:
            raise CompileTimeError(f'Unresolved generic type "{type_ref.name}".')
        return self._type_key(type_ref)

    def _expression_label(self, expression: Expression) -> str:
        if type(expression) is VariableExpression:
            return expression.name
        if type(expression) is LiteralExpression:
            return str(expression.value)
        if type(expression) is IndexExpression:
            return f'{self._expression_label(expression.target)}[{self._expression_label(expression.index)}]'
        if type(expression) is SliceExpression:
            start = '' if expression.start is None else self._expression_label(expression.start)
            end = '' if expression.end is None else self._expression_label(expression.end)
            return f'{self._expression_label(expression.target)}[{start}..{end}]'
        if type(expression) is BorrowExpression:
            return f'&{expression.mode} {self._expression_label(expression.expr)}'
        if type(expression) is MoveExpression:
            return f'move {self._expression_label(expression.expr)}'
        if type(expression) is StructLiteralExpression:
            fields = ', '.join(
                f'{field.name} = {self._expression_label(field.expr)}'
                for field in expression.fields
            )
            return f'{self._type_name(expression.type_ref)} {{{fields}}}'
        return '<expr>'

    def _is_type_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'type'

    def _is_void_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'void'

    def _is_str_type(self, type_ref: TypeReference) -> bool:
        return self._type_name(type_ref) == 'str'

    def _is_struct_type(self, type_ref: TypeReference) -> bool:
        type_name = self._type_name(type_ref)
        declaration = self.types.get(type_name)
        return declaration is not None and not declaration.parameters and not declaration.extern

    def _is_opaque_comptime_type(self, type_ref: TypeReference) -> bool:
        declaration = self.types.get(type_ref.name)
        return declaration is not None and declaration.extern and type_ref.borrow is not None


class CompileTimeExecutor(ExecutionEngine[LiteralExpression, CompileTimeScope]):
    LOOP_ITERATION_LIMIT = CompileTimePass.LOOP_ITERATION_LIMIT

    def __init__(self, compile_time_pass: CompileTimePass) -> None:
        self.compile_time_pass = compile_time_pass
        self.caught_errors: list[ComptimeRaisedError] = []

    def _allows_comptime_statement(self, statement: Statement, scope: CompileTimeScope) -> bool:
        return True

    def _child_scope(self, scope: CompileTimeScope) -> CompileTimeScope:
        return CompileTimeScope(scope)

    def _execute_variable_declaration(
        self, declaration: VariableDeclaration, scope: CompileTimeScope
    ) -> None:
        runtime_type = self.compile_time_pass._apply_type_reference(declaration.type, scope)
        if declaration.expr is None:
            value = self.compile_time_pass._default_literal(runtime_type, scope)
        else:
            value = self._eval_expression(declaration.expr, scope)
            value = LiteralExpression(
                self.compile_time_pass._cast_comptime(
                    value.value, runtime_type, source_type=value.type
                ),
                self.compile_time_pass._type_name(runtime_type),
            )
        scope.declare(declaration.name, value)
        if declaration.constructor_args:
            self.compile_time_pass._eval_comptime_method_call(
                FunctionCall(f'{declaration.name}.init', declaration.constructor_args),
                scope,
            )

    def _execute_assignment(self, assignment: Assignment, scope: CompileTimeScope) -> None:
        value = self._eval_expression(assignment.expr, scope)
        if type(assignment.name) is str:
            current = scope.get(assignment.name)
            if current is None:
                raise CompileTimeError(
                    f'Comptime assignment targets unknown variable "{assignment.name}".'
                )

            current_type = self.compile_time_pass._literal_type_reference(current)
            scope.assign(
                assignment.name,
                LiteralExpression(
                    self.compile_time_pass._cast_comptime(
                        value.value, current_type, source_type=value.type
                    ),
                    self.compile_time_pass._type_name(current_type),
                ),
            )
            return

        target = self._assignment_cell(assignment.name, scope)
        target_type = self.compile_time_pass._literal_type_reference(target)
        target.value = self.compile_time_pass._cast_comptime(
            value.value, target_type, source_type=value.type
        )
        target.type = self.compile_time_pass._type_name(target_type)

    def _execute_print(self, statement: Print, scope: CompileTimeScope) -> None:
        self.compile_time_pass._eval_comptime_print(statement, scope)

    def _execute_type_declaration(
        self, declaration: TypeDeclaration, scope: CompileTimeScope
    ) -> None:
        raise CompileTimeFeatureNotImplemented(
            'Comptime execution of TypeDeclaration statements is not implemented yet.'
        )
    def _execute_function_declaration(
        self, declaration: FunctionDeclaration, scope: CompileTimeScope
    ) -> None:
        raise CompileTimeFeatureNotImplemented(
            'Comptime execution of FunctionDeclaration statements is not implemented yet.'
        )

    def _execute_raise(self, statement: Raise, scope: CompileTimeScope) -> None:
        payload = self._eval_expression(statement.expr, scope)
        raise ComptimeRaisedError(payload.type, copy.deepcopy(payload))

    def _execute_rethrow(self, statement: Rethrow, scope: CompileTimeScope) -> None:
        if not self.caught_errors:
            raise CompileTimeError('rethrow used outside of a catch block.')
        raise self.caught_errors[-1]

    def _execute_try(
        self, statement: Try, scope: CompileTimeScope, allow_return: bool
    ):
        try:
            return self._execute_block(statement.body, scope, allow_return)
        except ComptimeRaisedError as err:
            for catch in statement.catches:
                if self._catch_matches_error(catch.error_type.name, err):
                    catch_scope = self._child_scope(scope)
                    if catch.name is not None:
                        catch_scope.declare(catch.name, copy.deepcopy(err.payload))
                    self.caught_errors.append(err)
                    try:
                        return self._execute_statements(catch.body, catch_scope, allow_return)
                    finally:
                        self.caught_errors.pop()
            raise CompileTimeError(f'Unhandled comptime error "{err.type_name}".') from err

    def _catch_matches_error(
        self, error_name: str, err: ComptimeRaisedError
    ) -> bool:
        return error_name == err.type_name

    def _eval_literal(
        self, literal: LiteralExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        return copy.deepcopy(literal)

    def _eval_variable(
        self, variable: VariableExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        value = scope.get(variable.name)
        if value is not None:
            if value.type == 'type':
                raise CompileTimeError(f'Cannot use type "{variable.name}" as a value.')
            return copy.deepcopy(value)
        if scope.contains_root(variable.name):
            raise CompileTimeError(
                f'Cannot read comptime value "{variable.name}" as a value.'
            )
        raise CompileTimeError(f'Comptime expression references runtime name "{variable.name}".')

    def _eval_function_call(
        self, function_call: FunctionCall, scope: CompileTimeScope
    ) -> LiteralExpression:
        if function_call.function_name == 'len':
            return self.compile_time_pass._eval_comptime_len_function_call(function_call, scope)
        return self.compile_time_pass._eval_comptime_method_call(function_call, scope)

    def _eval_formatted_string(
        self, expression: FormattedStringExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        parts: list[str] = []
        for part in expression.parts:
            if type(part) is str:
                parts.append(part)
            else:
                parts.append(
                    self.compile_time_pass._format_comptime_value(
                        self._eval_expression(part, scope)
                    )
                )
        return LiteralExpression(''.join(parts), 'str')

    def _eval_borrow(
        self, expression: BorrowExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        mutable = borrow_mode_can_write(expression.mode)
        if type(expression.expr) is SliceExpression:
            return self._slice_borrow_from_expression(expression.expr, scope, expression.mode)
        if type(expression.expr) is IndexExpression:
            return self._index_borrow_from_expression(expression.expr, scope, expression.mode)
        if type(expression.expr) is not VariableExpression:
            raise CompileTimeError('Cannot borrow a comptime temporary value.')

        target = scope.get(expression.expr.name)
        if target is None:
            raise CompileTimeError(
                f'Comptime borrow references runtime name "{expression.expr.name}".'
            )
        if type(target.value) is ComptimeBorrowValue:
            if mutable and not target.value.mutable:
                raise CompileTimeError('Cannot create a writable borrow from a read-only borrow.')
            type_ref = copy.deepcopy(target.value.type_ref)
            type_ref.borrow = expression.mode
            return LiteralExpression(target.value.as_type(type_ref), self.compile_time_pass._type_name(type_ref))
        if type(target.value) is ComptimeArrayValue:
            type_ref = self.compile_time_pass._array_type(target.value)
            type_ref.borrow = expression.mode
            return LiteralExpression(
                ComptimeBorrowValue(
                    copy.deepcopy(type_ref),
                    mutable,
                    array=target.value,
                    start=0,
                    length=len(target.value.elements),
                ),
                self.compile_time_pass._type_name(type_ref),
            )

        target_type = self.compile_time_pass._literal_type_reference(target)
        target_type.borrow = expression.mode
        return LiteralExpression(
            ComptimeBorrowValue(copy.deepcopy(target_type), mutable, cell=target),
            self.compile_time_pass._type_name(target_type),
        )

    def _eval_index(
        self, expression: IndexExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        target = self._storage_value(expression.target, scope)
        index = self._index_value(expression.index, scope)
        return copy.deepcopy(self._indexed_cell(target, index))

    def _eval_slice(
        self, expression: SliceExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        return self._slice_borrow_from_expression(expression, scope, 'in')

    def _eval_struct_literal(
        self, expression: StructLiteralExpression, scope: CompileTimeScope
    ) -> LiteralExpression:
        type_ref = self.compile_time_pass._apply_type_reference(expression.type_ref, scope)
        type_decl = self.compile_time_pass._type_declaration_for(type_ref)
        initializers: dict[str, Expression] = {}
        for field in expression.fields:
            if field.name in initializers:
                raise CompileTimeError(
                    f'Struct literal for "{self.compile_time_pass._type_name(type_ref)}" has duplicate field "{field.name}".'
                )
            initializers[field.name] = field.expr
        fields: dict[str, LiteralExpression] = {}
        for field in type_decl.fields:
            initializer = initializers.get(field.name)
            if initializer is None:
                raise CompileTimeError(
                    f'Struct literal for "{self.compile_time_pass._type_name(type_ref)}" is missing field "{field.name}".'
                )
            field_type = self.compile_time_pass._apply_type_reference(field.type, scope)
            value = self._eval_expression(initializer, scope)
            fields[field.name] = LiteralExpression(
                self.compile_time_pass._cast_comptime(
                    value.value, field_type, source_type=value.type
                ),
                self.compile_time_pass._type_name(field_type),
            )

        extras = [name for name in initializers if name not in fields]
        if extras:
            raise CompileTimeError(
                f'Type "{self.compile_time_pass._type_name(type_ref)}" has no field "{extras[0]}".'
            )
        return LiteralExpression(
            ComptimeStructValue(copy.deepcopy(type_ref), fields),
            self.compile_time_pass._type_name(type_ref),
        )

    def _assignment_cell(self, target: Expression, scope: CompileTimeScope) -> LiteralExpression:
        if type(target) is IndexExpression:
            indexed = self._storage_value(target.target, scope)
            index = self._index_value(target.index, scope)
            return self._indexed_cell(indexed, index, require_mutable=True)
        if type(target) is VariableExpression:
            value = scope.get(target.name)
            if value is None:
                raise CompileTimeError(f'Unknown comptime assignment target "{target.name}".')
            return value
        raise CompileTimeError(f'Unsupported comptime assignment target "{type(target).__name__}".')

    def _storage_value(self, expression: Expression, scope: CompileTimeScope) -> LiteralExpression:
        if type(expression) is VariableExpression:
            value = scope.get(expression.name)
            if value is None:
                raise CompileTimeError(f'Comptime expression references runtime name "{expression.name}".')
            return value
        if type(expression) is IndexExpression:
            indexed = self._storage_value(expression.target, scope)
            index = self._index_value(expression.index, scope)
            return self._indexed_cell(indexed, index)
        return self._eval_expression(expression, scope)

    def _indexed_cell(
        self, target: LiteralExpression, index: int, require_mutable: bool = False
    ) -> LiteralExpression:
        if type(target.value) is ComptimeArrayValue:
            self._check_array_index(target.value, index)
            return target.value.elements[index]
        if type(target.value) is ComptimeBorrowValue:
            if require_mutable and not target.value.mutable:
                raise CompileTimeError('Cannot assign through a read-only comptime borrow.')
            return target.value.element_cell(index)
        raise CompileTimeError(f'Cannot index comptime value of type "{target.type}".')

    def _slice_borrow_from_expression(
        self, expression: SliceExpression, scope: CompileTimeScope, mode: str
    ) -> LiteralExpression:
        mutable = borrow_mode_can_write(mode)
        target = self._storage_value(expression.target, scope)
        if type(target.value) is ComptimeArrayValue:
            array = target.value
            base_start = 0
            length = len(array.elements)
        elif type(target.value) is ComptimeBorrowValue and target.value.array is not None:
            if mutable and not target.value.mutable:
                raise CompileTimeError('Cannot create a writable slice from a read-only comptime borrow.')
            array = target.value.array
            base_start = target.value.start
            length = target.value.window_length()
        else:
            raise CompileTimeError(f'Cannot slice comptime value of type "{target.type}".')

        start = 0 if expression.start is None else self._index_value(expression.start, scope)
        end = length if expression.end is None else self._index_value(expression.end, scope)
        if start < 0 or end < start or end > length:
            raise CompileTimeError(f'Invalid comptime slice range {start}..{end} for length {length}.')

        type_ref = self.compile_time_pass._slice_type(
            array.element_type, mode
        )
        return LiteralExpression(
            ComptimeBorrowValue(
                copy.deepcopy(type_ref),
                mutable,
                array=array,
                start=base_start + start,
                length=end - start,
            ),
            self.compile_time_pass._type_name(type_ref),
        )

    def _index_borrow_from_expression(
        self, expression: IndexExpression, scope: CompileTimeScope, mode: str
    ) -> LiteralExpression:
        mutable = borrow_mode_can_write(mode)
        target = self._storage_value(expression.target, scope)
        index = self._index_value(expression.index, scope)
        if type(target.value) is ComptimeArrayValue:
            self._check_array_index(target.value, index)
            type_ref = self.compile_time_pass._element_borrow_type(target.value.element_type, mode)
            return LiteralExpression(
                ComptimeBorrowValue(
                    copy.deepcopy(type_ref),
                    mutable,
                    array=target.value,
                    start=index,
                    length=None,
                ),
                self.compile_time_pass._type_name(type_ref),
            )
        if type(target.value) is ComptimeBorrowValue:
            if mutable and not target.value.mutable:
                raise CompileTimeError('Cannot create a writable borrow from a read-only comptime borrow.')
            cell = target.value.element_cell(index)
            if target.value.array is not None:
                type_ref = self.compile_time_pass._element_borrow_type(
                    target.value.array.element_type, mode
                )
                return LiteralExpression(
                    ComptimeBorrowValue(
                        copy.deepcopy(type_ref),
                        mutable,
                        array=target.value.array,
                        start=target.value.start + index,
                        length=None,
                    ),
                    self.compile_time_pass._type_name(type_ref),
                )
            type_ref = self.compile_time_pass._literal_type_reference(cell)
            type_ref.borrow = mode
            return LiteralExpression(
                ComptimeBorrowValue(copy.deepcopy(type_ref), mutable, cell=cell),
                self.compile_time_pass._type_name(type_ref),
            )
        raise CompileTimeError(f'Cannot index comptime value of type "{target.type}".')

    def _index_value(self, expression: Expression, scope: CompileTimeScope) -> int:
        value = self._eval_expression(expression, scope)
        if type(value.value) is bool or type(value.value) is not int:
            raise CompileTimeError(f'Comptime index must be an integer, got "{value.type}".')
        return value.value

    def _check_array_index(self, array: ComptimeArrayValue, index: int) -> None:
        if index < 0 or index >= len(array.elements):
            raise CompileTimeError(
                f'Comptime array index {index} is out of bounds for length {len(array.elements)}.'
            )

    def _eval_composite_operator(
        self, operator: str, left: LiteralExpression, right: LiteralExpression
    ) -> LiteralExpression:
        if operator in {'+', '-', '*', '/', '%'}:
            merged_type = self.compile_time_pass._merge_types(left, right)
            if not is_numeric_type(merged_type) or is_raw_byte_type(merged_type):
                raise CompileTimeError(
                    f'Operator "{operator}" is not implemented for comptime values of type "{merged_type}".'
                )
            try:
                operations = {
                    '+': lambda: left.value + right.value,
                    '-': lambda: left.value - right.value,
                    '*': lambda: left.value * right.value,
                    '/': lambda: left.value / right.value if merged_type in {'f32', 'f64'} else left.value // right.value,
                    '%': lambda: left.value % right.value,
                }
                result = operations[operator]()
                return LiteralExpression(
                    cast_builtin_value(result, merged_type), merged_type
                )
            except (TypeError, ValueError, OverflowError, ZeroDivisionError) as err:
                raise CompileTimeError(
                    f'Cannot evaluate {merged_type} operator "{operator}".'
                ) from err
        if operator in {'==', '!=', '<', '>', '<=', '>='}:
            return LiteralExpression(
                self.compile_time_pass._compare_comptime_values(left, right, operator),
                'bool',
            )
        self._unknown_operator(operator)

    def _is_truthy(self, value: LiteralExpression) -> bool:
        if is_bool_type(value.type):
            return bool(value.value)
        raise CompileTimeError(
            f'Cannot use comptime value of type "{value.type}" as a condition.'
        )

    def _void_return_value(self) -> LiteralExpression:
        return LiteralExpression(None, 'void')

    def _return_outside_function(self) -> None:
        raise CompileTimeError('Return statement outside of comptime function execution.')

    def _unexpected_comptime_statement(self, statement: Statement) -> None:
        raise CompileTimeError(
            f'Unexpected comptime statement "{type(statement).__name__}" during comptime execution.'
        )

    def _unknown_statement(self, statement: Statement) -> None:
        raise CompileTimeFeatureNotImplemented(
            f'Comptime execution of {type(statement).__name__} statements is not implemented yet.'
        )

    def _unknown_expression(self, expression: Expression) -> None:
        raise CompileTimeError(
            f'Unknown comptime expression type "{type(expression).__name__}".'
        )

    def _unknown_operator(self, operator: str) -> None:
        raise CompileTimeError(f'Unknown comptime operator "{operator}".')

    def _loop_iteration_limit_exceeded(self, loop_name: str) -> None:
        raise CompileTimeError(
            f'Comptime {loop_name} loop exceeded {self.LOOP_ITERATION_LIMIT} iterations.'
        )
