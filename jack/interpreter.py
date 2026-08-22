from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Self
import copy
try:
    from .borrow_modes import borrow_mode_can_read, borrow_mode_can_write, borrow_mode_compatible
    from .builtin_types import (
        BuiltinType,
        JackPrimitiveValue,
        is_bool_type,
        is_builtin_type,
        is_numeric_type,
        is_raw_byte_type,
        runtime_builtin_types,
    )
    from .ast_nodes import (
        Statement,
        TypeReference,
    )
    from .execution import ReturnSignal
    from .hir_lowering_pass import compile_to_hir, lower_to_hir
    from .hir_nodes import (
        HIRAssignment,
        HIRBlock,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCallTarget,
        HIRCompositeExpression,
        HIRDereferenceExpression,
        HIRDeclaration,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFor,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRFormattedStringExpression,
        HIRIndexExpression,
        HIRLiteralExpression,
        HIRMoveExpression,
        HIRPointerCastExpression,
        HIRPointerOffsetExpression,
        HIRPrint,
        HIRProgram,
        HIRRawAddressExpression,
        HIRRaise,
        HIRRethrow,
        HIRReturn,
        HIRSliceExpression,
        HIRStatement,
        HIRTry,
        HIRUnsafeBlock,
        HIRStructLiteralExpression,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRViewDeclaration,
        HIRWhile,
    )
    from .parser import parse
except ImportError:
    from borrow_modes import borrow_mode_can_read, borrow_mode_can_write, borrow_mode_compatible
    from builtin_types import (
        BuiltinType,
        JackPrimitiveValue,
        is_bool_type,
        is_builtin_type,
        is_numeric_type,
        is_raw_byte_type,
        runtime_builtin_types,
    )
    from ast_nodes import (
        Statement,
        TypeReference,
    )
    from execution import ReturnSignal
    from hir_lowering_pass import compile_to_hir, lower_to_hir
    from hir_nodes import (
        HIRAssignment,
        HIRBlock,
        HIRBorrowExpression,
        HIRCallExpression,
        HIRCallTarget,
        HIRCompositeExpression,
        HIRDereferenceExpression,
        HIRDeclaration,
        HIRExpression,
        HIRExpressionStatement,
        HIRFieldAccessExpression,
        HIRFor,
        HIRFunctionDeclaration,
        HIRGlobalVariable,
        HIRIf,
        HIRFormattedStringExpression,
        HIRIndexExpression,
        HIRLiteralExpression,
        HIRMoveExpression,
        HIRPointerCastExpression,
        HIRPointerOffsetExpression,
        HIRPrint,
        HIRProgram,
        HIRRawAddressExpression,
        HIRRaise,
        HIRRethrow,
        HIRReturn,
        HIRSliceExpression,
        HIRStatement,
        HIRTry,
        HIRUnsafeBlock,
        HIRStructLiteralExpression,
        HIRTypeDeclaration,
        HIRVariableDeclaration,
        HIRVariableExpression,
        HIRViewDeclaration,
        HIRWhile,
    )
    from parser import parse


class InterpreterError(Exception):
    """Base class for errors raised by this interpreter."""


class NameResolutionError(InterpreterError):
    pass


class EvaluationError(InterpreterError):
    pass


@dataclass
class JackErrorValue:
    type_name: str
    payload: object

    def __str__(self) -> str:
        return self.type_name


class JackRaisedError(Exception):
    def __init__(self, value: JackErrorValue) -> None:
        super().__init__(str(value))
        self.value = value


@dataclass
class JackArray:
    element_type: TypeReference
    values: list[object]

    def __deepcopy__(self, memo):
        return JackArray(copy.deepcopy(self.element_type, memo), copy.deepcopy(self.values, memo))

    def __str__(self) -> str:
        return '[' + ', '.join(str(value) for value in self.values) + ']'


@dataclass
class JackSlice:
    array: JackArray
    start: int
    length: int
    mutable: bool = False
    mode: str = 'in'

    def __post_init__(self) -> None:
        if self.mutable and self.mode == 'in':
            self.mode = 'inout'
        self.mutable = borrow_mode_can_write(self.mode)

    def __deepcopy__(self, memo):
        return JackSlice(self.array, self.start, self.length, self.mutable, self.mode)

    def get(self, index: int) -> object:
        if not borrow_mode_can_read(self.mode):
            raise EvaluationError('Cannot read through a write-only slice.')
        self._check_index(index)
        return self.array.values[self.start + index]

    def set(self, index: int, value: object) -> None:
        if not self.mutable:
            raise EvaluationError('Cannot assign through a read-only slice.')
        self._check_index(index)
        self.array.values[self.start + index] = value

    def _check_index(self, index: int) -> None:
        if index < 0 or index >= self.length:
            raise EvaluationError(f'Slice index {index} is out of bounds for length {self.length}.')

    def __str__(self) -> str:
        return '[' + ', '.join(str(self.get(index)) for index in range(self.length)) + ']'


@dataclass
class JackBorrow:
    value: object
    mutable: bool = False
    mode: str = 'in'
    field_modes: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.mutable and self.mode == 'in':
            self.mode = 'inout'
        self.mutable = borrow_mode_can_write(self.mode)

    def __deepcopy__(self, memo):
        field_modes = None if self.field_modes is None else dict(self.field_modes)
        return JackBorrow(self.value, self.mutable, self.mode, field_modes)

    def get_field(self, name: str) -> object:
        if not borrow_mode_can_read(self._field_mode(name)):
            raise EvaluationError('Cannot read through a write-only borrow.')
        return getattr(self.value, name)

    def set_field(self, name: str, value: object) -> None:
        if not borrow_mode_can_write(self._field_mode(name)):
            raise EvaluationError('Cannot assign through a read-only borrow.')
        setattr(self.value, name, value)

    def _field_mode(self, name: str) -> str:
        if self.field_modes is not None and name in self.field_modes:
            return self.field_modes[name]
        return self.mode


class JackSymbolBorrow(JackBorrow):
    def __init__(
        self, scope: 'SymbolTable', name: str, mutable: bool = False,
        mode: str = 'in', field_modes: dict[str, str] | None = None,
    ) -> None:
        self.scope = scope
        self.name = name
        self.mode = 'inout' if mutable and mode == 'in' else mode
        self.mutable = borrow_mode_can_write(self.mode)
        self.field_modes = field_modes

    @property
    def value(self) -> object:
        return self.scope.get(self.name)

    @value.setter
    def value(self, value: object) -> None:
        if not self.mutable:
            raise EvaluationError('Cannot assign through a read-only borrow.')
        self.scope.assign(self.name, value)

    def __deepcopy__(self, memo):
        field_modes = None if self.field_modes is None else dict(self.field_modes)
        return JackSymbolBorrow(
            self.scope, self.name, self.mutable, self.mode, field_modes
        )


@dataclass
class JackArrayElementBorrow:
    array: JackArray
    index: int
    mutable: bool = False
    mode: str = 'in'
    field_modes: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.mutable and self.mode == 'in':
            self.mode = 'inout'
        self.mutable = borrow_mode_can_write(self.mode)

    def __deepcopy__(self, memo):
        field_modes = None if self.field_modes is None else dict(self.field_modes)
        return JackArrayElementBorrow(self.array, self.index, self.mutable, self.mode, field_modes)

    @property
    def value(self) -> object:
        if not borrow_mode_can_read(self.mode):
            raise EvaluationError('Cannot read through a write-only borrow.')
        return self.array.values[self.index]

    @value.setter
    def value(self, value: object) -> None:
        if not self.mutable:
            raise EvaluationError('Cannot assign through a read-only borrow.')
        self.array.values[self.index] = value

    def get_field(self, name: str) -> object:
        if not borrow_mode_can_read(self._field_mode(name)):
            raise EvaluationError('Cannot read through a write-only borrow.')
        return getattr(self.value, name)

    def set_field(self, name: str, value: object) -> None:
        if not borrow_mode_can_write(self._field_mode(name)):
            raise EvaluationError('Cannot assign through a read-only borrow.')
        setattr(self.value, name, value)

    def _field_mode(self, name: str) -> str:
        if self.field_modes is not None and name in self.field_modes:
            return self.field_modes[name]
        return self.mode


