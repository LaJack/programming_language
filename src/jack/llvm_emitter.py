from __future__ import annotations

from dataclasses import dataclass
import re

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
    IRIf,
    IRWhile,
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
        name_repr = self._format_global_name(global_.name)
        self._lines.append(
            f"{name_repr} = global {self._llvm_type(global_.type)} "
            f"{self._literal_value(global_.initializer)}"
        )

    def _emit_function(self, function: IRFunction) -> None:
        self._temp_id = 0
        self._label_id = 0
        self._locals = {}
        params = ", ".join(
            f"{self._llvm_type(param.type)} %{param.name}"
            for param in function.parameters
        )
        name_repr = self._format_global_name(function.name)
        self._lines.append(
            f"define {self._llvm_type(function.return_type)} {name_repr}({params}) {{"
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
        if isinstance(statement, IRIf):
            # Evaluate condition
            cond_val = self._emit_expression(statement.condition)
            if cond_val.type == "i32":
                cmp_tmp = self._temp()
                self._line(f"{cmp_tmp} = icmp ne i32 {cond_val.value}, 0")
            elif cond_val.type == "f64":
                cmp_tmp = self._temp()
                self._line(f"{cmp_tmp} = fcmp one double {cond_val.value}, 0.0")
            else:
                raise LLVMEmitError(f"Unsupported condition type: {cond_val.type}")

            then_label = f"then{self._label_id}"
            else_label = f"else{self._label_id}"
            end_label = f"ifend{self._label_id}"
            self._label_id += 1

            self._line(f"br i1 {cmp_tmp}, label %{then_label}, label %{else_label}")

            # then block
            self._lines.append(f"{then_label}:")
            then_returned = False
            for st in statement.then_body:
                if then_returned:
                    break
                then_returned = self._emit_statement(st)
            if not then_returned:
                self._line(f"br label %{end_label}")

            # else block
            self._lines.append(f"{else_label}:")
            else_returned = False
            for st in statement.else_body:
                if else_returned:
                    break
                else_returned = self._emit_statement(st)
            if not else_returned:
                self._line(f"br label %{end_label}")

            # end label
            self._lines.append(f"{end_label}:")
            return False
        if isinstance(statement, IRWhile):
            loop_cond = f"loopcond{self._label_id}"
            loop_body = f"loopbody{self._label_id}"
            loop_end = f"loopend{self._label_id}"
            self._label_id += 1

            # jump to condition check
            self._line(f"br label %{loop_cond}")

            # condition
            self._lines.append(f"{loop_cond}:")
            cond_val = self._emit_expression(statement.condition)
            if cond_val.type == "i32":
                cmp_tmp = self._temp()
                self._line(f"{cmp_tmp} = icmp ne i32 {cond_val.value}, 0")
            elif cond_val.type == "f64":
                cmp_tmp = self._temp()
                self._line(f"{cmp_tmp} = fcmp one double {cond_val.value}, 0.0")
            else:
                raise LLVMEmitError(f"Unsupported condition type: {cond_val.type}")

            self._line(f"br i1 {cmp_tmp}, label %{loop_body}, label %{loop_end}")

            # body
            self._lines.append(f"{loop_body}:")
            body_returned = False
            for st in statement.body:
                if body_returned:
                    break
                body_returned = self._emit_statement(st)
            if not body_returned:
                self._line(f"br label %{loop_cond}")

            # end label
            self._lines.append(f"{loop_end}:")
            return False
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
            op = expression.operator
            # Arithmetic operations
            if op in ("+", "-", "*", "/"):
                temp = self._temp()
                self._line(
                    f"{temp} = {self._binary_instruction(expression)} "
                    f"{self._llvm_type(expression.type)} {left.value}, {right.value}"
                )
                return EmittedValue(expression.type, temp)

            # Comparison operators -> produce i32 0/1
            if op in ("==", "!=", "<", "<=", ">", ">="):
                # Determine operand type (left.type)
                if left.type == "i32":
                    icmp_map = {"==": "eq", "!=": "ne", "<": "slt", "<=": "sle", ">": "sgt", ">=": "sge"}
                    cmp_tmp = self._temp()
                    self._line(f"{cmp_tmp} = icmp {icmp_map[op]} i32 {left.value}, {right.value}")
                elif left.type == "f64":
                    fcmp_map = {"==": "oeq", "!=": "one", "<": "olt", "<=": "ole", ">": "ogt", ">=": "oge"}
                    cmp_tmp = self._temp()
                    self._line(f"{cmp_tmp} = fcmp {fcmp_map[op]} double {left.value}, {right.value}")
                else:
                    raise LLVMEmitError(f"Unsupported comparison operand type: {left.type}")
                res_tmp = self._temp()
                self._line(f"{res_tmp} = zext i1 {cmp_tmp} to i32")
                return EmittedValue("i32", res_tmp)

            # Logical operators (non-short-circuit): evaluate operands to booleans then combine
            if op in ("&&", "||"):
                # left truthiness
                if left.type == "i32":
                    lb = self._temp()
                    self._line(f"{lb} = icmp ne i32 {left.value}, 0")
                elif left.type == "f64":
                    lb = self._temp()
                    self._line(f"{lb} = fcmp one double {left.value}, 0.0")
                else:
                    raise LLVMEmitError(f"Unsupported logical operand type: {left.type}")

                if right.type == "i32":
                    rb = self._temp()
                    self._line(f"{rb} = icmp ne i32 {right.value}, 0")
                elif right.type == "f64":
                    rb = self._temp()
                    self._line(f"{rb} = fcmp one double {right.value}, 0.0")
                else:
                    raise LLVMEmitError(f"Unsupported logical operand type: {right.type}")

                comb = self._temp()
                if op == "&&":
                    self._line(f"{comb} = and i1 {lb}, {rb}")
                else:
                    self._line(f"{comb} = or i1 {lb}, {rb}")

                res_tmp = self._temp()
                self._line(f"{res_tmp} = zext i1 {comb} to i32")
                return EmittedValue("i32", res_tmp)

            raise LLVMEmitError(f"Unsupported IR binary operator: {op}")
        if isinstance(expression, IRCall):
            args = ", ".join(
                f"{self._llvm_type(arg.type)} {self._emit_expression(arg).value}"
                for arg in expression.arguments
            )
            temp = self._temp()
            name_repr = self._format_global_name(expression.name)
            self._line(
                f"{temp} = call {self._llvm_type(expression.type)} {name_repr}({args})"
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
            return self._format_global_name(name)
        raise LLVMEmitError(f"Unknown variable: {name}")

    def _format_global_name(self, name: str) -> str:
        # LLVM identifiers must be quoted if they contain characters that are
        # not allowed in plain identifiers. Use a simple check and otherwise
        # emit a quoted name to ensure validity when names contain '#'.
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.$]*$", name):
            return f"@{name}"
        # Escape any double quotes in the name
        safe = name.replace('"', '\\"')
        return f'@"{safe}"'

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
