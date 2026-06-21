from __future__ import annotations

from dataclasses import dataclass

from .ir import (
    IRAssignment,
    IRBinary,
    IRCall,
    IRDeclaration,
    IRExpression,
    IRExpressionStatement,
    IRFunction,
    IRGlobal,
    IRLiteral,
    IRModule,
    IRPrint,
    IRReturn,
    IRVariable,
)


class LLVMEmitError(ValueError):
    pass


@dataclass
class EmittedValue:
    type: str
    value: str


class LLVMEmitter:
    def __init__(self):
        self._lines: list[str] = []
        self._temp_id = 0
        self._locals: dict[str, str] = {}
        self._globals: dict[str, str] = {}

    def emit(self, module: IRModule) -> str:
        self._lines = []
        self._globals = {global_.name: global_.type for global_ in module.globals}

        self._emit_runtime_declarations()
        for global_ in module.globals:
            self._emit_global(global_)
        if module.globals:
            self._lines.append("")

        for function in module.functions:
            self._emit_function(function)
            self._lines.append("")

        return "\n".join(self._lines).rstrip() + "\n"

    def _emit_runtime_declarations(self) -> None:
        self._lines.extend(
            [
                'target triple = "x86_64-pc-linux-gnu"',
                "",
                '@.fmt_i32 = private unnamed_addr constant [4 x i8] c"%d\\0A\\00"',
                '@.fmt_f64 = private unnamed_addr constant [4 x i8] c"%f\\0A\\00"',
                '@.fmt_string = private unnamed_addr constant [4 x i8] c"%s\\0A\\00"',
                "declare i32 @printf(i8*, ...)",
                "",
            ]
        )

    def _emit_global(self, global_: IRGlobal) -> None:
        self._lines.append(
            f"@{global_.name} = global {self._llvm_type(global_.type)} "
            f"{self._literal_value(global_.initializer)}"
        )

    def _emit_function(self, function: IRFunction) -> None:
        self._temp_id = 0
        self._locals = {}
        params = ", ".join(
            f"{self._llvm_type(param.type)} %{param.name}"
            for param in function.parameters
        )
        self._lines.append(
            f"define {self._llvm_type(function.return_type)} @{function.name}({params}) {{"
        )
        self._lines.append("entry:")

        for param in function.parameters:
            pointer = f"%{param.name}.addr"
            self._locals[param.name] = pointer
            self._line(f"{pointer} = alloca {self._llvm_type(param.type)}")
            self._line(
                f"store {self._llvm_type(param.type)} %{param.name}, "
                f"{self._llvm_type(param.type)}* {pointer}"
            )

        has_return = False
        for statement in function.body:
            if has_return:
                break
            has_return = self._emit_statement(statement)

        if not has_return:
            self._line(f"ret {self._llvm_type(function.return_type)} 0")

        self._lines.append("}")

    def _emit_statement(self, statement) -> bool:
        if isinstance(statement, IRDeclaration):
            pointer = f"%{statement.name}.addr"
            self._locals[statement.name] = pointer
            self._line(f"{pointer} = alloca {self._llvm_type(statement.type)}")
            return False
        if isinstance(statement, IRAssignment):
            value = self._emit_expression(statement.value)
            pointer = self._pointer_for(statement.name)
            self._line(
                f"store {self._llvm_type(statement.type)} {value.value}, "
                f"{self._llvm_type(statement.type)}* {pointer}"
            )
            return False
        if isinstance(statement, IRPrint):
            self._emit_print(statement.expression)
            return False
        if isinstance(statement, IRExpressionStatement):
            self._emit_expression(statement.expression)
            return False
        if isinstance(statement, IRReturn):
            value = self._emit_expression(statement.expression)
            self._line(f"ret {self._llvm_type(statement.expression.type)} {value.value}")
            return True
        raise LLVMEmitError(f"Unsupported IR statement: {statement.__class__.__name__}")

    def _emit_expression(self, expression: IRExpression) -> EmittedValue:
        if isinstance(expression, IRLiteral):
            return EmittedValue(expression.type, self._literal_value(expression))
        if isinstance(expression, IRVariable):
            pointer = self._pointer_for(expression.name)
            temp = self._temp()
            self._line(
                f"{temp} = load {self._llvm_type(expression.type)}, "
                f"{self._llvm_type(expression.type)}* {pointer}"
            )
            return EmittedValue(expression.type, temp)
        if isinstance(expression, IRBinary):
            left = self._emit_expression(expression.left)
            right = self._emit_expression(expression.right)
            temp = self._temp()
            self._line(
                f"{temp} = {self._binary_instruction(expression)} "
                f"{self._llvm_type(expression.type)} {left.value}, {right.value}"
            )
            return EmittedValue(expression.type, temp)
        if isinstance(expression, IRCall):
            args = ", ".join(
                f"{self._llvm_type(arg.type)} {self._emit_expression(arg).value}"
                for arg in expression.arguments
            )
            temp = self._temp()
            self._line(
                f"{temp} = call {self._llvm_type(expression.type)} @{expression.name}({args})"
            )
            return EmittedValue(expression.type, temp)
        raise LLVMEmitError(f"Unsupported IR expression: {expression.__class__.__name__}")

    def _emit_print(self, expression: IRExpression) -> None:
        value = self._emit_expression(expression)
        if value.type == "i32":
            format_ref = "getelementptr inbounds ([4 x i8], [4 x i8]* @.fmt_i32, i32 0, i32 0)"
        elif value.type == "f64":
            format_ref = "getelementptr inbounds ([4 x i8], [4 x i8]* @.fmt_f64, i32 0, i32 0)"
        elif value.type == "string":
            format_ref = "getelementptr inbounds ([4 x i8], [4 x i8]* @.fmt_string, i32 0, i32 0)"
        else:
            raise LLVMEmitError(f"Cannot print value of type: {value.type}")
        self._line(
            f"call i32 (i8*, ...) @printf(i8* {format_ref}, "
            f"{self._llvm_type(value.type)} {value.value})"
        )

    def _binary_instruction(self, expression: IRBinary) -> str:
        if expression.type == "i32":
            return {
                "+": "add",
                "-": "sub",
                "*": "mul",
                "/": "sdiv",
            }[expression.operator]
        if expression.type == "f64":
            return {
                "+": "fadd",
                "-": "fsub",
                "*": "fmul",
                "/": "fdiv",
            }[expression.operator]
        raise LLVMEmitError(f"Cannot lower binary operator for type: {expression.type}")

    def _pointer_for(self, name: str) -> str:
        if name in self._locals:
            return self._locals[name]
        if name in self._globals:
            return f"@{name}"
        raise LLVMEmitError(f"Unknown variable: {name}")

    def _literal_value(self, literal: IRLiteral) -> str:
        if literal.type == "i32":
            return literal.value
        if literal.type == "f64":
            return literal.value
        if literal.type == "string":
            raise LLVMEmitError("String literal storage is not implemented yet")
        raise LLVMEmitError(f"Unsupported literal type: {literal.type}")

    def _llvm_type(self, type_: str) -> str:
        if type_ == "i32":
            return "i32"
        if type_ == "f64":
            return "double"
        if type_ == "string":
            return "i8*"
        raise LLVMEmitError(f"Unsupported type: {type_}")

    def _temp(self) -> str:
        self._temp_id += 1
        return f"%t{self._temp_id}"

    def _line(self, line: str) -> None:
        self._lines.append(f"  {line}")


def emit_llvm(module: IRModule) -> str:
    return LLVMEmitter().emit(module)