@dataclass
class JackRawPointer:
    target: JackBorrow | JackArrayElementBorrow
    mutable: bool
    allocation: 'JackAllocationRecord | None' = None

    def __deepcopy__(self, memo):
        return JackRawPointer(self.target, self.mutable, self.allocation)

    def get(self) -> object:
        self._require_live()
        if isinstance(self.target, JackArrayElementBorrow):
            value = self.target.value
        else:
            value = self.target.value
        if value is _UNINITIALIZED_VALUE:
            raise EvaluationError('Cannot read uninitialized allocation storage.')
        return value

    def set(self, value: object) -> None:
        self._require_live()
        if not self.mutable:
            raise EvaluationError('Cannot write through a *in raw pointer.')
        if isinstance(self.target, JackArrayElementBorrow):
            self.target.value = value
        else:
            self.target.value = value

    def offset(self, count: int) -> 'JackRawPointer':
        self._require_live()
        if not isinstance(self.target, JackArrayElementBorrow):
            if count != 0:
                raise EvaluationError('Cannot offset a raw pointer without array provenance.')
            return self
        index = self.target.index + count
        if index < 0 or index >= len(self.target.array.values):
            raise EvaluationError('Raw pointer offset is out of bounds.')
        return JackRawPointer(
            JackArrayElementBorrow(
                self.target.array,
                index,
                mutable=self.mutable,
                mode='inout' if self.mutable else 'in',
            ),
            self.mutable,
            self.allocation,
        )

    def _require_live(self) -> None:
        if self.allocation is not None and not self.allocation.live:
            raise EvaluationError(
                f'Raw pointer uses freed allocation {self.allocation.identity}.'
            )


@dataclass
class JackAllocationRecord:
    identity: int
    storage: JackArray
    alignment: int
    live: bool = True


_MOVED_VALUE = object()
_UNINITIALIZED_VALUE = object()


class SymbolTable:
    BUILTINS = {
        'str': str,
        **runtime_builtin_types(),
    }

    def __init__(self, parent: Self | None = None) -> None:
        self.parent = parent
        self.symbols: dict[str, object] = dict(self.BUILTINS)
        self.deinit_names: list[str] = []

    def get(self, name: str) -> object:
        parts = self._split_name(name)
        obj = self._resolve_root(parts[0])
        if obj is _MOVED_VALUE:
            raise EvaluationError(f'Cannot use moved value "{parts[0]}".')

        for field in parts[1:]:
            try:
                if isinstance(obj, (JackBorrow, JackArrayElementBorrow)):
                    obj = obj.get_field(field)
                else:
                    obj = getattr(obj, field)
            except AttributeError as err:
                raise NameResolutionError(f'Unknown field "{field}" in "{name}".') from err

        return obj

    def get_assignment_current(self, name: str) -> object:
        parts = self._split_name(name)
        if len(parts) == 1:
            return self.get(name)

        obj = self.get('.'.join(parts[:-1]))
        field = parts[-1]
        try:
            if isinstance(obj, JackBorrow):
                return getattr(obj.value, field)
            if isinstance(obj, JackArrayElementBorrow):
                return getattr(obj.value, field)
            return getattr(obj, field)
        except AttributeError as err:
            raise NameResolutionError(f'Unknown field "{field}" in "{name}".') from err

    def declare(self, name: str, value: object) -> None:
        parts = self._split_name(name)
        if len(parts) != 1:
            raise NameResolutionError(f'Cannot declare dotted name "{name}".')
        if name in self.symbols:
            raise NameResolutionError(f'Name "{name}" is already declared in this scope.')
        self.symbols[name] = value

    def mark_for_deinit(self, name: str) -> None:
        if name not in self.symbols:
            raise NameResolutionError(f'Cannot deinit undeclared name "{name}".')
        self.deinit_names.append(name)

    def is_marked_for_deinit(self, name: str) -> bool:
        target_scope = self._scope_containing(name)
        return target_scope is not None and name in target_scope.deinit_names

    def take(self, name: str) -> object:
        parts = self._split_name(name)
        if len(parts) != 1:
            raise NameResolutionError('Only whole variables can be moved.')
        target_scope = self._scope_containing(name)
        if target_scope is None:
            raise NameResolutionError(f'Cannot move undeclared name "{name}".')
        value = target_scope.symbols[name]
        while name in target_scope.deinit_names:
            target_scope.deinit_names.remove(name)
        target_scope.symbols[name] = _MOVED_VALUE
        return value

    def assign(self, name: str, value: object) -> None:
        parts = self._split_name(name)

        if len(parts) == 1:
            target_scope = self._scope_containing(parts[0])
            if target_scope is None:
                raise NameResolutionError(f'Cannot assign undeclared name "{name}".')
            target_scope.symbols[parts[0]] = value
            return

        obj = self.get('.'.join(parts[:-1]))
        field_name = parts[-1]
        if isinstance(obj, (JackBorrow, JackArrayElementBorrow)):
            try:
                obj.set_field(field_name, value)
            except AttributeError as err:
                raise NameResolutionError(f'Unknown field "{field_name}" in "{name}".') from err
            return
        if not hasattr(obj, field_name):
            raise NameResolutionError(f'Unknown field "{field_name}" in "{name}".')
        setattr(obj, field_name, value)

    def _resolve_root(self, name: str) -> object:
        scope = self._scope_containing(name)
        if scope is None:
            raise NameResolutionError(f'Unknown name "{name}".')
        return scope.symbols[name]

    def _scope_containing(self, name: str) -> Self | None:
        if name in self.symbols:
            return self
        if self.parent is not None:
            return self.parent._scope_containing(name)
        return None

    def _split_name(self, name: str) -> list[str]:
        parts = name.split('.')
        if any(part == '' for part in parts):
            raise NameResolutionError(f'Invalid name "{name}".')
        return parts


ExternHandler = Callable[..., object]


