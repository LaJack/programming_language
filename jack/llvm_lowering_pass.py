from __future__ import annotations

import sys
from dataclasses import dataclass

from .borrow_modes import borrow_mode_can_write
from .builtin_types import BUILTIN_TYPE_SPECS
from .hir_nodes import (
    HIRAssignment,
    HIRBlock,
    HIRBorrowExpression,
    HIRCallExpression,
    HIRCompositeExpression,
    HIRDeclaration,
    HIRExpression,
    HIRExpressionStatement,
    HIRFieldAccessExpression,
    HIRFor,
    HIRFormattedStringExpression,
    HIRFunctionDeclaration,
    HIRGlobalVariable,
    HIRIf,
    HIRIndexExpression,
    HIRLiteralExpression,
    HIRPrint,
    HIRProgram,
    HIRRaise,
    HIRRethrow,
    HIRReturn,
    HIRSliceExpression,
    HIRStatement,
    HIRStructLiteralExpression,
    HIRTry,
    HIRTypeDeclaration,
    HIRVariableDeclaration,
    HIRVariableExpression,
    HIRViewDeclaration,
    HIRWhile,
)
from .llvm_ir import LLVMFunction, LLVMInstruction, LLVMModule, quoted
from .source_model import TypeReference


class LLVMLoweringError(Exception):
    def __init__(self, message: str, span=None) -> None:
        super().__init__(message)
        self.span = span


@dataclass(frozen=True)
class LLVMValue:
    type_name: str
    operand: str
    type_ref: TypeReference


class FunctionBuilder:
    def __init__(self, lowerer: 'LLVMLoweringPass', declaration: HIRFunctionDeclaration | None):
        self.lowerer = lowerer
        self.declaration = declaration
        self.blocks: list[tuple[str, list[LLVMInstruction]]] = [('entry', [])]
        self.block = self.blocks[0][1]
        self.terminated = False
        self.counter = 0
        self.label_counter = 0
        self.entry_alloca_count = 0
        self.env: dict[str, tuple[str, TypeReference]] = dict(lowerer.global_env)
        self.error_handlers: list[str] = []
        self.caught_errors: list[tuple[str, str]] = []
        self.has_error_slots = False
        self.current_span = declaration.span if declaration is not None else None

    def temp(self, prefix: str = 'v') -> str:
        self.counter += 1
        return f'%{prefix}{self.counter}'

    def label(self, prefix: str) -> str:
        self.label_counter += 1
        return f'{prefix}.{self.label_counter}'

    def emit(self, instruction: str) -> None:
        if self.terminated:
            return
        self.block.append(LLVMInstruction(instruction, self.current_span))

    def terminate(self, instruction: str) -> None:
        self.emit(instruction)
        self.terminated = True

    def start(self, label: str) -> None:
        self.blocks.append((label, []))
        self.block = self.blocks[-1][1]
        self.terminated = False

    def branch(self, label: str) -> None:
        if not self.terminated:
            self.terminate(f'br label %{label}')

    def alloca(self, type_name: str, prefix: str = 'slot') -> str:
        value = self.temp(prefix)
        self.named_alloca(value, type_name)
        return value

    def named_alloca(self, value: str, type_name: str) -> None:
        entry = self.blocks[0][1]
        entry.insert(
            self.entry_alloca_count,
            LLVMInstruction(f'{value} = alloca {type_name}'),
        )
        self.entry_alloca_count += 1


