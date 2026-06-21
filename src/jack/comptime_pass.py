import re
from typing import Dict, List

from .ast_nodes import (
    Assignment,
    CompositeExpression,
    Declaration,
    Definition,
    ExpressionStatement,
    Expression,
    FunctionCall,
    FunctionDefinition,
    Literal,
    Print,
    Return,
    Statement,
    Variable,
)


class ComptimePass:
    """
    A compile-time pass that evaluates `comptime` statements and returns a
    transformed AST. This implementation does not call the runtime
    `Interpreter` directly; instead it keeps a small internal type/variable
    registry sufficient to evaluate expressions used at compile time.
    """

    def __init__(self):
        # Registry of types: builtin primitives map to None, user types map
        # to a dict of field_name->field_type.
        self._types: Dict[str, Dict[str, str] | None] = {
            "i32": None,
            "f64": None,
            "string": None,
        }
        # Variable environment: variable name -> Python value or dict for
        # composite (user-defined) types.
        self._vars: Dict[str, object] = {}
        self._functions: Dict[str, FunctionDefinition] = {}
        self._specializations: Dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}
        self._pending_specializations: List[FunctionDefinition] = []

    def _value_to_literal(self, value: object) -> Literal:
        if isinstance(value, int):
            return Literal("i32", str(value))
        if isinstance(value, float):
            return Literal("f64", str(value))
        if isinstance(value, str):
            return Literal("string", value)
        raise ValueError(f"Cannot convert value of type {type(value)} to Literal")

    def _create_default_for_type(self, type_name: str):
        if type_name == "i32":
            return 0
        if type_name == "f64":
            return 0.0
        if type_name == "string":
            return ""
        # user-defined type
        type_def = self._types.get(type_name)
        if type_def is None:
            raise ValueError(f"Unknown type: {type_name}")
        obj: Dict[str, object] = {}
        for fname, ftype in type_def.items():
            obj[fname] = self._create_default_for_type(ftype)
        return obj

    def _define(self, definition: Definition) -> None:
        self._types[definition.name] = {f.name: f.type for f in definition.fields}

    def _declare(self, declaration: Declaration) -> None:
        self._vars[declaration.variable.name] = self._create_default_for_type(declaration.type)

    def _get_variable_value(self, variable: Variable) -> object:
        parts = variable.name.split(".")
        if parts[0] not in self._vars:
            raise ValueError(f"Variable {parts[0]} not declared")
        val = self._vars[parts[0]]
        for p in parts[1:]:
            if isinstance(val, dict):
                val = val[p]
            else:
                val = getattr(val, p)
        return val

    def _set_variable_value(self, variable: Variable, value: object) -> None:
        parts = variable.name.split(".")
        if len(parts) == 1:
            self._vars[parts[0]] = value
            return
        if parts[0] not in self._vars:
            # If variable wasn't declared, create a simple entry.
            self._vars[parts[0]] = {}
        obj = self._vars[parts[0]]
        for p in parts[1:-1]:
            if isinstance(obj, dict):
                obj = obj[p]
            else:
                obj = getattr(obj, p)
        last = parts[-1]
        if isinstance(obj, dict):
            obj[last] = value
        else:
            setattr(obj, last, value)

    def _evaluate_expression(self, expression: Expression) -> object:
        if isinstance(expression, Literal):
            if expression.type == "i32":
                return int(expression.value)
            if expression.type == "f64":
                return float(expression.value)
            if expression.type == "string":
                return expression.value
            raise ValueError(f"Unsupported literal type: {expression.type}")
        elif isinstance(expression, Variable):
            return self._get_variable_value(expression)
        elif isinstance(expression, CompositeExpression):
            left_value = self._evaluate_expression(expression.first_operand)
            right_value = self._evaluate_expression(expression.second_operand)
            if expression.operator == "+":
                return left_value + right_value
            elif expression.operator == "-":
                return left_value - right_value
            elif expression.operator == "*":
                return left_value * right_value
            elif expression.operator == "/":
                return left_value / right_value
            else:
                raise ValueError(f"Unsupported operator: {expression.operator}")
        elif isinstance(expression, FunctionCall):
            return self._call_function(expression)
        else:
            raise ValueError(f"Unsupported expression type: {expression.__class__}")

    def _define_function(self, definition: FunctionDefinition) -> None:
        self._functions[definition.name] = definition

    def _has_comptime_parameters(self, definition: FunctionDefinition) -> bool:
        return any(parameter.comptime for parameter in definition.parameters)

    def _call_function(self, call: FunctionCall) -> object:
        if call.name not in self._functions:
            raise ValueError(f"Unknown function: {call.name}")

        definition = self._functions[call.name]
        if len(call.arguments) != len(definition.parameters):
            raise ValueError(
                f"Function {call.name} expects {len(definition.parameters)} arguments, "
                f"got {len(call.arguments)}"
            )

        argument_values = [self._evaluate_expression(arg) for arg in call.arguments]
        previous_vars = self._vars.copy()

        try:
            for parameter, value in zip(definition.parameters, argument_values):
                self._vars[parameter.name] = value

            for stmt in definition.body:
                if isinstance(stmt, Return):
                    return self._evaluate_expression(stmt.expression)
                self._execute_statement(stmt)
        finally:
            self._vars = previous_vars

        return None

    def _transform_statement(self, stmt: Statement) -> Statement:
        if isinstance(stmt, Assignment):
            return Assignment(
                stmt.comptime,
                stmt.variable,
                self._transform_expression(stmt.value),
            )
        if isinstance(stmt, Print):
            return Print(stmt.comptime, self._transform_expression(stmt.expression))
        if isinstance(stmt, ExpressionStatement):
            return ExpressionStatement(
                stmt.comptime,
                self._transform_expression(stmt.expression),
            )
        if isinstance(stmt, Return):
            return Return(stmt.comptime, self._transform_expression(stmt.expression))
        if isinstance(stmt, FunctionDefinition):
            return FunctionDefinition(
                stmt.comptime,
                stmt.return_type,
                stmt.name,
                stmt.parameters,
                [self._transform_statement(body_stmt) for body_stmt in stmt.body],
            )
        return stmt

    def _transform_expression(
        self,
        expression: Expression,
        substitutions: Dict[str, Literal] | None = None,
    ) -> Expression:
        substitutions = substitutions or {}

        if isinstance(expression, Literal):
            return expression
        if isinstance(expression, Variable):
            return substitutions.get(expression.name, expression)
        if isinstance(expression, CompositeExpression):
            return CompositeExpression(
                expression.operator,
                self._transform_expression(expression.first_operand, substitutions),
                self._transform_expression(expression.second_operand, substitutions),
            )
        if isinstance(expression, FunctionCall):
            transformed_arguments = [
                self._transform_expression(argument, substitutions)
                for argument in expression.arguments
            ]
            if expression.name in self._functions and self._has_comptime_parameters(
                self._functions[expression.name]
            ):
                return self._specialize_call(
                    FunctionCall(expression.name, transformed_arguments)
                )
            return FunctionCall(expression.name, transformed_arguments)
        return expression

    def _specialize_call(self, call: FunctionCall) -> FunctionCall:
        definition = self._functions[call.name]
        if len(call.arguments) != len(definition.parameters):
            raise ValueError(
                f"Function {call.name} expects {len(definition.parameters)} arguments, "
                f"got {len(call.arguments)}"
            )

        comptime_values: list[tuple[str, str]] = []
        substitutions: Dict[str, Literal] = {}
        runtime_arguments: list[Expression] = []

        for parameter, argument in zip(definition.parameters, call.arguments):
            if parameter.comptime:
                value = self._evaluate_expression(argument)
                literal = self._value_to_literal(value)
                comptime_values.append((literal.type, literal.value))
                substitutions[parameter.name] = literal
            else:
                runtime_arguments.append(argument)

        key = (definition.name, tuple(comptime_values))
        if key in self._specializations:
            return FunctionCall(self._specializations[key], runtime_arguments)

        specialized_name = self._specialized_name(definition.name, comptime_values)
        specialized_parameters = [
            parameter for parameter in definition.parameters if not parameter.comptime
        ]
        specialized_body = [
            self._transform_statement_with_substitutions(stmt, substitutions)
            for stmt in definition.body
        ]
        specialized_definition = FunctionDefinition(
            False,
            definition.return_type,
            specialized_name,
            specialized_parameters,
            specialized_body,
        )

        self._specializations[key] = specialized_name
        self._define_function(specialized_definition)
        self._pending_specializations.append(specialized_definition)
        return FunctionCall(specialized_name, runtime_arguments)

    def _transform_statement_with_substitutions(
        self,
        stmt: Statement,
        substitutions: Dict[str, Literal],
    ) -> Statement:
        if isinstance(stmt, Assignment):
            return Assignment(
                stmt.comptime,
                stmt.variable,
                self._transform_expression(stmt.value, substitutions),
            )
        if isinstance(stmt, Print):
            return Print(
                stmt.comptime,
                self._transform_expression(stmt.expression, substitutions),
            )
        if isinstance(stmt, ExpressionStatement):
            return ExpressionStatement(
                stmt.comptime,
                self._transform_expression(stmt.expression, substitutions),
            )
        if isinstance(stmt, Return):
            return Return(
                stmt.comptime,
                self._transform_expression(stmt.expression, substitutions),
            )
        return stmt

    def _specialized_name(self, name: str, values: list[tuple[str, str]]) -> str:
        # Use a forbidden token (`#`) as the separator between the base
        # function name and the specialization suffix to reduce collision
        # risk with normal identifiers.
        suffix = "#".join(
            f"{type_}_{self._sanitize_specialization_value(value)}"
            for type_, value in values
        )
        return f"{name}#{suffix}"

    def _sanitize_specialization_value(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9_]", "_", value)
        if sanitized and sanitized[0].isdigit():
            return sanitized
        return sanitized or "empty"

    def _append_pending_specializations(self, ast: List[Statement]) -> None:
        ast.extend(self._pending_specializations)
        self._pending_specializations = []

    def _execute_statement(self, stmt: Statement) -> None:
        if isinstance(stmt, Definition):
            self._define(stmt)
        elif isinstance(stmt, FunctionDefinition):
            self._define_function(stmt)
        elif isinstance(stmt, Declaration):
            self._declare(stmt)
        elif isinstance(stmt, Assignment):
            value = self._evaluate_expression(stmt.value)
            self._set_variable_value(stmt.variable, value)
        elif isinstance(stmt, Print):
            self._evaluate_expression(stmt.expression)
        elif isinstance(stmt, ExpressionStatement):
            self._evaluate_expression(stmt.expression)

    def run(self, ast: List[Statement]) -> List[Statement]:
        """
        Transform the AST by evaluating `comptime` statements. The transformed
        AST is returned; no runtime `Interpreter` methods are invoked.
        """
        new_ast: List[Statement] = []

        for stmt in ast:
            if not getattr(stmt, "comptime", False):
                # Keep runtime statements, but update the internal registry so
                # later comptime expressions can use declarations/definitions.
                if isinstance(stmt, Definition):
                    self._define(stmt)
                elif isinstance(stmt, FunctionDefinition):
                    self._define_function(stmt)
                    if self._has_comptime_parameters(stmt):
                        continue
                    transformed_stmt = self._transform_statement(stmt)
                    self._append_pending_specializations(new_ast)
                    new_ast.append(transformed_stmt)
                    continue
                elif isinstance(stmt, Declaration):
                    self._declare(stmt)
                elif isinstance(stmt, Assignment):
                    stmt = self._transform_statement(stmt)
                    value = self._evaluate_expression(stmt.value)
                    self._set_variable_value(stmt.variable, value)
                elif isinstance(stmt, ExpressionStatement):
                    stmt = self._transform_statement(stmt)
                    self._evaluate_expression(stmt.expression)
                elif isinstance(stmt, Print):
                    stmt = self._transform_statement(stmt)
                self._append_pending_specializations(new_ast)
                new_ast.append(stmt)
            else:
                # Statement is marked comptime: evaluate/execute and replace.
                if isinstance(stmt, Definition):
                    self._define(stmt)
                    # definition is compile-time only; drop from AST
                elif isinstance(stmt, FunctionDefinition):
                    self._define_function(stmt)
                    # definition is compile-time only; drop from AST
                elif isinstance(stmt, Declaration):
                    self._declare(stmt)
                    # drop from AST
                elif isinstance(stmt, Assignment):
                    value = self._evaluate_expression(stmt.value)
                    literal = self._value_to_literal(value)
                    # assign in comptime environment and append converted assignment
                    self._set_variable_value(stmt.variable, value)
                    new_ast.append(Assignment(False, stmt.variable, literal))
                elif isinstance(stmt, Print):
                    value = self._evaluate_expression(stmt.expression)
                    literal = self._value_to_literal(value)
                    new_ast.append(Print(False, literal))
                elif isinstance(stmt, ExpressionStatement):
                    self._evaluate_expression(stmt.expression)
                else:
                    # Unknown statement at compile time: drop it (no runtime side-effects)
                    pass

        return new_ast
