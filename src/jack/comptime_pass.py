import re
from typing import Dict, List, Set

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
    If,
    Statement,
    Variable,
)


class ComptimePass:
    """
    A compile-time pass that evaluates `comptime` statements and returns a
    transformed AST. This pass executes all comptime statements (including
    function calls marked comptime) and drops them from the emitted AST.

    It also collects values of variables that were declared only for comptime
    use and substitutes those variable occurrences with `Literal`s in the
    remaining runtime AST.
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
        # Variables that were declared/evaluated only at compile-time and
        # must not be emitted into the runtime AST.
        self._comptime_only_vars: Set[str] = set()
        # Stack of local comptime variable sets for currently executing
        # comptime function calls. Each entry is a set of parameter names
        # that are allowed to be assigned during that call.
        self._local_comptime_vars: List[Set[str]] = []

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
            op = expression.operator
            if op == "+":
                return left_value + right_value
            if op == "-":
                return left_value - right_value
            if op == "*":
                return left_value * right_value
            if op == "/":
                return left_value / right_value
            if op == "==":
                return 1 if left_value == right_value else 0
            if op == "!=":
                return 1 if left_value != right_value else 0
            if op == "<":
                return 1 if left_value < right_value else 0
            if op == "<=":
                return 1 if left_value <= right_value else 0
            if op == ">":
                return 1 if left_value > right_value else 0
            if op == ">=":
                return 1 if left_value >= right_value else 0
            if op == "&&":
                return 1 if (bool(left_value) and bool(right_value)) else 0
            if op == "||":
                return 1 if (bool(left_value) or bool(right_value)) else 0
            raise ValueError(f"Unsupported operator: {op}")
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

        # Push local comptime parameter names for the duration of this call.
        local_params = set(p.name for p in definition.parameters if p.comptime)
        self._local_comptime_vars.append(local_params)

        try:
            for parameter, value in zip(definition.parameters, argument_values):
                self._vars[parameter.name] = value

            for stmt in definition.body:
                if isinstance(stmt, Return):
                    return self._evaluate_expression(stmt.expression)
                self._execute_statement(stmt)
        finally:
            self._vars = previous_vars
            self._local_comptime_vars.pop()

        return None

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
                return self._specialize_call(FunctionCall(expression.name, transformed_arguments))
            return FunctionCall(expression.name, transformed_arguments)
        return expression

    def _transform_statement_with_substitutions(
        self, stmt: Statement, substitutions: Dict[str, Literal]
    ) -> Statement:
        if isinstance(stmt, Assignment):
            return Assignment(
                stmt.comptime,
                stmt.variable,
                self._transform_expression(stmt.value, substitutions),
            )
        if isinstance(stmt, Print):
            return Print(stmt.comptime, self._transform_expression(stmt.expression, substitutions))
        if isinstance(stmt, ExpressionStatement):
            return ExpressionStatement(
                stmt.comptime, self._transform_expression(stmt.expression, substitutions)
            )
        if isinstance(stmt, Return):
            return Return(stmt.comptime, self._transform_expression(stmt.expression, substitutions))
        if isinstance(stmt, FunctionDefinition):
            return FunctionDefinition(
                stmt.comptime,
                stmt.return_type,
                stmt.name,
                stmt.parameters,
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.body],
            )
        if isinstance(stmt, If):
            return If(
                stmt.comptime,
                self._transform_expression(stmt.condition, substitutions),
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.body],
                [
                    (
                        self._transform_expression(cond, substitutions),
                        [self._transform_statement_with_substitutions(s, substitutions) for s in body],
                    )
                    for cond, body in stmt.elifs
                ],
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.else_body]
                if stmt.else_body
                else None,
            )
        return stmt

    def _specialized_name(self, name: str, values: list[tuple[str, str]]) -> str:
        suffix = "#".join(f"{type_}_{self._sanitize_specialization_value(value)}" for type_, value in values)
        return f"{name}#{suffix}"

    def _sanitize_specialization_value(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9_]", "_", value)
        if sanitized and sanitized[0].isdigit():
            return sanitized
        return sanitized or "empty"

    def _append_pending_specializations(self, ast: List[Statement]) -> None:
        ast.extend(self._pending_specializations)
        self._pending_specializations = []

    def _specialize_call(self, call: FunctionCall) -> FunctionCall:
        """Create or reuse a specialized version of a function when some
        parameters are comptime. Returns a FunctionCall to the specialized
        function with runtime arguments only.
        """
        if call.name not in self._functions:
            raise ValueError(f"Unknown function: {call.name}")

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
        specialized_parameters = [p for p in definition.parameters if not p.comptime]
        specialized_body = [self._transform_statement_with_substitutions(s, substitutions) for s in definition.body]
        specialized_definition = FunctionDefinition(False, definition.return_type, specialized_name, specialized_parameters, specialized_body)

        self._specializations[key] = specialized_name
        self._define_function(specialized_definition)
        self._pending_specializations.append(specialized_definition)
        return FunctionCall(specialized_name, runtime_arguments)

    def _current_comptime_substitutions(self) -> Dict[str, Literal]:
        subs: Dict[str, Literal] = {}
        for name in self._comptime_only_vars:
            if name in self._vars:
                value = self._vars[name]
                try:
                    subs[name] = self._value_to_literal(value)
                except ValueError:
                    continue
        return subs

    def _execute_statement(self, stmt: Statement) -> None:
        if isinstance(stmt, Definition):
            self._define(stmt)
        elif isinstance(stmt, FunctionDefinition):
            self._define_function(stmt)
        elif isinstance(stmt, Declaration):
            self._declare(stmt)
        elif isinstance(stmt, Assignment):
            # If this assignment is marked comptime, ensure the target is a
            # comptime-declared variable (either a top-level comptime
            # declaration or a comptime function parameter currently in
            # scope). Otherwise this is an error.
            if getattr(stmt, "comptime", False):
                base = stmt.variable.name.split(".")[0]
                allowed = base in self._comptime_only_vars or any(base in s for s in self._local_comptime_vars)
                if not allowed:
                    raise ValueError(f"Cannot assign to non-comptime variable: {stmt.variable.name}")
            value = self._evaluate_expression(stmt.value)
            self._set_variable_value(stmt.variable, value)
        elif isinstance(stmt, Print):
            # Execute comptime prints immediately during the pass so the user
            # sees output when compiling.
            value = self._evaluate_expression(stmt.expression)
            print(value)
        elif isinstance(stmt, ExpressionStatement):
            self._evaluate_expression(stmt.expression)
        elif isinstance(stmt, If):
            cond = self._evaluate_expression(stmt.condition)
            taken = False
            if cond:
                for s in stmt.body:
                    self._execute_statement(s)
                taken = True
            if not taken:
                for econd, ebody in stmt.elifs:
                    if self._evaluate_expression(econd):
                        for s in ebody:
                            self._execute_statement(s)
                        taken = True
                        break
            if not taken and stmt.else_body:
                for s in stmt.else_body:
                    self._execute_statement(s)

    def run(self, ast: List[Statement]) -> List[Statement]:
        new_ast: List[Statement] = []

        for stmt in ast:
            if not getattr(stmt, "comptime", False):
                substitutions = self._current_comptime_substitutions()

                if isinstance(stmt, Definition):
                    self._define(stmt)
                elif isinstance(stmt, FunctionDefinition):
                    self._define_function(stmt)
                    if self._has_comptime_parameters(stmt):
                        continue
                    transformed_stmt = self._transform_statement_with_substitutions(stmt, substitutions)
                    self._append_pending_specializations(new_ast)
                    new_ast.append(transformed_stmt)
                    continue
                elif isinstance(stmt, Declaration):
                    self._declare(stmt)
                elif isinstance(stmt, Assignment):
                    stmt = self._transform_statement_with_substitutions(stmt, substitutions)
                    value = self._evaluate_expression(stmt.value)
                    self._set_variable_value(stmt.variable, value)
                elif isinstance(stmt, ExpressionStatement):
                    stmt = self._transform_statement_with_substitutions(stmt, substitutions)
                    self._evaluate_expression(stmt.expression)
                elif isinstance(stmt, Print):
                    stmt = self._transform_statement_with_substitutions(stmt, substitutions)
                elif isinstance(stmt, If):
                    stmt = self._transform_statement_with_substitutions(stmt, substitutions)

                self._append_pending_specializations(new_ast)
                new_ast.append(stmt)
            else:
                # comptime: execute and do not emit runtime statements
                if isinstance(stmt, Definition):
                    self._define(stmt)
                elif isinstance(stmt, FunctionDefinition):
                    self._define_function(stmt)
                elif isinstance(stmt, Declaration):
                    self._declare(stmt)
                    self._comptime_only_vars.add(stmt.variable.name)
                elif isinstance(stmt, If):
                    cond_val = self._evaluate_expression(stmt.condition)
                    selected: List[Statement] | None = None
                    if cond_val:
                        selected = stmt.body
                    else:
                        for econd, ebody in stmt.elifs:
                            if self._evaluate_expression(econd):
                                selected = ebody
                                break
                    if selected is None:
                        selected = stmt.else_body or []

                    substitutions = self._current_comptime_substitutions()

                    for inner in selected:
                        if isinstance(inner, Definition):
                            self._define(inner)
                        elif isinstance(inner, FunctionDefinition):
                            self._define_function(inner)
                        elif isinstance(inner, Declaration):
                            self._declare(inner)
                            self._comptime_only_vars.add(inner.variable.name)
                        elif isinstance(inner, Assignment):
                            if inner.comptime:
                                base = inner.variable.name.split(".")[0]
                                allowed = base in self._comptime_only_vars or any(base in s for s in self._local_comptime_vars)
                                if not allowed:
                                    raise ValueError(f"Cannot assign to non-comptime variable: {inner.variable.name}")
                                value = self._evaluate_expression(inner.value)
                                self._set_variable_value(inner.variable, value)
                            else:
                                transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                value = self._evaluate_expression(transformed.value)
                                self._set_variable_value(transformed.variable, value)
                                self._append_pending_specializations(new_ast)
                                new_ast.append(transformed)
                        elif isinstance(inner, Print):
                            if inner.comptime:
                                value = self._evaluate_expression(inner.expression)
                                print(value)
                            else:
                                transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                self._append_pending_specializations(new_ast)
                                new_ast.append(transformed)
                        elif isinstance(inner, ExpressionStatement):
                            if inner.comptime:
                                self._evaluate_expression(inner.expression)
                            else:
                                transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                self._evaluate_expression(transformed.expression)
                                self._append_pending_specializations(new_ast)
                                new_ast.append(transformed)
                        else:
                            if getattr(inner, "comptime", False):
                                self._execute_statement(inner)
                            else:
                                transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                self._append_pending_specializations(new_ast)
                                new_ast.append(transformed)
                elif isinstance(stmt, Assignment):
                    if getattr(stmt, "comptime", False):
                        base = stmt.variable.name.split(".")[0]
                        allowed = base in self._comptime_only_vars or any(base in s for s in self._local_comptime_vars)
                        if not allowed:
                            raise ValueError(f"Cannot assign to non-comptime variable: {stmt.variable.name}")
                    value = self._evaluate_expression(stmt.value)
                    self._set_variable_value(stmt.variable, value)
                elif isinstance(stmt, Print):
                    value = self._evaluate_expression(stmt.expression)
                    print(value)
                elif isinstance(stmt, ExpressionStatement):
                    self._evaluate_expression(stmt.expression)
                else:
                    # drop unknown comptime statements
                    pass

        return new_ast