class LLVMLoweringPass:
    def __init__(self, *, debug: bool = False, optimization: int = 0) -> None:
        self.module = LLVMModule(debug=debug, optimization=optimization)
        self.types: dict[str, HIRTypeDeclaration] = {}
        self.views: dict[str, HIRViewDeclaration] = {}
        self.functions: dict[str, HIRFunctionDeclaration] = {}
        self.global_env: dict[str, tuple[str, TypeReference]] = {}
        self.string_globals: dict[bytes, str] = {}
        self.error_tags: dict[str, int] = {}
        self.error_payload_indices: dict[str, int] = {}
        self.result_types: dict[str, str] = {}
        self.builder: FunctionBuilder | None = None

    def lower(self, program: HIRProgram) -> LLVMModule:
        self.types = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRTypeDeclaration) and not declaration.extern
        }
        self.views = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRViewDeclaration)
        }
        self.functions = {
            declaration.name: declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        }
        self._collect_error_tags(program)
        self._declare_runtime()
        self._declare_types()
        self._declare_error_payload()
        self._declare_globals(program)
        self._declare_functions(program)
        for declaration in program.declarations:
            if isinstance(declaration, HIRFunctionDeclaration) and not declaration.extern:
                self.module.functions.append(self._function(declaration))
            elif isinstance(declaration, HIRTypeDeclaration):
                for method in declaration.methods:
                    self.module.functions.append(
                        self._function(
                            method,
                            declaration.name,
                            declaration.source_name or declaration.name,
                        )
                    )
        self.module.functions.append(self._main(program))
        return self.module

    def _declare_runtime(self) -> None:
        self.module.type_definitions.append('%jack.str = type { ptr, i32 }')
        self.module.declarations.extend([
            'declare i32 @printf(ptr, ...)',
            'declare void @abort() noreturn',
            'declare i32 @memcmp(ptr, ptr, i64)',
            'declare i16 @llvm.bswap.i16(i16)',
            'declare i32 @llvm.bswap.i32(i32)',
            'declare i64 @llvm.bswap.i64(i64)',
        ])

    def _declare_types(self) -> None:
        for declaration in self.types.values():
            fields = ', '.join(self._type(field.type_ref) for field in declaration.fields)
            if not fields:
                fields = 'i8'
            self.module.type_definitions.append(
                f'{self._named_type(declaration.name)} = type {{ {fields} }}'
            )
        for declaration in self.views.values():
            fields = ', '.join(self._view_field_type(field.type_ref) for field in declaration.fields)
            if not fields:
                fields = 'i8'
            self.module.type_definitions.append(
                f'{self._named_type(declaration.name)} = type {{ {fields} }}'
            )

    def _declare_error_payload(self) -> None:
        names = sorted(self.error_tags)
        self.error_payload_indices = {
            name: index for index, name in enumerate(names)
        }
        fields = ', '.join(self._named_type(name) for name in names) or 'i8'
        self.module.type_definitions.append(
            f'%jack.error.payload = type {{ {fields} }}'
        )

    def _declare_globals(self, program: HIRProgram) -> None:
        for declaration in program.declarations:
            if not isinstance(declaration, HIRGlobalVariable):
                continue
            symbol = declaration.symbol
            name = self._global_name(symbol.name)
            self.global_env[symbol.name] = (name, symbol.type_ref)
            type_name = self._type(symbol.type_ref)
            if symbol.extern:
                self.module.globals.append(f'{name} = external global {type_name}')
            else:
                self.module.globals.append(f'{name} = global {type_name} zeroinitializer')

    def _declare_functions(self, program: HIRProgram) -> None:
        declarations: list[tuple[HIRFunctionDeclaration, str | None]] = []
        for declaration in program.declarations:
            if isinstance(declaration, HIRFunctionDeclaration):
                declarations.append((declaration, None))
            elif isinstance(declaration, HIRTypeDeclaration):
                declarations.extend((method, declaration.name) for method in declaration.methods)
        for function, owner in declarations:
            return_type = self._function_return_type(function)
            parameters = self._function_parameter_types(function, owner)
            name = self._function_name(function, owner)
            if function.extern:
                params = ', '.join(parameters)
                self.module.declarations.append(
                    f'declare {return_type} @{quoted(name)}({params})'
                )

    def _function(
        self,
        declaration: HIRFunctionDeclaration,
        owner: str | None = None,
        debug_owner: str | None = None,
    ) -> LLVMFunction:
        builder = FunctionBuilder(self, declaration)
        self.builder = builder
        parameters: list[tuple[str, str]] = []
        symbols = []
        if declaration.self_parameter is not None:
            symbols.append(declaration.self_parameter)
        symbols.extend(declaration.parameters)
        for index, symbol in enumerate(symbols):
            type_name = self._parameter_type(symbol.type_ref)
            parameter_name = f'p{index}'
            parameters.append((type_name, parameter_name))
            slot = builder.alloca(type_name, 'arg')
            builder.emit(f'store {type_name} %{parameter_name}, ptr {slot}')
            builder.env[symbol.name] = (slot, symbol.type_ref)
        self._statements(declaration.body, builder.env)
        if not builder.terminated:
            self._return_success(None)
        function = LLVMFunction(
            self._function_name(declaration, owner),
            self._function_return_type(declaration),
            tuple(parameters),
            tuple((label, tuple(lines)) for label, lines in builder.blocks),
            span=declaration.span,
            debug_name=(
                f'{debug_owner or owner}.{declaration.source_name or declaration.name}'
                if owner is not None
                else declaration.source_name or declaration.name
            ),
        )
        self.builder = None
        return function

    def _main(self, program: HIRProgram) -> LLVMFunction:
        builder = FunctionBuilder(self, None)
        main_span = next(
            (
                statement.span
                for statement in program.top_level
                if statement.span is not None
                and statement.span.source_path is not None
            ),
            None,
        )
        builder.current_span = main_span
        self.builder = builder
        for statement in program.top_level:
            previous_span = builder.current_span
            builder.current_span = statement.span or previous_span
            if isinstance(statement, HIRGlobalVariable):
                if statement.initializer is not None:
                    value = self._expression(statement.initializer, builder.env)
                    value = self._coerce(value, statement.symbol.type_ref)
                    pointer, _ = builder.env[statement.symbol.name]
                    builder.emit(f'store {value.type_name} {value.operand}, ptr {pointer}')
                if statement.constructor_call is not None:
                    self._expression(statement.constructor_call, builder.env)
            elif not isinstance(statement, HIRDeclaration):
                self._statement(statement, builder.env)
            builder.current_span = previous_span
        if not builder.terminated:
            for declaration in reversed(program.declarations):
                if not isinstance(declaration, HIRGlobalVariable) or declaration.symbol.extern:
                    continue
                type_declaration = self.types.get(declaration.symbol.type_ref.name)
                if type_declaration is None:
                    continue
                deinit = next(
                    (method for method in type_declaration.methods if method.name == 'deinit'),
                    None,
                )
                if deinit is not None:
                    builder.emit(
                        f'call void @{quoted(f"{type_declaration.name}.deinit")}'
                        f'(ptr {self._global_name(declaration.symbol.name)})'
                    )
        if not builder.terminated:
            builder.terminate('ret i32 0')
        function = LLVMFunction(
            'main', 'i32', (),
            tuple((label, tuple(lines)) for label, lines in builder.blocks),
            span=main_span,
            debug_name='main',
        )
        self.builder = None
        return function

    def _statements(self, statements: list[HIRStatement], env: dict[str, tuple[str, TypeReference]]) -> None:
        for statement in statements:
            if self._b.terminated:
                break
            self._statement(statement, env)

    def _statement(self, statement: HIRStatement, env: dict[str, tuple[str, TypeReference]]) -> None:
        previous_span = self._b.current_span
        self._b.current_span = statement.span or previous_span
        try:
            self._statement_body(statement, env)
        finally:
            self._b.current_span = previous_span

    def _statement_body(
        self, statement: HIRStatement, env: dict[str, tuple[str, TypeReference]]
    ) -> None:
        if isinstance(statement, HIRVariableDeclaration):
            type_name = self._type(statement.symbol.type_ref)
            slot = self._b.alloca(type_name, 'local')
            env[statement.symbol.name] = (slot, statement.symbol.type_ref)
            self._b.emit(f'store {type_name} zeroinitializer, ptr {slot}')
            if statement.initializer is not None:
                value = self._expression(statement.initializer, env)
                value = self._coerce(value, statement.symbol.type_ref)
                self._b.emit(f'store {type_name} {value.operand}, ptr {slot}')
            if statement.constructor_call is not None:
                self._expression(statement.constructor_call, env)
            return
        if isinstance(statement, HIRAssignment):
            pointer, _ = self._lvalue(statement.target, env)
            value = self._expression(statement.expr, env)
            value = self._coerce(value, statement.target_type)
            self._b.emit(f'store {self._type(statement.target_type)} {value.operand}, ptr {pointer}')
            return
        if isinstance(statement, HIRExpressionStatement):
            self._expression(statement.expr, env)
            return
        if isinstance(statement, HIRPrint):
            self._print(statement, env)
            return
        if isinstance(statement, HIRReturn):
            value = None
            if statement.expr is not None:
                if self._b.declaration is not None and self._b.declaration.return_type.borrow is not None:
                    value = self._borrow_argument(statement.expr, env)
                else:
                    value = self._expression(statement.expr, env)
                    value = self._coerce(value, self._b.declaration.return_type)
            self._return_success(value)
            return
        if isinstance(statement, HIRRaise):
            value = self._expression(statement.expr, env)
            value = self._coerce(value, statement.error_type)
            payload = self._error_payload(value, statement.error_type.name)
            self._propagate_error(str(self.error_tags[statement.error_type.name]), payload)
            return
        if isinstance(statement, HIRRethrow):
            if not self._b.caught_errors:
                raise LLVMLoweringError(
                    'rethrow reached LLVM lowering outside a catch.', statement.span
                )
            tag, payload = self._b.caught_errors[-1]
            self._propagate_error(tag, payload)
            return
        if isinstance(statement, HIRBlock):
            self._statements(statement.body, dict(env))
            return
        if isinstance(statement, HIRIf):
            self._if(statement, env)
            return
        if isinstance(statement, HIRWhile):
            self._while(statement, env)
            return
        if isinstance(statement, HIRFor):
            self._for(statement, env)
            return
        if isinstance(statement, HIRTry):
            self._try(statement, env)
            return
        raise LLVMLoweringError(
            f'Unsupported HIR statement {type(statement).__name__}.', statement.span
        )

    def _if(self, statement: HIRIf, env) -> None:
        merge = self._b.label('if.end')
        next_test = None
        for index, branch in enumerate(statement.branches):
            if next_test is not None:
                self._b.start(next_test)
            body_label = self._b.label('if.body')
            next_test = self._b.label('if.next')
            condition = self._expression(branch.condition, env)
            self._b.terminate(f'br i1 {condition.operand}, label %{body_label}, label %{next_test}')
            self._b.start(body_label)
            self._statements(branch.body, dict(env))
            self._b.branch(merge)
        assert next_test is not None
        self._b.start(next_test)
        if statement.else_body is not None:
            self._statements(statement.else_body, dict(env))
        self._b.branch(merge)
        self._b.start(merge)

    def _while(self, statement: HIRWhile, env) -> None:
        condition_label = self._b.label('while.cond')
        body_label = self._b.label('while.body')
        end_label = self._b.label('while.end')
        self._b.branch(condition_label)
        self._b.start(condition_label)
        condition = self._expression(statement.condition, env)
        self._b.terminate(f'br i1 {condition.operand}, label %{body_label}, label %{end_label}')
        self._b.start(body_label)
        self._statements(statement.body, dict(env))
        self._b.branch(condition_label)
        self._b.start(end_label)

    def _for(self, statement: HIRFor, env) -> None:
        loop_env = dict(env)
        if statement.initializer is not None:
            self._statement(statement.initializer, loop_env)
        condition_label = self._b.label('for.cond')
        body_label = self._b.label('for.body')
        end_label = self._b.label('for.end')
        self._b.branch(condition_label)
        self._b.start(condition_label)
        if statement.condition is None:
            self._b.branch(body_label)
        else:
            condition = self._expression(statement.condition, loop_env)
            self._b.terminate(f'br i1 {condition.operand}, label %{body_label}, label %{end_label}')
        self._b.start(body_label)
        self._statements(statement.body, dict(loop_env))
        if statement.update is not None and not self._b.terminated:
            self._statement(statement.update, loop_env)
        self._b.branch(condition_label)
        self._b.start(end_label)

    def _try(self, statement: HIRTry, env) -> None:
        handler = self._b.label('try.error')
        merge = self._b.label('try.end')
        self._ensure_error_slots()
        self._b.error_handlers.append(handler)
        self._statements(statement.body, dict(env))
        self._b.error_handlers.pop()
        self._b.branch(merge)
        self._b.start(handler)
        tag = self._b.temp('error.tag')
        payload = self._b.temp('error.payload')
        self._b.emit(f'{tag} = load i32, ptr %jack.error.tag.slot')
        self._b.emit(
            f'{payload} = load %jack.error.payload, ptr %jack.error.payload.slot'
        )
        for catch in statement.catches:
            body_label = self._b.label('catch.body')
            next_label = self._b.label('catch.next')
            matches = self._b.temp('catch.match')
            self._b.emit(f'{matches} = icmp eq i32 {tag}, {self.error_tags[catch.error_type.name]}')
            self._b.terminate(f'br i1 {matches}, label %{body_label}, label %{next_label}')
            self._b.start(body_label)
            catch_env = dict(env)
            if catch.name is not None:
                type_name = self._type(catch.error_type)
                slot = self._b.alloca(type_name, 'caught')
                value = self._b.temp('caught')
                payload_index = self.error_payload_indices[catch.error_type.name]
                self._b.emit(
                    f'{value} = extractvalue %jack.error.payload {payload}, '
                    f'{payload_index}'
                )
                self._b.emit(f'store {type_name} {value}, ptr {slot}')
                catch_env[catch.name] = (slot, catch.error_type)
            self._b.caught_errors.append((tag, payload))
            self._statements(catch.body, catch_env)
            self._b.caught_errors.pop()
            self._b.branch(merge)
            self._b.start(next_label)
        self._propagate_error(tag, payload)
        self._b.start(merge)

    def _expression(self, expression: HIRExpression, env) -> LLVMValue:
        if isinstance(expression, HIRLiteralExpression):
            return self._literal(expression)
        if isinstance(expression, HIRVariableExpression):
            pointer, type_ref = env[expression.name]
            type_name = self._type(type_ref)
            value = self._b.temp('load')
            self._b.emit(f'{value} = load {type_name}, ptr {pointer}')
            if (
                type_ref.borrow is not None
                and type_ref.name not in self.views
                and not self._is_slice(type_ref)
                and type_ref.array_size is None
                and expression.read_type is not None
            ):
                read_type = self._type(expression.read_type)
                loaded = self._b.temp('borrowed')
                self._b.emit(f'{loaded} = load {read_type}, ptr {value}')
                return LLVMValue(read_type, loaded, expression.read_type)
            return LLVMValue(type_name, value, type_ref)
        if isinstance(expression, HIRFieldAccessExpression):
            pointer, type_ref = self._lvalue(expression, env)
            type_name = self._type(type_ref)
            value = self._b.temp('field')
            self._b.emit(f'{value} = load {type_name}, ptr {pointer}')
            return LLVMValue(type_name, value, type_ref)
        if isinstance(expression, HIRIndexExpression):
            pointer, type_ref = self._lvalue(expression, env)
            type_name = self._type(type_ref)
            value = self._b.temp('item')
            self._b.emit(f'{value} = load {type_name}, ptr {pointer}')
            return LLVMValue(type_name, value, type_ref)
        if isinstance(expression, HIRBorrowExpression):
            return self._borrow(expression, env)
        if isinstance(expression, HIRSliceExpression):
            return self._slice(expression, env, mutable=True)
        if isinstance(expression, HIRCompositeExpression):
            return self._composite(expression, env)
        if isinstance(expression, HIRCallExpression):
            return self._call(expression, env)
        if isinstance(expression, HIRStructLiteralExpression):
            return self._struct_literal(expression, env)
        raise LLVMLoweringError(
            f'Unsupported HIR expression {type(expression).__name__}.', expression.span
        )

    def _lvalue(self, expression: HIRExpression, env) -> tuple[str, TypeReference]:
        if isinstance(expression, HIRVariableExpression):
            return env[expression.name]
        if isinstance(expression, HIRFieldAccessExpression):
            target_ptr, target_type = self._lvalue_target(expression.target, env)
            owner = self.views.get(expression.owner_type_name) or self.types.get(expression.owner_type_name)
            if owner is None:
                raise LLVMLoweringError(
                    f'Unknown field owner {expression.owner_type_name}.', expression.span
                )
            fields = owner.fields
            index = next(i for i, field in enumerate(fields) if field.name == expression.field_name)
            pointer = self._b.temp('field.ptr')
            self._b.emit(
                f'{pointer} = getelementptr inbounds {self._named_type(expression.owner_type_name)}, '
                f'ptr {target_ptr}, i32 0, i32 {index}'
            )
            field_type = fields[index].type_ref
            if expression.from_view and not self._is_slice(field_type) and field_type.array_size is None:
                indirect = self._b.temp('view.ptr')
                self._b.emit(f'{indirect} = load ptr, ptr {pointer}')
                return indirect, field_type
            return pointer, field_type
        if isinstance(expression, HIRIndexExpression):
            index = self._expression(expression.index, env)
            index_operand = self._integer_as_i64(index)
            target_type = expression.target.type_ref
            element_type = self._element_type(target_type)
            if self._is_slice(target_type):
                target = self._expression(expression.target, env)
                data = self._b.temp('slice.data')
                self._b.emit(f'{data} = extractvalue {target.type_name} {target.operand}, 0')
                pointer = self._b.temp('item.ptr')
                self._b.emit(f'{pointer} = getelementptr {self._type(element_type)}, ptr {data}, i64 {index_operand}')
                return pointer, element_type
            target_ptr, _ = self._lvalue_target(expression.target, env)
            pointer = self._b.temp('item.ptr')
            if target_type.array_size is not None and target_type.borrow is None:
                self._b.emit(
                    f'{pointer} = getelementptr inbounds {self._type(target_type)}, ptr {target_ptr}, '
                    f'i32 0, i64 {index_operand}'
                )
            else:
                self._b.emit(f'{pointer} = getelementptr {self._type(element_type)}, ptr {target_ptr}, i64 {index_operand}')
            return pointer, element_type
        raise LLVMLoweringError(
            f'Expression {type(expression).__name__} is not assignable.', expression.span
        )

    def _lvalue_target(self, expression: HIRExpression, env) -> tuple[str, TypeReference]:
        if (
            isinstance(expression, HIRVariableExpression)
            and expression.type_ref.name in self.views
        ):
            return env[expression.name]
        if expression.type_ref.borrow is not None and not isinstance(expression, HIRBorrowExpression):
            value = self._borrow_argument(expression, env)
            return value.operand, self._element_type(expression.type_ref)
        return self._lvalue(expression, env)

    def _literal(self, expression: HIRLiteralExpression) -> LLVMValue:
        type_name = self._type(expression.type_ref)
        if expression.literal_type == 'str':
            data = str(expression.value).encode('utf-8')
            name = self._string(data)
            operand = (
                f'{{ ptr getelementptr inbounds ([{len(data) + 1} x i8], ptr {name}, i32 0, i32 0), '
                f'i32 {len(data)} }}'
            )
        elif expression.literal_type == 'bool':
            operand = 'true' if expression.value else 'false'
        elif expression.literal_type in {'f32', 'f64'}:
            operand = str(float(expression.value))
        else:
            operand = str(int(expression.value))
        return LLVMValue(type_name, operand, expression.type_ref)

    def _composite(self, expression: HIRCompositeExpression, env) -> LLVMValue:
        left = self._expression(expression.left, env)
        right = self._expression(expression.right, env)
        operator = expression.operator
        result_type = self._type(expression.type_ref)
        left_name = expression.left.type_ref.name
        if left_name == 'str':
            return self._string_compare(left, right, operator, expression.type_ref)
        float_op = left_name in {'f32', 'f64'}
        if operator in {'+', '-', '*', '/', '%'}:
            ops = ({'+': 'fadd', '-': 'fsub', '*': 'fmul', '/': 'fdiv', '%': 'frem'} if float_op
                   else {'+': 'add', '-': 'sub', '*': 'mul', '/': 'sdiv' if left_name.startswith('i') else 'udiv', '%': 'srem' if left_name.startswith('i') else 'urem'})
            instruction = ops[operator]
        else:
            if float_op:
                instruction = {'==': 'fcmp oeq', '!=': 'fcmp une', '<': 'fcmp olt', '<=': 'fcmp ole', '>': 'fcmp ogt', '>=': 'fcmp oge'}[operator]
            else:
                signed = left_name.startswith('i') or left_name in {'be_i32', 'le_i32'}
                instruction = {'==': 'icmp eq', '!=': 'icmp ne', '<': f'icmp {"slt" if signed else "ult"}', '<=': f'icmp {"sle" if signed else "ule"}', '>': f'icmp {"sgt" if signed else "ugt"}', '>=': f'icmp {"sge" if signed else "uge"}'}[operator]
        value = self._b.temp('op')
        self._b.emit(f'{value} = {instruction} {left.type_name} {left.operand}, {right.operand}')
        return LLVMValue(result_type, value, expression.type_ref)

    def _call(self, call: HIRCallExpression, env) -> LLVMValue:
        if call.target.kind == 'len':
            return self._len(call, env)
        if call.target.kind == 'builtin_conversion':
            return self._conversion(call, env)
        arguments: list[LLVMValue] = []
        if call.target.kind == 'method':
            if call.implicit_self_argument is not None:
                if call.target.self_parameter is None:
                    raise LLVMLoweringError(
                        f'Method {call.target.name} has no self parameter.', call.span
                    )
                arguments.append(
                    self._argument(
                        call.implicit_self_argument,
                        call.target.self_parameter.type_ref,
                        env,
                    )
                )
            elif call.receiver is not None:
                pointer, type_ref = self._lvalue(call.receiver, env)
                arguments.append(LLVMValue('ptr', pointer, TypeReference(type_ref.name, borrow='inout')))
        arguments.extend(
            self._argument(argument, parameter.type_ref, env)
            for parameter, argument in zip(call.target.parameters, call.arguments)
        )
        name = call.target.name
        args = ', '.join(f'{argument.type_name} {argument.operand}' for argument in arguments)
        return_type = self._function_call_return_type(call)
        if return_type == 'void':
            self._b.emit(f'call void @{quoted(name)}({args})')
            return LLVMValue('void', '', call.type_ref)
        result = self._b.temp('call')
        self._b.emit(f'{result} = call {return_type} @{quoted(name)}({args})')
        if call.target.raises:
            return self._unwrap_call(call, result, return_type)
        return LLVMValue(self._type(call.type_ref), result, call.type_ref)

    def _unwrap_call(self, call, result: str, result_type: str) -> LLVMValue:
        ok = self._b.temp('call.ok')
        self._b.emit(f'{ok} = extractvalue {result_type} {result}, 0')
        success = self._b.label('call.success')
        failure = self._b.label('call.error')
        self._b.terminate(f'br i1 {ok}, label %{success}, label %{failure}')
        self._b.start(failure)
        tag = self._b.temp('call.tag')
        payload = self._b.temp('call.payload')
        self._b.emit(f'{tag} = extractvalue {result_type} {result}, 1')
        self._b.emit(f'{payload} = extractvalue {result_type} {result}, 2')
        self._propagate_error(tag, payload)
        self._b.start(success)
        value_type = self._type(call.type_ref)
        if value_type == 'void':
            return LLVMValue('void', '', call.type_ref)
        value = self._b.temp('call.value')
        self._b.emit(f'{value} = extractvalue {result_type} {result}, 3')
        return LLVMValue(value_type, value, call.type_ref)

    def _borrow(self, expression: HIRBorrowExpression, env) -> LLVMValue:
        if isinstance(expression.expr, HIRSliceExpression):
            return self._slice(expression.expr, env, borrow_mode_can_write(expression.mode))
        if expression.type_ref.name in self.views:
            return self._view_borrow(expression, env)
        if self._is_slice(expression.expr.type_ref):
            return self._expression(expression.expr, env)
        pointer, _ = self._lvalue(expression.expr, env)
        return LLVMValue('ptr', pointer, expression.type_ref)

    def _argument(self, expression, expected_type, env) -> LLVMValue:
        if expected_type.borrow is not None:
            if expected_type.name in self.views and isinstance(expression, HIRBorrowExpression):
                return self._view_borrow(expression, env, expected_type.name)
            return self._borrow_argument(expression, env)
        return self._coerce(self._expression(expression, env), expected_type)

    def _borrow_argument(self, expression, env) -> LLVMValue:
        if isinstance(expression, HIRBorrowExpression):
            return self._borrow(expression, env)
        if isinstance(expression, HIRVariableExpression) and expression.type_ref.borrow is not None:
            slot, type_ref = env[expression.name]
            type_name = self._type(type_ref)
            value = self._b.temp('borrow')
            self._b.emit(f'{value} = load {type_name}, ptr {slot}')
            return LLVMValue(type_name, value, type_ref)
        return self._expression(expression, env)

    def _slice(self, expression: HIRSliceExpression, env, mutable: bool) -> LLVMValue:
        target_type = expression.target.type_ref
        element_type = self._element_type(target_type)
        start = LLVMValue('i64', '0', TypeReference('usize')) if expression.start is None else self._expression(expression.start, env)
        start64 = self._integer_as_i64(start)
        start32 = self._coerce(start, TypeReference('i32')).operand
        if self._is_slice(target_type):
            target = self._expression(expression.target, env)
            data = self._b.temp('slice.data')
            total = self._b.temp('slice.len')
            self._b.emit(f'{data} = extractvalue {target.type_name} {target.operand}, 0')
            self._b.emit(f'{total} = extractvalue {target.type_name} {target.operand}, 1')
        else:
            data, _ = self._lvalue_target(expression.target, env)
            total = str(target_type.array_size)
        pointer = data
        if start.operand != '0':
            pointer = self._b.temp('slice.start')
            self._b.emit(f'{pointer} = getelementptr {self._type(element_type)}, ptr {data}, i64 {start64}')
        if expression.end is None:
            length = total
            if start.operand != '0':
                length = self._b.temp('slice.length')
                self._b.emit(f'{length} = sub i32 {total}, {start32}')
        else:
            end = self._coerce(
                self._expression(expression.end, env), TypeReference('i32')
            )
            length = self._b.temp('slice.length')
            self._b.emit(f'{length} = sub i32 {end.operand}, {start32}')
        slice_type = self._type(expression.type_ref)
        first = self._b.temp('slice')
        value = self._b.temp('slice')
        self._b.emit(f'{first} = insertvalue {slice_type} poison, ptr {pointer}, 0')
        self._b.emit(f'{value} = insertvalue {slice_type} {first}, i32 {length}, 1')
        return LLVMValue(slice_type, value, expression.type_ref)

    def _struct_literal(self, expression: HIRStructLiteralExpression, env) -> LLVMValue:
        declaration = self.types[expression.type_ref.name]
        values = {field.name: self._expression(field.expr, env) for field in expression.fields}
        current = 'zeroinitializer'
        type_name = self._type(expression.type_ref)
        for index, field in enumerate(declaration.fields):
            if field.name not in values:
                continue
            next_value = self._b.temp('struct')
            value = self._coerce(values[field.name], field.type_ref)
            self._b.emit(f'{next_value} = insertvalue {type_name} {current}, {value.type_name} {value.operand}, {index}')
            current = next_value
        return LLVMValue(type_name, current, expression.type_ref)

    def _view_borrow(self, expression, env, view_name: str | None = None) -> LLVMValue:
        view_name = view_name or expression.type_ref.name
        declaration = self.views[view_name]
        current = 'zeroinitializer'
        type_name = self._named_type(view_name)
        for index, field in enumerate(declaration.fields):
            field_expr = HIRFieldAccessExpression(
                target=expression.expr, field_name=field.name,
                owner_type_name=expression.expr.type_ref.name,
                type_ref=field.type_ref, read_type=field.type_ref,
            )
            pointer, _ = self._lvalue(field_expr, env)
            value = self._b.temp('view')
            self._b.emit(f'{value} = insertvalue {type_name} {current}, ptr {pointer}, {index}')
            current = value
        return LLVMValue(type_name, current, expression.type_ref)

    def _conversion(self, call, env) -> LLVMValue:
        source = self._expression(call.arguments[0], env)
        target_type = self._type(call.type_ref)
        source_name = call.arguments[0].type_ref.name
        target_name = call.type_ref.name
        source_spec = BUILTIN_TYPE_SPECS[source_name]
        target_spec = BUILTIN_TYPE_SPECS[target_name]
        if (
            target_spec.family == 'raw'
            and source_spec.family in {'signed', 'unsigned', 'endian_signed'}
            and source_spec.bits == target_spec.bits
        ):
            needs_swap = (
                source_name == 'le_i32'
                or (source_name != 'be_i32' and sys.byteorder == 'little')
            )
            if needs_swap and source_spec.bits > 8:
                value = self._b.temp('byteswap')
                self._b.emit(
                    f'{value} = call i{source_spec.bits} @llvm.bswap.i{source_spec.bits}'
                    f'(i{source_spec.bits} {source.operand})'
                )
                return LLVMValue(target_type, value, call.type_ref)
            return LLVMValue(target_type, source.operand, call.type_ref)
        if source.type_name == target_type:
            return LLVMValue(target_type, source.operand, call.type_ref)
        value = self._b.temp('cast')
        if source_spec.family == 'float' and target_spec.family == 'float':
            op = 'fpext' if source_spec.bits < target_spec.bits else 'fptrunc'
        elif source_spec.family == 'float':
            op = 'fptosi' if target_spec.family in {'signed', 'endian_signed'} else 'fptoui'
        elif target_spec.family == 'float':
            op = 'sitofp' if source_spec.family in {'signed', 'endian_signed'} else 'uitofp'
        elif source_spec.bits < target_spec.bits:
            op = 'sext' if source_spec.family in {'signed', 'endian_signed'} else 'zext'
        else:
            op = 'trunc'
        self._b.emit(f'{value} = {op} {source.type_name} {source.operand} to {target_type}')
        return LLVMValue(target_type, value, call.type_ref)

    def _coerce(self, value: LLVMValue, target_ref: TypeReference) -> LLVMValue:
        target_type = self._type(target_ref)
        if value.type_name == target_type:
            return LLVMValue(target_type, value.operand, target_ref)
        source_spec = BUILTIN_TYPE_SPECS.get(value.type_ref.name)
        target_spec = BUILTIN_TYPE_SPECS.get(target_ref.name)
        if source_spec is None or target_spec is None:
            raise LLVMLoweringError(
                f'Cannot coerce {value.type_name} to {target_type} during LLVM lowering.',
                target_ref.span,
            )
        if value.operand.lstrip('-').isdigit() and target_spec.family != 'float':
            return LLVMValue(target_type, value.operand, target_ref)
        result = self._b.temp('coerce')
        if source_spec.family == 'float' and target_spec.family == 'float':
            operation = 'fpext' if source_spec.bits < target_spec.bits else 'fptrunc'
        elif source_spec.family == 'float':
            operation = 'fptosi' if target_spec.family in {'signed', 'endian_signed'} else 'fptoui'
        elif target_spec.family == 'float':
            operation = 'sitofp' if source_spec.family in {'signed', 'endian_signed'} else 'uitofp'
        elif source_spec.bits < target_spec.bits:
            operation = 'sext' if source_spec.family in {'signed', 'endian_signed'} else 'zext'
        elif source_spec.bits > target_spec.bits:
            operation = 'trunc'
        else:
            return LLVMValue(target_type, value.operand, target_ref)
        self._b.emit(
            f'{result} = {operation} {value.type_name} {value.operand} to {target_type}'
        )
        return LLVMValue(target_type, result, target_ref)

    def _len(self, call, env) -> LLVMValue:
        argument = call.arguments[0]
        inner = argument.expr if isinstance(argument, HIRBorrowExpression) else argument
        if isinstance(inner, HIRSliceExpression):
            value = self._slice(inner, env, True)
            length = self._b.temp('len')
            self._b.emit(f'{length} = extractvalue {value.type_name} {value.operand}, 1')
            return LLVMValue('i32', length, call.type_ref)
        if inner.type_ref.array_size is not None:
            return LLVMValue('i32', str(inner.type_ref.array_size), call.type_ref)
        value = self._expression(inner, env)
        length = self._b.temp('len')
        self._b.emit(f'{length} = extractvalue {value.type_name} {value.operand}, 1')
        return LLVMValue('i32', length, call.type_ref)

    def _print(self, statement: HIRPrint, env) -> None:
        if isinstance(statement.expr, HIRFormattedStringExpression):
            parts = statement.expr.parts
            format_text = ''
            values: list[LLVMValue] = []
            for part in parts:
                if isinstance(part, str):
                    format_text += part.replace('%', '%%')
                else:
                    value = self._expression(part, env)
                    spec, promoted = self._printf_value(value)
                    format_text += spec
                    values.extend(promoted)
        else:
            value = self._expression(statement.expr, env)
            spec, values = self._printf_value(value)
            label = statement.label or self._expression_label(statement.expr)
            format_text = f'{label} = {spec}' if label else spec
        format_text += '\n'
        format_ptr = self._cstring_pointer(format_text)
        arguments = ''.join(f', {value.type_name} {value.operand}' for value in values)
        self._b.emit(f'call i32 (ptr, ...) @printf(ptr {format_ptr}{arguments})')

    def _printf_value(self, value: LLVMValue) -> tuple[str, list[LLVMValue]]:
        name = value.type_ref.name
        if name == 'str':
            data = self._b.temp('str.data')
            length = self._b.temp('str.len')
            self._b.emit(f'{data} = extractvalue %jack.str {value.operand}, 0')
            self._b.emit(f'{length} = extractvalue %jack.str {value.operand}, 1')
            return '%.*s', [LLVMValue('i32', length, TypeReference('i32')), LLVMValue('ptr', data, TypeReference('str'))]
        spec = BUILTIN_TYPE_SPECS[name]
        if name == 'bool':
            true_ptr = self._cstring_pointer('true')
            false_ptr = self._cstring_pointer('false')
            selected = self._b.temp('bool.text')
            self._b.emit(f'{selected} = select i1 {value.operand}, ptr {true_ptr}, ptr {false_ptr}')
            return '%s', [LLVMValue('ptr', selected, TypeReference('str'))]
        if spec.family == 'float':
            if value.type_name == 'float':
                promoted = self._b.temp('float')
                self._b.emit(f'{promoted} = fpext float {value.operand} to double')
                value = LLVMValue('double', promoted, value.type_ref)
            return ('%.17g' if name == 'f64' else '%.9g'), [value]
        if spec.bits < 32:
            promoted = self._b.temp('integer')
            op = 'sext' if spec.family in {'signed', 'endian_signed'} else 'zext'
            self._b.emit(f'{promoted} = {op} {value.type_name} {value.operand} to i32')
            value = LLVMValue('i32', promoted, value.type_ref)
        if spec.family == 'raw':
            suffix = 'llx' if spec.bits == 64 else 'x'
            return f'0x%0{spec.printf_width}{suffix}', [value]
        if spec.family in {'unsigned'}:
            return ('%llu' if spec.bits == 64 else '%u'), [value]
        return ('%lld' if spec.bits == 64 else '%d'), [value]

    def _string_compare(self, left, right, operator, result_ref) -> LLVMValue:
        left_data, left_len = self._str_parts(left)
        right_data, right_len = self._str_parts(right)
        lengths = self._b.temp('str.lengths')
        self._b.emit(f'{lengths} = icmp eq i32 {left_len}, {right_len}')
        length64 = self._b.temp('str.length')
        self._b.emit(f'{length64} = zext i32 {left_len} to i64')
        compared = self._b.temp('str.compare')
        self._b.emit(f'{compared} = call i32 @memcmp(ptr {left_data}, ptr {right_data}, i64 {length64})')
        equal_data = self._b.temp('str.equal')
        self._b.emit(f'{equal_data} = icmp eq i32 {compared}, 0')
        equal = self._b.temp('str.equal')
        self._b.emit(f'{equal} = and i1 {lengths}, {equal_data}')
        if operator == '!=':
            result = self._b.temp('str.not.equal')
            self._b.emit(f'{result} = xor i1 {equal}, true')
            equal = result
        return LLVMValue('i1', equal, result_ref)

    def _str_parts(self, value):
        data = self._b.temp('str.data')
        length = self._b.temp('str.len')
        self._b.emit(f'{data} = extractvalue %jack.str {value.operand}, 0')
        self._b.emit(f'{length} = extractvalue %jack.str {value.operand}, 1')
        return data, length

    def _error_payload(self, value: LLVMValue, error_name: str) -> str:
        payload = self._b.temp('payload')
        index = self.error_payload_indices[error_name]
        self._b.emit(
            f'{payload} = insertvalue %jack.error.payload zeroinitializer, '
            f'{value.type_name} {value.operand}, {index}'
        )
        return payload

    def _propagate_error(self, tag: str, payload: str) -> None:
        if self._b.error_handlers:
            self._ensure_error_slots()
            self._b.emit(f'store i32 {tag}, ptr %jack.error.tag.slot')
            self._b.emit(
                f'store %jack.error.payload {payload}, ptr %jack.error.payload.slot'
            )
            self._b.terminate(f'br label %{self._b.error_handlers[-1]}')
            return
        declaration = self._b.declaration
        if declaration is None or not declaration.raises:
            self._b.emit('call void @abort()')
            self._b.terminate('unreachable')
            return
        result_type = self._function_return_type(declaration)
        first = self._b.temp('error.result')
        second = self._b.temp('error.result')
        third = self._b.temp('error.result')
        self._b.emit(f'{first} = insertvalue {result_type} zeroinitializer, i1 false, 0')
        self._b.emit(f'{second} = insertvalue {result_type} {first}, i32 {tag}, 1')
        self._b.emit(
            f'{third} = insertvalue {result_type} {second}, '
            f'%jack.error.payload {payload}, 2'
        )
        self._b.terminate(f'ret {result_type} {third}')

    def _return_success(self, value: LLVMValue | None) -> None:
        declaration = self._b.declaration
        if declaration is None:
            self._b.terminate('ret i32 0')
            return
        if not declaration.raises:
            if self._type(declaration.return_type) == 'void':
                self._b.terminate('ret void')
            else:
                operand = value.operand if value is not None else 'zeroinitializer'
                self._b.terminate(f'ret {self._type(declaration.return_type)} {operand}')
            return
        result_type = self._function_return_type(declaration)
        current = self._b.temp('success')
        self._b.emit(f'{current} = insertvalue {result_type} zeroinitializer, i1 true, 0')
        if self._type(declaration.return_type) != 'void' and value is not None:
            next_value = self._b.temp('success')
            self._b.emit(f'{next_value} = insertvalue {result_type} {current}, {value.type_name} {value.operand}, 3')
            current = next_value
        self._b.terminate(f'ret {result_type} {current}')

    def _ensure_error_slots(self) -> None:
        if self._b.has_error_slots:
            return
        self._b.named_alloca('%jack.error.tag.slot', 'i32')
        self._b.named_alloca('%jack.error.payload.slot', '%jack.error.payload')
        self._b.has_error_slots = True

    def _collect_error_tags(self, program: HIRProgram) -> None:
        functions = list(self.functions.values())
        for declaration in program.declarations:
            if isinstance(declaration, HIRTypeDeclaration):
                functions.extend(declaration.methods)
        names = sorted({error.name for function in functions for error in function.raises})
        self.error_tags = {name: index + 1 for index, name in enumerate(names)}

    def _function_return_type(self, declaration: HIRFunctionDeclaration) -> str:
        value_type = self._type(declaration.return_type)
        if not declaration.raises:
            return value_type
        key = value_type.replace(' ', '').replace('%', '').replace('"', '').replace('[', 'a').replace(']', '')
        name = self.result_types.get(key)
        if name is None:
            name = f'%jack.result.{len(self.result_types)}'
            fields = 'i1, i32, %jack.error.payload'
            if value_type != 'void':
                fields += f', {value_type}'
            self.module.type_definitions.append(f'{name} = type {{ {fields} }}')
            self.result_types[key] = name
        return name

    def _function_call_return_type(self, call: HIRCallExpression) -> str:
        declaration = HIRFunctionDeclaration(
            name=call.target.name, parameters=call.target.parameters, body=[],
            return_type=call.target.return_type, raises=call.target.raises,
        )
        return self._function_return_type(declaration)

    def _function_parameter_types(self, declaration, owner):
        values = []
        if declaration.self_parameter is not None:
            values.append(self._parameter_type(declaration.self_parameter.type_ref))
        values.extend(self._parameter_type(parameter.type_ref) for parameter in declaration.parameters)
        return values

    def _parameter_type(self, type_ref: TypeReference) -> str:
        return self._type(type_ref)

    def _function_name(self, declaration, owner):
        return f'{owner}.{declaration.name}' if owner is not None else declaration.name

    def _type(self, type_ref: TypeReference) -> str:
        if type_ref.borrow is not None:
            if type_ref.name in self.views:
                return self._named_type(type_ref.name)
            if self._is_slice(type_ref):
                return self._slice_type(type_ref)
            return 'ptr'
        if type_ref.array_size is not None:
            return f'[{int(type_ref.array_size)} x {self._type(self._element_type(type_ref))}]'
        if self._is_slice(type_ref):
            return self._slice_type(type_ref)
        name = type_ref.name
        if name == 'void':
            return 'void'
        if name == 'str':
            return '%jack.str'
        if name in BUILTIN_TYPE_SPECS:
            spec = BUILTIN_TYPE_SPECS[name]
            if spec.family == 'float':
                return 'float' if spec.bits == 32 else 'double'
            return f'i{spec.bits}'
        if name in self.types or name in self.views:
            return self._named_type(name)
        if name in {'c_void', 'c_char'}:
            return 'i8'
        return 'ptr'

    def _slice_type(self, type_ref: TypeReference) -> str:
        key = self._type(self._element_type(type_ref)).replace(' ', '').replace('%', '').replace('"', '')
        name = f'%jack.slice.{key}'
        definition = f'{name} = type {{ ptr, i32 }}'
        if definition not in self.module.type_definitions:
            self.module.type_definitions.append(definition)
        return name

    def _view_field_type(self, type_ref):
        return self._type(type_ref) if self._is_slice(type_ref) else 'ptr'

    def _element_type(self, type_ref: TypeReference) -> TypeReference:
        return TypeReference(type_ref.name, list(type_ref.arguments))

    def _is_slice(self, type_ref):
        return bool(type_ref.is_slice)

    def _named_type(self, name):
        return f'%{quoted(name)}'

    def _global_name(self, name):
        return f'@{quoted(name)}'

    def _string(self, data: bytes) -> str:
        existing = self.string_globals.get(data)
        if existing is not None:
            return existing
        name = f'@.str.{len(self.string_globals)}'
        escaped = ''.join(chr(byte) if 32 <= byte < 127 and byte not in {34, 92} else f'\\{byte:02X}' for byte in data) + '\\00'
        self.module.globals.append(f'{name} = private unnamed_addr constant [{len(data) + 1} x i8] c"{escaped}"')
        self.string_globals[data] = name
        return name

    def _cstring_pointer(self, text):
        data = text.encode('utf-8')
        name = self._string(data)
        return f'getelementptr inbounds ([{len(data) + 1} x i8], ptr {name}, i32 0, i32 0)'

    def _as_i32(self, operand: str) -> str:
        if operand.lstrip('-').isdigit() and -(1 << 31) <= int(operand) < (1 << 31):
            return operand
        value = self._b.temp('i32')
        self._b.emit(f'{value} = trunc i64 {operand} to i32')
        return value

    def _integer_as_i64(self, value: LLVMValue) -> str:
        if value.type_name == 'i64':
            return value.operand
        if value.operand.lstrip('-').isdigit():
            return value.operand
        result = self._b.temp('index')
        spec = BUILTIN_TYPE_SPECS.get(value.type_ref.name)
        operation = (
            'sext'
            if spec is not None and spec.family in {'signed', 'endian_signed'}
            else 'zext'
        )
        self._b.emit(f'{result} = {operation} {value.type_name} {value.operand} to i64')
        return result

    def _expression_label(self, expression):
        if isinstance(expression, HIRVariableExpression):
            return expression.name
        if isinstance(expression, HIRFieldAccessExpression):
            return f'{self._expression_label(expression.target)}.{expression.field_name}'
        if isinstance(expression, HIRCallExpression):
            return f'{expression.target.name}()'
        return ''

    @property
    def _b(self) -> FunctionBuilder:
        if self.builder is None:
            raise LLVMLoweringError('LLVM instruction emitted outside a function.')
        return self.builder


def lower_to_llvm(
    program: HIRProgram, *, debug: bool = False, optimization: int = 0
) -> LLVMModule:
    return LLVMLoweringPass(
        debug=debug, optimization=optimization
    ).lower(program)
