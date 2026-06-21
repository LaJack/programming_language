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
                elif isinstance(stmt, Declaration):
                    self._declare(stmt)
                elif isinstance(stmt, Assignment):
                    value = self._evaluate_expression(stmt.value)
                    self._set_variable_value(stmt.variable, value)
                elif isinstance(stmt, ExpressionStatement):
                    self._evaluate_expression(stmt.expression)
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