class Interpreter:
    LOOP_ITERATION_LIMIT: int | None = None

    def __init__(
        self,
        externs: dict[str, ExternHandler] | None = None,
        comptime_externs: dict[str, ExternHandler] | None = None,
    ) -> None:
        self.externs = externs or {}
        self.comptime_externs = comptime_externs or {}
        self.global_scope = SymbolTable()
        self.scope = self.global_scope
        self.caught_errors: list[JackRaisedError] = []
        self.view_declarations: dict[str, HIRViewDeclaration] = {}
        self.hir_program: HIRProgram | None = None
        self.hir_functions_by_name: dict[str, HIRFunctionDeclaration] = {}
        self.hir_methods_by_owner_and_name: dict[tuple[str, str], HIRFunctionDeclaration] = {}
        self.hir_types_by_name: dict[str, HIRTypeDeclaration] = {}

    def _prepare_hir(self, program: HIRProgram) -> None:
        self.hir_program = program
        self.hir_functions_by_name = {
            declaration.name: declaration
            for declaration in self.hir_program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        }
        self.hir_methods_by_owner_and_name = {
            (type_declaration.name, method.name): method
            for type_declaration in self.hir_program.declarations
            if isinstance(type_declaration, HIRTypeDeclaration)
            for method in type_declaration.methods
        }
        self.hir_types_by_name = {
            declaration.name: declaration
            for declaration in self.hir_program.declarations
            if isinstance(declaration, HIRTypeDeclaration)
        }

    def _hir_method_declaration_for_target(self, target: object) -> HIRFunctionDeclaration:
        owner_type_name = getattr(target, 'owner_type_name', None)
        if owner_type_name is None:
            raise EvaluationError(f'Incomplete HIR method target for "{target.name}".')
        method_name = target.name.rsplit('.', 1)[-1]
        declaration = self.hir_methods_by_owner_and_name.get((owner_type_name, method_name))
        if declaration is None:
            raise EvaluationError(f'Unknown HIR method "{owner_type_name}.{method_name}".')
        return declaration

    # Statements
    def eval(self, ast: List[Statement]) -> None:
        self.eval_source_ast(ast)

    def eval_source_ast(self, ast: List[Statement]) -> None:
        self.eval_hir_program(
            compile_to_hir(ast, externs=self.comptime_externs)
        )

    def eval_runtime_ast(self, ast: List[Statement]) -> None:
        self.eval_hir_program(lower_to_hir(ast))

    def eval_hir_program(self, program: HIRProgram) -> None:
        self._prepare_hir(program)
        self.view_declarations = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRViewDeclaration)
        }

        for declaration in program.declarations:
            if isinstance(declaration, HIRTypeDeclaration):
                self._execute_hir_type_declaration(declaration, self.global_scope)

        try:
            try:
                for statement in program.top_level:
                    if isinstance(statement, HIRGlobalVariable):
                        self._execute_hir_statement(
                            statement,
                            self.global_scope,
                            allow_return=False,
                        )
                    elif not isinstance(statement, HIRDeclaration):
                        self._execute_hir_statement(
                            statement,
                            self.global_scope,
                            allow_return=False,
                        )
            finally:
                self._execute_deinit_scope(self.global_scope)
        except JackRaisedError as err:
            raise EvaluationError(f'Unhandled error "{err.value}".') from err

    def _execute_hir_type_declaration(
        self, type_decl: HIRTypeDeclaration, scope: SymbolTable
    ) -> None:
        if type_decl.extern:
            scope.declare(type_decl.name, type(type_decl.name, (), {}))
            return

        field_types = {field.name: field.type_ref for field in type_decl.fields}
        interpreter = self

        def __init__(self):
            for name, field_type in field_types.items():
                if interpreter._is_borrow_type(field_type) or interpreter._is_slice_type(field_type):
                    setattr(self, name, None)
                else:
                    setattr(self, name, interpreter._default_value_for_type(field_type, scope))

        value = type(
            type_decl.name,
            (),
            {
                '__init__': __init__,
                '_jack_field_types': field_types,
            },
        )
        scope.declare(type_decl.name, value)

    def _child_scope(self, scope: SymbolTable) -> SymbolTable:
        return SymbolTable(scope)

    def _check_loop_limit(self, iterations: int, loop_name: str) -> None:
        if (
            self.LOOP_ITERATION_LIMIT is not None
            and iterations >= self.LOOP_ITERATION_LIMIT
        ):
            raise EvaluationError(
                f'{loop_name.capitalize()} loop exceeded the iteration limit.'
            )

    def _execute_hir_statement(
        self, statement: HIRStatement, scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        if isinstance(statement, (HIRGlobalVariable, HIRVariableDeclaration)):
            self._execute_hir_variable_declaration(statement, scope)
        elif isinstance(statement, HIRAssignment):
            self._execute_hir_assignment(statement, scope)
        elif isinstance(statement, HIRExpressionStatement):
            self._eval_hir_expression(statement.expr, scope)
        elif isinstance(statement, HIRPrint):
            self._execute_hir_print(statement, scope)
        elif isinstance(statement, HIRRaise):
            self._execute_hir_raise(statement, scope)
        elif isinstance(statement, HIRRethrow):
            self._execute_hir_rethrow()
        elif isinstance(statement, HIRReturn):
            if not allow_return:
                self._return_outside_function()
            return ReturnSignal(self._eval_hir_return(statement, scope))
        elif isinstance(statement, HIRIf):
            return self._execute_hir_if(statement, scope, allow_return)
        elif isinstance(statement, HIRWhile):
            return self._execute_hir_while(statement, scope, allow_return)
        elif isinstance(statement, HIRFor):
            return self._execute_hir_for(statement, scope, allow_return)
        elif isinstance(statement, HIRTry):
            return self._execute_hir_try(statement, scope, allow_return)
        elif isinstance(statement, HIRBlock):
            return self._execute_hir_block(statement.body, scope, allow_return)
        elif isinstance(statement, HIRUnsafeBlock):
            return self._execute_hir_block(statement.body, scope, allow_return)
        else:
            raise EvaluationError(f'Unknown HIR statement type "{type(statement).__name__}".')
        return None

    def _execute_hir_statements(
        self, statements: list[HIRStatement], scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        for statement in statements:
            returned = self._execute_hir_statement(statement, scope, allow_return)
            if returned is not None:
                return returned
        return None

    def _execute_hir_block(
        self, statements: list[HIRStatement], scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        child_scope = self._child_scope(scope)
        try:
            return self._execute_hir_statements(statements, child_scope, allow_return)
        finally:
            self._execute_deinit_scope(child_scope)

    def _execute_hir_variable_declaration(
        self, declaration: HIRGlobalVariable | HIRVariableDeclaration, scope: SymbolTable
    ) -> None:
        symbol = declaration.symbol
        if symbol.extern:
            if symbol.name not in self.externs:
                raise EvaluationError(f'No extern binding registered for variable "{symbol.name}".')
            value = self.externs[symbol.name]
            if self._is_borrow_type(symbol.type_ref) and not isinstance(value, (JackBorrow, JackSlice)):
                value = JackBorrow(value, mutable=borrow_mode_can_write(symbol.type_ref.borrow))
            value = self._coerce_value(value, symbol.type_ref, scope)
            scope.declare(symbol.name, value)
            return

        if declaration.initializer is None:
            value = self._default_value_for_type(symbol.type_ref, scope)
        else:
            value = self._eval_hir_expression_as_type(
                declaration.initializer, symbol.type_ref, scope
            )
        scope.declare(symbol.name, value)
        if self._value_needs_drop(value):
            scope.mark_for_deinit(symbol.name)
        if declaration.constructor_call is not None:
            self._eval_hir_function_call(declaration.constructor_call, scope)

    def _execute_hir_assignment(self, assignment: HIRAssignment, scope: SymbolTable) -> None:
        value = self._eval_hir_expression_as_type(assignment.expr, assignment.target_type, scope)
        if isinstance(assignment.target, HIRVariableExpression):
            name = assignment.target.name
            if scope.is_marked_for_deinit(name):
                self._execute_deinit_name(scope, name)
        self._assign_hir_expression_target(assignment.target, value, scope)
        if (
            isinstance(assignment.target, HIRVariableExpression)
            and self._value_needs_drop(value)
            and not scope.is_marked_for_deinit(assignment.target.name)
        ):
            target_scope = scope._scope_containing(assignment.target.name)
            assert target_scope is not None
            target_scope.mark_for_deinit(assignment.target.name)

    def _execute_hir_print(self, prt: HIRPrint, scope: SymbolTable) -> None:
        value = self._read_value(self._eval_hir_expression(prt.expr, scope))
        if not prt.label:
            print(value)
        else:
            print(f'{prt.label} = {value}')

    def _execute_hir_raise(self, statement: HIRRaise, scope: SymbolTable) -> None:
        payload = self._read_value(self._eval_hir_expression(statement.expr, scope))
        type_name = payload.__class__.__name__
        raise JackRaisedError(JackErrorValue(type_name, copy.deepcopy(payload)))

    def _execute_hir_rethrow(self) -> None:
        if not self.caught_errors:
            raise EvaluationError('rethrow used outside of a catch block.')
        raise self.caught_errors[-1]

    def _eval_hir_return(self, statement: HIRReturn, scope: SymbolTable) -> object:
        if statement.expr is None:
            return self._void_return_value()
        return self._eval_hir_expression(statement.expr, scope)

    def _execute_hir_if(
        self, statement: HIRIf, scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        for branch in statement.branches:
            if self._is_truthy(self._eval_hir_expression(branch.condition, scope)):
                return self._execute_hir_block(branch.body, scope, allow_return)
        if statement.else_body is not None:
            return self._execute_hir_block(statement.else_body, scope, allow_return)
        return None

    def _execute_hir_while(
        self, statement: HIRWhile, scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        iterations = 0
        while self._is_truthy(self._eval_hir_expression(statement.condition, scope)):
            self._check_loop_limit(iterations, 'while')
            returned = self._execute_hir_block(statement.body, scope, allow_return)
            if returned is not None:
                return returned
            iterations += 1
        return None

    def _execute_hir_for(
        self, statement: HIRFor, scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        loop_scope = self._child_scope(scope)
        if statement.initializer is not None:
            self._execute_hir_statement(statement.initializer, loop_scope, allow_return=False)

        iterations = 0
        while statement.condition is None or self._is_truthy(
            self._eval_hir_expression(statement.condition, loop_scope)
        ):
            self._check_loop_limit(iterations, 'for')
            returned = self._execute_hir_block(statement.body, loop_scope, allow_return)
            if returned is not None:
                return returned
            if statement.update is not None:
                self._execute_hir_statement(statement.update, loop_scope, allow_return=False)
            iterations += 1
        return None

    def _execute_hir_try(
        self, statement: HIRTry, scope: SymbolTable, allow_return: bool
    ) -> ReturnSignal[object] | None:
        try:
            return self._execute_hir_block(statement.body, scope, allow_return)
        except JackRaisedError as err:
            for catch in statement.catches:
                if self._catch_matches_error(catch.error_type.name, err.value):
                    catch_scope = self._child_scope(scope)
                    if catch.name is not None:
                        catch_scope.declare(catch.name, copy.deepcopy(err.value.payload))
                    self.caught_errors.append(err)
                    try:
                        return self._execute_hir_statements(catch.body, catch_scope, allow_return)
                    finally:
                        self.caught_errors.pop()
                        self._execute_deinit_scope(catch_scope)
            raise








    def _catch_matches_error(
        self, error_name: str, value: JackErrorValue
    ) -> bool:
        return error_name == value.type_name

    # Expressions
    def _eval_hir_expression(self, expression: HIRExpression, scope: SymbolTable) -> object:
        if isinstance(expression, HIRLiteralExpression):
            return self._eval_literal_value(expression.value, expression.literal_type, scope)
        if isinstance(expression, HIRVariableExpression):
            return scope.get(expression.name)
        if isinstance(expression, HIRFieldAccessExpression):
            return self._eval_hir_field_access(expression, scope)
        if isinstance(expression, HIRCompositeExpression):
            left = self._eval_hir_expression(expression.left, scope)
            right = self._eval_hir_expression(expression.right, scope)
            return self._eval_composite_operator(expression.operator, left, right)
        if isinstance(expression, HIRFormattedStringExpression):
            return self._eval_hir_formatted_string(expression, scope)
        if isinstance(expression, HIRCallExpression):
            return self._eval_hir_function_call(expression, scope)
        if isinstance(expression, HIRStructLiteralExpression):
            return self._eval_hir_struct_literal(expression, scope)
        if isinstance(expression, HIRIndexExpression):
            return self._eval_hir_index(expression, scope)
        if isinstance(expression, HIRSliceExpression):
            return self._slice_from_hir_expression(expression, scope, mutable=False)
        if isinstance(expression, HIRBorrowExpression):
            return self._eval_hir_borrow(expression, scope)
        if isinstance(expression, HIRMoveExpression):
            return self._take_hir_place(expression.expr, scope)
        if isinstance(expression, HIRRawAddressExpression):
            borrowed = self._eval_hir_borrow(
                HIRBorrowExpression(
                    mode=expression.mode,
                    expr=expression.expr,
                    type_ref=expression.type_ref,
                    read_type=expression.read_type,
                    span=expression.span,
                ),
                scope,
            )
            return JackRawPointer(
                borrowed,
                mutable=expression.type_ref.pointer_mode == 'inout',
            )
        if isinstance(expression, HIRDereferenceExpression):
            pointer = self._eval_hir_expression(expression.expr, scope)
            if pointer is None:
                raise EvaluationError('Cannot dereference a null raw pointer.')
            if not isinstance(pointer, JackRawPointer):
                raise EvaluationError('Cannot dereference a non-pointer value.')
            return pointer.get()
        if isinstance(expression, HIRPointerOffsetExpression):
            pointer = self._eval_hir_expression(expression.pointer, scope)
            if not isinstance(pointer, JackRawPointer):
                raise EvaluationError('Cannot offset a null or non-pointer value.')
            return pointer.offset(self._hir_index_value(expression.offset, scope))
        if isinstance(expression, HIRPointerCastExpression):
            return self._eval_hir_expression(expression.pointer, scope)
        raise EvaluationError(f'Unknown HIR expression type "{type(expression).__name__}".')

    def _eval_hir_expression_as_type(
        self, expression: HIRExpression, type_ref: str | TypeReference, scope: SymbolTable
    ) -> object:
        if isinstance(type_ref, TypeReference) and self._is_borrow_type(type_ref):
            return self._eval_hir_borrow_argument(expression, type_ref, scope)
        if isinstance(expression, HIRMoveExpression):
            value = self._eval_hir_expression(expression, scope)
            if self._type_name(expression.type_ref) == self._type_name(type_ref):
                return value
            return self._coerce_value(value, type_ref, scope)
        type_name = self._type_name(type_ref)
        if isinstance(expression, HIRLiteralExpression) and is_builtin_type(type_name):
            return self._coerce_value(
                expression.value, type_ref, scope, source_type=expression.literal_type
            )
        return self._coerce_value(self._eval_hir_expression(expression, scope), type_ref, scope)

    def _eval_hir_field_access(
        self, expression: HIRFieldAccessExpression, scope: SymbolTable
    ) -> object:
        target = self._eval_hir_expression(expression.target, scope)
        try:
            if isinstance(target, (JackBorrow, JackArrayElementBorrow)):
                value = target.get_field(expression.field_name)
            else:
                value = getattr(target, expression.field_name)
            if value is _MOVED_VALUE:
                raise EvaluationError(
                    f'Cannot use moved field "{expression.field_name}".'
                )
            return value
        except AttributeError as err:
            raise NameResolutionError(
                f'Unknown field "{expression.field_name}" in HIR field access.'
            ) from err

    def _eval_hir_formatted_string(
        self, expression: HIRFormattedStringExpression, scope: SymbolTable
    ) -> object:
        parts: list[str] = []
        for part in expression.parts:
            if type(part) is str:
                parts.append(part)
            else:
                parts.append(str(self._read_value(self._eval_hir_expression(part, scope))))
        return ''.join(parts)

    def _eval_hir_function_call(
        self, hir_call: HIRCallExpression, scope: SymbolTable
    ) -> object:
        target = hir_call.target
        if target.kind == 'len':
            return self._eval_hir_len_function_call(hir_call, scope)
        if target.kind == 'builtin_conversion':
            target_type = scope.get(target.name)
            if not isinstance(target_type, BuiltinType):
                raise EvaluationError(f'"{target.name}" is not a builtin type.')
            return self._eval_hir_builtin_conversion(target_type, hir_call, scope)
        if target.kind == 'function':
            if target.extern:
                return self._eval_hir_extern_function_call(target, hir_call, scope)
            declaration = self.hir_functions_by_name.get(target.name)
            if declaration is None:
                raise EvaluationError(f'Unknown HIR function "{target.name}".')
            return self._eval_hir_declared_function_call(declaration, hir_call, scope)
        if target.kind == 'method':
            if hir_call.receiver is None:
                raise EvaluationError(f'Incomplete HIR method target for "{target.name}".')
            declaration = self._hir_method_declaration_for_target(target)
            instance = self._method_receiver_value(
                self._eval_hir_expression(hir_call.receiver, scope)
            )
            return self._eval_hir_declared_method_call(
                instance, declaration, hir_call, scope
            )
        raise EvaluationError(f'Unknown HIR call target kind "{target.kind}".')

    def _eval_hir_len_function_call(
        self, hir_call: HIRCallExpression, scope: SymbolTable
    ) -> object:
        if len(hir_call.arguments) != 1:
            raise EvaluationError(f'len expects 1 argument, got {len(hir_call.arguments)}.')
        value = self._eval_hir_expression(hir_call.arguments[0], scope)
        if isinstance(value, JackBorrow):
            value = value.value
        if isinstance(value, JackSlice):
            length = value.length
        elif isinstance(value, JackArray):
            length = len(value.values)
        else:
            raise EvaluationError(f'len expects an array or slice, got "{type(value).__name__}".')
        return self.global_scope.get('i32')(length)

    def _eval_hir_builtin_conversion(
        self, target_type: BuiltinType, hir_call: HIRCallExpression, scope: SymbolTable
    ) -> object:
        if len(hir_call.arguments) != 1:
            raise EvaluationError(
                f'Type conversion "{hir_call.target.name}" expects 1 argument, '
                f'got {len(hir_call.arguments)}.'
            )

        argument = hir_call.arguments[0]
        try:
            if isinstance(argument, HIRLiteralExpression):
                return target_type(
                    argument.value, source_type=argument.literal_type, memory_raw=True
                )
            return target_type(
                self._read_value(self._eval_hir_expression(argument, scope)),
                memory_raw=True,
            )
        except (TypeError, ValueError, OverflowError) as err:
            raise EvaluationError(
                f'Cannot convert value to type "{target_type.name}".'
            ) from err

    def _eval_hir_extern_function_call(
        self, declaration: object, hir_call: HIRCallExpression, scope: SymbolTable
    ) -> object:
        self._validate_hir_call_arity(declaration.name, declaration.parameters, hir_call.arguments)

        handler = self.externs.get(declaration.name)
        if handler is None:
            raise EvaluationError(f'No extern binding registered for "{declaration.name}".')

        arguments = [
            self._hir_call_argument_value(parameter, argument, scope)
            for parameter, argument in zip(declaration.parameters, hir_call.arguments)
        ]
        try:
            result = handler(*arguments)
        except Exception as err:
            raise EvaluationError(f'Extern function "{declaration.name}" failed: {err}') from err

        return self._coerce_value(result, declaration.return_type, scope)

    def _eval_hir_declared_function_call(
        self, declaration: HIRFunctionDeclaration, hir_call: HIRCallExpression, scope: SymbolTable
    ) -> object:
        self._validate_hir_call_arity(
            hir_call.target.name, declaration.parameters, hir_call.arguments
        )

        function_scope = SymbolTable(scope)
        for parameter, argument in zip(declaration.parameters, hir_call.arguments):
            value = self._hir_call_argument_value(parameter, argument, scope)
            function_scope.declare(parameter.name, value)
            if parameter.type_ref.borrow is None and self._value_needs_drop(value):
                function_scope.mark_for_deinit(parameter.name)

        try:
            returned = self._execute_hir_statements(
                declaration.body,
                function_scope,
                allow_return=True,
            )
            if returned is None:
                return None
            return self._coerce_value(returned.value, declaration.return_type, scope)
        finally:
            self._execute_deinit_scope(function_scope)

    def _eval_hir_declared_method_call(
        self,
        instance: object,
        declaration: HIRFunctionDeclaration,
        hir_call: HIRCallExpression,
        scope: SymbolTable,
    ) -> object:
        if declaration.self_parameter is None:
            raise EvaluationError(f'Method "{hir_call.target.name}" has no HIR self parameter.')
        self._validate_hir_call_arity(
            hir_call.target.name, declaration.parameters, hir_call.arguments
        )

        method_scope = SymbolTable(scope)
        method_scope.declare(
            'self',
            self._method_self_value_for_type(
                instance, declaration.self_parameter.type_ref, scope
            ),
        )
        for parameter, argument in zip(declaration.parameters, hir_call.arguments):
            value = self._hir_call_argument_value(parameter, argument, scope)
            method_scope.declare(parameter.name, value)
            if parameter.type_ref.borrow is None and self._value_needs_drop(value):
                method_scope.mark_for_deinit(parameter.name)

        try:
            returned = self._execute_hir_statements(
                declaration.body,
                method_scope,
                allow_return=True,
            )
            if returned is None:
                return None
            return self._coerce_value(returned.value, declaration.return_type, scope)
        finally:
            self._execute_deinit_scope(method_scope)

    def _validate_hir_call_arity(
        self, name: str, parameters: list[object], arguments: list[HIRExpression]
    ) -> None:
        expected = len(parameters)
        actual = len(arguments)
        if actual != expected:
            raise EvaluationError(
                f'Function "{name}" expects {expected} argument(s), got {actual}.'
            )

    def _hir_call_argument_value(
        self, parameter: object, argument: HIRExpression, scope: SymbolTable
    ) -> object:
        parameter_type = self._parameter_type(parameter)
        if self._is_borrow_type(parameter_type):
            return self._eval_hir_borrow_argument(argument, parameter_type, scope)
        if isinstance(argument, HIRMoveExpression):
            return self._eval_hir_expression_as_type(argument, parameter_type, scope)
        return copy.deepcopy(self._eval_hir_expression_as_type(argument, parameter_type, scope))

    def _parameter_type(self, parameter: object) -> TypeReference:
        if hasattr(parameter, 'type_ref'):
            return parameter.type_ref
        return parameter.type

    def _eval_hir_borrow_argument(
        self, argument: HIRExpression, expected_type: TypeReference, scope: SymbolTable
    ) -> object:
        if isinstance(argument, HIRBorrowExpression):
            if not borrow_mode_compatible(expected_type.borrow, argument.mode):
                raise EvaluationError(
                    f'Cannot pass an &{argument.mode} borrow to an &{expected_type.borrow} parameter.'
                )
            return self._coerce_value(self._eval_hir_borrow(argument, scope), expected_type, scope)

        value = self._eval_hir_expression(argument, scope)
        return self._coerce_value(value, expected_type, scope)

    def _eval_hir_borrow(
        self, expression: HIRBorrowExpression, scope: SymbolTable
    ) -> object:
        mutable = borrow_mode_can_write(expression.mode)
        if isinstance(expression.expr, HIRDereferenceExpression):
            pointer = self._eval_hir_expression(expression.expr.expr, scope)
            if pointer is None:
                raise EvaluationError('Cannot borrow through a null raw pointer.')
            if not isinstance(pointer, JackRawPointer):
                raise EvaluationError('Cannot borrow through a non-pointer value.')
            if mutable and not pointer.mutable:
                raise EvaluationError('Cannot create a writable borrow through *in pointer.')
            return pointer.target
        if isinstance(expression.expr, HIRSliceExpression):
            return self._slice_from_hir_expression(
                expression.expr, scope, mutable=mutable, mode=expression.mode
            )
        if isinstance(expression.expr, HIRIndexExpression):
            return self._eval_hir_index_borrow(
                expression.expr, scope, mutable=mutable, mode=expression.mode
            )

        if isinstance(expression.expr, HIRVariableExpression):
            return JackSymbolBorrow(
                scope,
                expression.expr.name,
                mutable=mutable,
                mode=expression.mode,
            )

        value = self._eval_hir_expression(expression.expr, scope)
        if isinstance(value, JackSlice):
            if mutable and not value.mutable:
                raise EvaluationError('Cannot create a writable slice from a read-only slice.')
            return JackSlice(value.array, value.start, value.length, mutable=mutable, mode=expression.mode)
        return JackBorrow(value, mutable=mutable, mode=expression.mode)

    def _eval_hir_index_borrow(
        self, expression: HIRIndexExpression, scope: SymbolTable, mutable: bool, mode: str
    ) -> object:
        target = self._eval_hir_expression(expression.target, scope)
        index = self._hir_index_value(expression.index, scope)

        if isinstance(target, JackBorrow):
            if mutable and not target.mutable:
                raise EvaluationError('Cannot create a writable borrow from a read-only borrow.')
            target = target.value

        if isinstance(target, JackSlice):
            if mutable and not target.mutable:
                raise EvaluationError('Cannot create a writable borrow from a read-only slice.')
            target._check_index(index)
            return JackArrayElementBorrow(target.array, target.start + index, mutable=mutable, mode=mode)

        if isinstance(target, JackArray):
            self._check_array_index(target, index)
            return JackArrayElementBorrow(target, index, mutable=mutable, mode=mode)

        raise EvaluationError(f'Cannot index value of type "{type(target).__name__}".')

    def _eval_hir_index(self, expression: HIRIndexExpression, scope: SymbolTable) -> object:
        target = self._eval_hir_expression(expression.target, scope)
        index = self._hir_index_value(expression.index, scope)
        return self._get_indexed_value(target, index)

    def _slice_from_hir_expression(
        self,
        expression: HIRSliceExpression,
        scope: SymbolTable,
        mutable: bool,
        mode: str | None = None,
    ) -> JackSlice:
        mode = mode or ('inout' if mutable else 'in')
        target = self._eval_hir_expression(expression.target, scope)
        if isinstance(target, JackBorrow):
            if mutable and not target.mutable:
                raise EvaluationError('Cannot create a writable slice from a read-only borrow.')
            target = target.value

        if isinstance(target, JackSlice):
            if mutable and not target.mutable:
                raise EvaluationError('Cannot create a writable slice from a read-only slice.')
            base_array = target.array
            base_start = target.start
            length = target.length
        elif isinstance(target, JackArray):
            base_array = target
            base_start = 0
            length = len(target.values)
        else:
            raise EvaluationError(f'Cannot slice value of type "{type(target).__name__}".')

        start = 0 if expression.start is None else self._hir_index_value(expression.start, scope)
        end = length if expression.end is None else self._hir_index_value(expression.end, scope)
        if start < 0 or end < start or end > length:
            raise EvaluationError(f'Invalid slice range {start}..{end} for length {length}.')
        return JackSlice(base_array, base_start + start, end - start, mutable=mutable, mode=mode)

    def _eval_hir_struct_literal(
        self, expression: HIRStructLiteralExpression, scope: SymbolTable
    ) -> object:
        struct_type = self._get_type(expression.type_ref, scope)
        if not isinstance(struct_type, type):
            raise EvaluationError(f'"{self._type_name(expression.type_ref)}" is not a struct type.')
        field_types = getattr(struct_type, '_jack_field_types', None)
        if field_types is None:
            raise EvaluationError(f'"{self._type_name(expression.type_ref)}" is not a Jack struct type.')

        # A complete struct literal initializes every field itself; running the
        # default constructor first would create objects that are immediately
        # overwritten and would make non-defaultable fields impossible.
        value = struct_type.__new__(struct_type)
        seen: set[str] = set()
        for field in expression.fields:
            if field.name in seen:
                raise EvaluationError(f'Struct literal has duplicate field "{field.name}".')
            if field.name not in field_types:
                raise EvaluationError(
                    f'Type "{self._type_name(expression.type_ref)}" has no field "{field.name}".'
                )
            seen.add(field.name)
            setattr(
                value,
                field.name,
                self._eval_hir_expression_as_type(field.expr, field_types[field.name], scope),
            )

        missing = [name for name in field_types if name not in seen]
        if missing:
            raise EvaluationError(
                f'Struct literal for "{self._type_name(expression.type_ref)}" is missing field "{missing[0]}".'
            )
        return value

    def _hir_index_value(self, expression: HIRExpression, scope: SymbolTable) -> int:
        value = self._eval_hir_expression(expression, scope)
        if isinstance(value, JackPrimitiveValue):
            value = value.value
        if type(value) is not int:
            raise EvaluationError(f'Index must be an integer, got "{type(value).__name__}".')
        return value


    def _eval_literal_value(
        self, value: object, literal_type_name: str, scope: SymbolTable
    ) -> object:
        if literal_type_name == 'null':
            return None
        literal_type = self._get_type(literal_type_name, scope)
        try:
            return literal_type(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise EvaluationError(
                f'Cannot convert {value!r} to type "{literal_type_name}".'
            ) from err



    def _method_receiver_value(self, value: object) -> object:
        if isinstance(value, JackBorrow):
            return value.value
        if isinstance(value, JackArrayElementBorrow):
            return value.value
        return value









    def _eval_composite_operator(self, operator: str, left: object, right: object) -> object:
        left = self._read_value(left)
        right = self._read_value(right)
        if isinstance(left, JackPrimitiveValue) or isinstance(right, JackPrimitiveValue):
            return self._eval_primitive_composite_operator(operator, left, right)
        if operator == '+':
            if type(left) is str or type(right) is str:
                raise EvaluationError('String concatenation is not implemented yet.')
            return left + right
        if operator == '==':
            return self.global_scope.get('bool')(left == right)
        if operator == '!=':
            return self.global_scope.get('bool')(left != right)
        if type(left) is str or type(right) is str:
            raise EvaluationError(f'Operator "{operator}" is not implemented for strings.')
        if operator == '<':
            return self.global_scope.get('bool')(left < right)
        if operator == '>':
            return self.global_scope.get('bool')(left > right)
        if operator == '<=':
            return self.global_scope.get('bool')(left <= right)
        if operator == '>=':
            return self.global_scope.get('bool')(left >= right)
        self._unknown_operator(operator)

    def _read_value(self, value: object) -> object:
        if isinstance(value, JackArrayElementBorrow):
            return self._read_value(value.value)
        if isinstance(value, JackBorrow):
            if not borrow_mode_can_read(value.mode):
                raise EvaluationError('Cannot read through a write-only borrow.')
            return self._read_value(value.value)
        return value

    def _eval_primitive_composite_operator(
        self, operator: str, left: object, right: object
    ) -> object:
        if not isinstance(left, JackPrimitiveValue) or not isinstance(right, JackPrimitiveValue):
            raise EvaluationError('Cannot combine primitive values with non-primitive values.')
        if left.type_name != right.type_name:
            raise EvaluationError(
                f'Cannot combine values of type "{left.type_name}" and "{right.type_name}".'
            )
        if is_raw_byte_type(left.type_name) and operator not in {'==', '!='}:
            raise EvaluationError(f'Operator "{operator}" is not implemented for raw byte types.')
        if not is_numeric_type(left.type_name) and operator not in {'==', '!='}:
            raise EvaluationError(f'Operator "{operator}" is not implemented for type "{left.type_name}".')

        if operator in {'+', '-', '*', '/', '%'}:
            if not is_numeric_type(left.type_name) or is_raw_byte_type(left.type_name):
                raise EvaluationError(f'Operator "{operator}" is not implemented for type "{left.type_name}".')
            operations = {
                '+': lambda: left.value + right.value,
                '-': lambda: left.value - right.value,
                '*': lambda: left.value * right.value,
                '/': lambda: left.value / right.value if left.type_name in {'f32', 'f64'} else left.value // right.value,
                '%': lambda: left.value % right.value,
            }
            try:
                result = operations[operator]()
                return self.global_scope.get(left.type_name)(result)
            except (TypeError, ValueError, OverflowError, ZeroDivisionError) as err:
                raise EvaluationError(
                    f'Cannot evaluate {left.type_name} operator "{operator}".'
                ) from err
        if operator == '==':
            return self.global_scope.get('bool')(left.value == right.value)
        if operator == '!=':
            return self.global_scope.get('bool')(left.value != right.value)
        if operator == '<':
            return self.global_scope.get('bool')(left.value < right.value)
        if operator == '>':
            return self.global_scope.get('bool')(left.value > right.value)
        if operator == '<=':
            return self.global_scope.get('bool')(left.value <= right.value)
        if operator == '>=':
            return self.global_scope.get('bool')(left.value >= right.value)
        self._unknown_operator(operator)

    def _is_truthy(self, value: object) -> bool:
        value = self._read_value(value)
        if isinstance(value, JackPrimitiveValue):
            if is_bool_type(value.type_name):
                return bool(value.value)
            raise EvaluationError(
                f'Cannot use value of type "{value.type_name}" as a condition.'
            )
        if type(value) is bool:
            return value
        raise EvaluationError(
            f'Cannot use value of type "{type(value).__name__}" as a condition.'
        )

    def _void_return_value(self) -> object:
        return None





    def _method_self_value_for_type(
        self, instance: object, self_type: TypeReference, scope: SymbolTable
    ) -> object:
        return self._coerce_value(
            JackBorrow(
                instance,
                mutable=borrow_mode_can_write(self_type.borrow),
                mode=self_type.borrow or 'inout',
            ),
            self_type,
            scope,
        )





    def _execute_deinit_scope(self, scope: SymbolTable) -> None:
        while scope.deinit_names:
            name = scope.deinit_names.pop()
            self._execute_deinit_value(scope.get(name), scope)

    def _execute_deinit_name(self, scope: SymbolTable, name: str) -> None:
        target_scope = scope._scope_containing(name)
        if target_scope is None:
            raise NameResolutionError(f'Cannot deinit undeclared name "{name}".')
        target_scope.deinit_names.remove(name)
        self._execute_deinit_value(target_scope.get(name), target_scope)

    def _execute_deinit_value(self, value: object, scope: SymbolTable) -> None:
        if value is _MOVED_VALUE:
            return
        hir_deinit = self._hir_method_for_value(value, 'deinit')
        if hir_deinit is not None:
            target = HIRCallTarget(
                kind='method',
                name=f'{value.__class__.__name__}.deinit',
                return_type=copy.deepcopy(hir_deinit.return_type),
                parameters=list(hir_deinit.parameters),
                self_parameter=hir_deinit.self_parameter,
                raises=list(hir_deinit.raises),
                owner_type_name=value.__class__.__name__,
            )
            call = HIRCallExpression(
                target=target,
                arguments=[],
                type_ref=copy.deepcopy(hir_deinit.return_type),
                read_type=copy.deepcopy(hir_deinit.return_type),
            )
            self._eval_hir_declared_method_call(value, hir_deinit, call, scope)

        declaration = self.hir_types_by_name.get(value.__class__.__name__)
        if declaration is None:
            if isinstance(value, JackArray):
                for item in reversed(value.values):
                    if item is not _MOVED_VALUE and self._value_needs_drop(item):
                        self._execute_deinit_value(item, scope)
            return
        for field in reversed(declaration.fields):
            if field.type_ref.borrow is not None or field.type_ref.is_slice:
                continue
            field_value = getattr(value, field.name)
            if field_value is not _MOVED_VALUE and self._value_needs_drop(field_value):
                self._execute_deinit_value(field_value, scope)

    def _value_needs_drop(self, value: object) -> bool:
        if value is _MOVED_VALUE:
            return False
        if isinstance(value, (JackBorrow, JackArrayElementBorrow, JackSlice)):
            return False
        if isinstance(value, JackArray):
            return any(self._value_needs_drop(item) for item in value.values)
        declaration = self.hir_types_by_name.get(value.__class__.__name__)
        if declaration is None:
            return False
        if any(method.name == 'deinit' for method in declaration.methods):
            return True
        return any(
            field.type_ref.borrow is None
            and not field.type_ref.is_slice
            and self._value_needs_drop(getattr(value, field.name))
            for field in declaration.fields
        )

    def _hir_method_for_value(
        self, value: object, name: str
    ) -> HIRFunctionDeclaration | None:
        instance = self._method_receiver_value(value)
        return self.hir_methods_by_owner_and_name.get((instance.__class__.__name__, name))

    def _has_method(self, value: object, name: str) -> bool:
        return self._hir_method_for_value(value, name) is not None

    # Type definition

    def _get_type(self, type_ref: str | TypeReference, scope: SymbolTable) -> type | BuiltinType:
        if isinstance(type_ref, TypeReference) and (
            self._is_array_type(type_ref) or self._is_slice_type(type_ref)
            or self._is_borrow_type(type_ref) or type_ref.pointer_mode is not None
        ):
            raise EvaluationError(f'"{self._type_name(type_ref)}" is not a directly constructible type.')
        name = self._type_name(type_ref)
        value = scope.get(name)
        if not isinstance(value, type) and not isinstance(value, BuiltinType):
            raise EvaluationError(f'"{name}" is not a type.')
        return value



    def _coerce_value(
        self,
        value: object,
        type_ref: str | TypeReference,
        scope: SymbolTable,
        source_type: str | None = None,
    ) -> object:
        type_name = self._type_name(type_ref)
        if type_name == 'void':
            if value is None:
                return None
            raise EvaluationError(f'Cannot convert {value!r} to type "void".')

        if isinstance(type_ref, TypeReference) and self._is_borrow_type(type_ref):
            return self._coerce_borrow_value(value, type_ref)
        if isinstance(type_ref, TypeReference) and type_ref.pointer_mode is not None:
            if value is None and type_ref.nullable:
                return None
            if not isinstance(value, JackRawPointer):
                raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')
            if type_ref.pointer_mode == 'inout' and not value.mutable:
                raise EvaluationError('Cannot convert *in pointer to *inout pointer.')
            return value
        if isinstance(type_ref, TypeReference) and self._is_slice_type(type_ref):
            if not isinstance(value, JackSlice):
                raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')
            self._check_slice_element_type(value, self._element_type(type_ref), type_name)
            return copy.deepcopy(value)
        if isinstance(type_ref, TypeReference) and self._is_array_type(type_ref):
            if not isinstance(value, JackArray):
                raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')
            expected_size = self._array_size_value(type_ref.array_size)
            if len(value.values) != expected_size:
                raise EvaluationError(
                    f'Cannot convert array of length {len(value.values)} to type "{type_name}".'
                )
            element_type = self._element_type(type_ref)
            return JackArray(
                element_type,
                [self._coerce_value(item, element_type, scope) for item in value.values],
            )

        value = self._read_value(value)
        target_type = self._get_type(type_ref, scope)
        if isinstance(target_type, type) and isinstance(value, target_type):
            return copy.deepcopy(value)

        try:
            if isinstance(target_type, BuiltinType):
                return target_type(value, source_type=source_type)
            return target_type(value)
        except (TypeError, ValueError, OverflowError) as err:
            raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".') from err

    def _default_value_for_type(self, type_ref: TypeReference, scope: SymbolTable) -> object:
        if self._is_borrow_type(type_ref) or self._is_slice_type(type_ref):
            raise EvaluationError(
                f'Cannot create a default value for type "{self._type_name(type_ref)}".'
            )
        if self._is_array_type(type_ref):
            size = self._array_size_value(type_ref.array_size)
            element_type = self._element_type(type_ref)
            return JackArray(
                element_type,
                [self._default_value_for_type(element_type, scope) for _ in range(size)],
            )

        variable_type = self._get_type(type_ref, scope)
        try:
            return variable_type()
        except (TypeError, ValueError, OverflowError) as err:
            raise EvaluationError(
                f'Cannot create a default value for type "{self._type_name(type_ref)}".'
            ) from err

    def _coerce_borrow_value(self, value: object, type_ref: TypeReference) -> object:
        type_name = self._type_name(type_ref)
        value_mode = getattr(value, 'mode', 'inout' if getattr(value, 'mutable', False) else 'in')
        if not borrow_mode_compatible(type_ref.borrow, value_mode):
            raise EvaluationError(f'Cannot convert borrow to type "{type_name}".')
        if self._is_view_borrow_type(type_ref):
            return self._coerce_view_borrow_value(value, type_ref, type_name)
        if self._is_slice_type(type_ref):
            if not isinstance(value, JackSlice):
                raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')
            self._check_slice_element_type(value, self._element_type(type_ref), type_name)
            return value
        if self._is_array_type(type_ref):
            if not isinstance(value, JackBorrow) or not isinstance(value.value, JackArray):
                raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')
            expected_size = self._literal_array_size(type_ref)
            if len(value.value.values) != expected_size:
                raise EvaluationError(
                    f'Cannot convert borrowed array of length {len(value.value.values)} to type "{type_name}".'
                )
            if self._type_name(value.value.element_type) != self._type_name(self._element_type(type_ref)):
                raise EvaluationError(f'Cannot convert borrowed array to type "{type_name}".')
            return value
        if not isinstance(value, (JackBorrow, JackArrayElementBorrow)):
            raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')
        return value

    def _coerce_view_borrow_value(
        self, value: object, type_ref: TypeReference, type_name: str
    ) -> object:
        view = self.view_declarations.get(type_ref.name)
        if view is None:
            raise EvaluationError(f'Unknown view "{type_ref.name}".')
        field_modes = {field.name: field.mode for field in view.fields}
        if isinstance(value, JackBorrow):
            return JackBorrow(value.value, value.mutable, value.mode, field_modes)
        if isinstance(value, JackArrayElementBorrow):
            return JackArrayElementBorrow(
                value.array, value.index, value.mutable, value.mode, field_modes
            )
        raise EvaluationError(f'Cannot convert {value!r} to type "{type_name}".')

    def _check_slice_element_type(
        self, value: JackSlice, expected_type: TypeReference, type_name: str
    ) -> None:
        if self._type_name(value.array.element_type) != self._type_name(expected_type):
            raise EvaluationError(f'Cannot convert slice to type "{type_name}".')

    def _assign_hir_expression_target(
        self, target: HIRExpression, value: object, scope: SymbolTable
    ) -> None:
        if isinstance(target, HIRVariableExpression):
            scope.assign(target.name, value)
            return
        if isinstance(target, HIRFieldAccessExpression):
            receiver = self._eval_hir_expression(target.target, scope)
            if isinstance(receiver, (JackBorrow, JackArrayElementBorrow)):
                try:
                    receiver.set_field(target.field_name, value)
                except AttributeError as err:
                    raise NameResolutionError(
                        f'Unknown field "{target.field_name}" in HIR assignment.'
                    ) from err
                return
            if not hasattr(receiver, target.field_name):
                raise NameResolutionError(
                    f'Unknown field "{target.field_name}" in HIR assignment.'
                )
            setattr(receiver, target.field_name, value)
            return
        if isinstance(target, HIRIndexExpression):
            indexed = self._eval_hir_expression(target.target, scope)
            index = self._hir_index_value(target.index, scope)
            self._set_indexed_value(indexed, index, value)
            return
        if isinstance(target, HIRDereferenceExpression):
            pointer = self._eval_hir_expression(target.expr, scope)
            if pointer is None:
                raise EvaluationError('Cannot assign through a null raw pointer.')
            if not isinstance(pointer, JackRawPointer):
                raise EvaluationError('Cannot assign through a non-pointer value.')
            pointer.set(value)
            return
        raise EvaluationError(f'Unsupported HIR assignment target "{type(target).__name__}".')

    def _take_hir_place(
        self, target: HIRExpression, scope: SymbolTable
    ) -> object:
        if isinstance(target, HIRVariableExpression):
            return scope.take(target.name)
        if isinstance(target, HIRFieldAccessExpression):
            receiver = self._eval_hir_expression(target.target, scope)
            owner = receiver.value if isinstance(receiver, JackBorrow) else receiver
            if isinstance(owner, JackArrayElementBorrow):
                owner = owner.value
            try:
                value = getattr(owner, target.field_name)
            except AttributeError as err:
                raise NameResolutionError(
                    f'Unknown field "{target.field_name}" in move expression.'
                ) from err
            if value is _MOVED_VALUE:
                raise EvaluationError(f'Cannot move field "{target.field_name}" twice.')
            setattr(owner, target.field_name, _MOVED_VALUE)
            return value
        if isinstance(target, HIRIndexExpression):
            indexed = self._eval_hir_expression(target.target, scope)
            index = self._hir_index_value(target.index, scope)
            if isinstance(indexed, JackBorrow):
                indexed = indexed.value
            if not isinstance(indexed, JackArray):
                raise EvaluationError('Only fixed arrays support partial indexed moves.')
            self._check_array_index(indexed, index)
            value = indexed.values[index]
            if value is _MOVED_VALUE:
                raise EvaluationError(f'Cannot move array element {index} twice.')
            indexed.values[index] = _MOVED_VALUE
            return value
        # Temporaries already own their result and need no source state update.
        return self._eval_hir_expression(target, scope)





    def _get_indexed_value(self, target: object, index: int) -> object:
        if isinstance(target, JackBorrow):
            if not borrow_mode_can_read(target.mode):
                raise EvaluationError('Cannot read through a write-only borrow.')
            return self._get_indexed_value(target.value, index)
        if isinstance(target, JackSlice):
            return target.get(index)
        if isinstance(target, JackArray):
            self._check_array_index(target, index)
            value = target.values[index]
            if value is _MOVED_VALUE:
                raise EvaluationError(f'Cannot use moved array element {index}.')
            return value
        raise EvaluationError(f'Cannot index value of type "{type(target).__name__}".')

    def _set_indexed_value(self, target: object, index: int, value: object) -> None:
        if isinstance(target, JackBorrow):
            if not target.mutable:
                raise EvaluationError('Cannot assign through a read-only borrow.')
            self._set_indexed_value(target.value, index, value)
            return
        if isinstance(target, JackSlice):
            target.set(index, value)
            return
        if isinstance(target, JackArray):
            self._check_array_index(target, index)
            target.values[index] = value
            return
        raise EvaluationError(f'Cannot index value of type "{type(target).__name__}".')


    def _check_array_index(self, array: JackArray, index: int) -> None:
        if index < 0 or index >= len(array.values):
            raise EvaluationError(
                f'Array index {index} is out of bounds for length {len(array.values)}.'
            )


    def _array_size_value(self, expression: object) -> int:
        if type(expression) is int:
            size = expression
        else:
            raise EvaluationError(
                'Runtime HIR array types require a compile-time constant size.'
            )
        if size < 0:
            raise EvaluationError(f'Array size must be non-negative, got {size}.')
        return size

    def _literal_array_size(self, type_ref: TypeReference) -> int:
        if type(type_ref.array_size) is not int:
            raise EvaluationError(
                f'Array type "{self._type_name(type_ref)}" needs a constant HIR size.'
            )
        return type_ref.array_size

    def _element_type(self, type_ref: TypeReference) -> TypeReference:
        return TypeReference(type_ref.name, copy.deepcopy(type_ref.arguments))


    def _is_array_type(self, type_ref: TypeReference) -> bool:
        return type_ref.array_size is not None

    def _is_slice_type(self, type_ref: TypeReference) -> bool:
        return type_ref.is_slice

    def _is_borrow_type(self, type_ref: TypeReference) -> bool:
        return type_ref.borrow is not None

    def _is_view_borrow_type(self, type_ref: TypeReference) -> bool:
        return self._is_borrow_type(type_ref) and type_ref.name in self.view_declarations

    def _type_name(self, type_ref: str | TypeReference) -> str:
        if isinstance(type_ref, str):
            return type_ref
        if type_ref.arguments:
            raise EvaluationError(f'Unresolved generic type "{type_ref.name}".')

        name = type_ref.name
        if type_ref.array_size is not None:
            name = f'{name}[{self._type_size_name(type_ref.array_size)}]'
        elif type_ref.is_slice:
            name = f'{name}[]'
        if type_ref.borrow is not None:
            name = f'&{type_ref.borrow} {name}'
        elif type_ref.pointer_mode is not None:
            prefix = '?' if type_ref.nullable else ''
            name = f'{prefix}*{type_ref.pointer_mode} {name}'
        return name

    def _type_size_name(self, expression: object) -> str:
        if type(expression) is int:
            return str(expression)
        raise EvaluationError('Runtime HIR array types require an integer extent.')

    def _return_outside_function(self) -> None:
        raise EvaluationError('Return statement outside of function.')




    def _unknown_operator(self, operator: str) -> None:
        raise EvaluationError(f'Unknown operator "{operator}".')



if __name__ == '__main__':
    # Parse and interpret the demo program only when this file is run directly.
    interpreter = Interpreter()
    source_path = Path(__file__).resolve().parents[1] / 'source.jk'
    with source_path.open('r') as f:
        source = f.read()

    interpreter.eval(parse(source))
