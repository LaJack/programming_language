from __future__ import annotations

from .ast_nodes import (
    Assignment,
    CompositeExpression,
    Declaration,
    Expression,
    ExpressionStatement,
    FunctionCall,
    FunctionDefinition,
    Literal,
    Print,
    Return,
    Statement,
    Variable,
)
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
    IRParameter,
    IRPrint,
    IRReturn,
    IRStatement,
    IRVariable,
)


class LoweringError(ValueError):
    pass


class Lowerer:
    def __init__(self):
        self._globals: dict[str, str] = {}
        self._functions: dict[str, str] = {}

    def lower(self, ast: list[Statement]) -> IRModule:
        self._collect_signatures(ast)

        globals_ = [
            IRGlobal(name, type_, self._default_literal(type_))
            for name, type_ in self._globals.items()
        ]
        functions = [
            self._lower_function(stmt)
            for stmt in ast
            if isinstance(stmt, FunctionDefinition)
        ]
        main = self._lower_main(ast)
        return IRModule(globals_, [*functions, main])

    def _collect_signatures(self, ast: list[Statement]) -> None:
        for stmt in ast:
            if isinstance(stmt, Declaration):
                self._globals[stmt.variable.name] = stmt.type
            elif isinstance(stmt, FunctionDefinition):
                self._functions[stmt.name] = stmt.return_type

    def _lower_main(self, ast: list[Statement]) -> IRFunction:
        body: list[IRStatement] = []
        locals_: dict[str, str] = {}

        for stmt in ast:
            if isinstance(stmt, (Declaration, FunctionDefinition)):
                continue
            lowered = self._lower_statement(stmt, locals_)
            if lowered is not None:
                body.append(lowered)

        body.append(IRReturn(IRLiteral("i32", "0")))
        return IRFunction("main", "i32", [], body)

    def _lower_function(self, definition: FunctionDefinition) -> IRFunction:
        locals_ = {param.name: param.type for param in definition.parameters}
        body: list[IRStatement] = []

        for stmt in definition.body:
            lowered = self._lower_statement(stmt, locals_)
            if lowered is not None:
                body.append(lowered)

        if not body or not isinstance(body[-1], IRReturn):
            body.append(IRReturn(self._default_literal(definition.return_type)))

        return IRFunction(
            definition.name,
            definition.return_type,
            [IRParameter(param.name, param.type) for param in definition.parameters],
            body,
        )

    def _lower_statement(
        self, stmt: Statement, locals_: dict[str, str]
    ) -> IRStatement | None:
        if isinstance(stmt, Declaration):
            locals_[stmt.variable.name] = stmt.type
            return IRDeclaration(stmt.variable.name, stmt.type)
        if isinstance(stmt, Assignment):
            type_ = self._lookup_variable_type(stmt.variable.name, locals_)
            return IRAssignment(
                stmt.variable.name,
                type_,
                self._lower_expression(stmt.value, locals_),
            )
        if isinstance(stmt, Print):
            return IRPrint(self._lower_expression(stmt.expression, locals_))
        if isinstance(stmt, ExpressionStatement):
            return IRExpressionStatement(self._lower_expression(stmt.expression, locals_))
        if isinstance(stmt, Return):
            return IRReturn(self._lower_expression(stmt.expression, locals_))
        raise LoweringError(f"Unsupported statement: {stmt.__class__.__name__}")

    def _lower_expression(
        self, expression: Expression, locals_: dict[str, str]
    ) -> IRExpression:
        if isinstance(expression, Literal):
            return IRLiteral(expression.type, expression.value)
        if isinstance(expression, Variable):
            return IRVariable(
                self._lookup_variable_type(expression.name, locals_),
                expression.name,
            )
        if isinstance(expression, CompositeExpression):
            left = self._lower_expression(expression.first_operand, locals_)
            right = self._lower_expression(expression.second_operand, locals_)
            if left.type != right.type:
                raise LoweringError(
                    f"Cannot lower binary expression with {left.type} and {right.type}"
                )
            return IRBinary(left.type, expression.operator, left, right)
        if isinstance(expression, FunctionCall):
            if expression.name not in self._functions:
                raise LoweringError(f"Unknown function: {expression.name}")
            return IRCall(
                self._functions[expression.name],
                expression.name,
                [self._lower_expression(arg, locals_) for arg in expression.arguments],
            )
        raise LoweringError(f"Unsupported expression: {expression.__class__.__name__}")

    def _lookup_variable_type(self, name: str, locals_: dict[str, str]) -> str:
        if "." in name:
            raise LoweringError(f"Struct field lowering is not implemented yet: {name}")
        if name in locals_:
            return locals_[name]
        if name in self._globals:
            return self._globals[name]
        raise LoweringError(f"Unknown variable: {name}")

    def _default_literal(self, type_: str) -> IRLiteral:
        if type_ == "i32":
            return IRLiteral("i32", "0")
        if type_ == "f64":
            return IRLiteral("f64", "0.0")
        if type_ == "string":
            return IRLiteral("string", "")
        raise LoweringError(f"Unsupported type: {type_}")


def lower(ast: list[Statement]) -> IRModule:
    return Lowerer().lower(ast)
