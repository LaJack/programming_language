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
    While,
    For,
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
        # NOTE: comptime checks are always strict; only variables declared
        # as comptime or local comptime parameters may be used during
        # comptime evaluation.

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

    def _get_variable_value(self, variable: Variable, comptime: bool = False) -> object:
        parts = variable.name.split(".")
        if parts[0] not in self._vars:
            raise ValueError(f"Variable {parts[0]} not declared")
        # If evaluating in a comptime context, ensure referenced variables
        # are allowed at comptime (either declared as comptime or are
        # comptime function parameters currently in scope). This is always
        # enforced for comptime evaluations.
        if comptime:
            base = parts[0]
            allowed = base in self._comptime_only_vars or any(base in s for s in self._local_comptime_vars)
            if not allowed:
                raise ValueError(f"Non-comptime variable used in comptime evaluation: {base}")
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

    def _evaluate_expression(self, expression: Expression, comptime: bool = False) -> object:
        if isinstance(expression, Literal):
            if expression.type == "i32":
                return int(expression.value)
            if expression.type == "f64":
                return float(expression.value)
            if expression.type == "string":
                return expression.value
            raise ValueError(f"Unsupported literal type: {expression.type}")
        elif isinstance(expression, Variable):
            return self._get_variable_value(expression, comptime=comptime)
        elif isinstance(expression, CompositeExpression):
            left_value = self._evaluate_expression(expression.first_operand, comptime=comptime)
            right_value = self._evaluate_expression(expression.second_operand, comptime=comptime)
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
            return self._call_function(expression, comptime=comptime)
        else:
            raise ValueError(f"Unsupported expression type: {expression.__class__}")

    def _define_function(self, definition: FunctionDefinition) -> None:
        self._functions[definition.name] = definition

    def _has_comptime_parameters(self, definition: FunctionDefinition) -> bool:
        return any(parameter.comptime for parameter in definition.parameters)

    def _call_function(self, call: FunctionCall, comptime: bool = False) -> object:
        if call.name not in self._functions:
            raise ValueError(f"Unknown function: {call.name}")

        definition = self._functions[call.name]
        if len(call.arguments) != len(definition.parameters):
            raise ValueError(
                f"Function {call.name} expects {len(definition.parameters)} arguments, "
                f"got {len(call.arguments)}"
            )

        argument_values = [self._evaluate_expression(arg, comptime=comptime) for arg in call.arguments]
        previous_vars = self._vars.copy()

        # Push local comptime parameter names for the duration of this call.
        local_params = set(p.name for p in definition.parameters if p.comptime)
        self._local_comptime_vars.append(local_params)

        try:
            for parameter, value in zip(definition.parameters, argument_values):
                self._vars[parameter.name] = value

            for stmt in definition.body:
                if isinstance(stmt, Return):
                    return self._evaluate_expression(stmt.expression, comptime=comptime)
                self._execute_statement(stmt, comptime=comptime)
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
        if isinstance(stmt, While):
            return While(
                stmt.comptime,
                self._transform_expression(stmt.condition, substitutions),
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.body],
            )
        if isinstance(stmt, For):
            return For(
                stmt.comptime,
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.init],
                self._transform_expression(stmt.condition, substitutions) if stmt.condition is not None else None,
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.post],
                [self._transform_statement_with_substitutions(s, substitutions) for s in stmt.body],
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
                # Evaluate comptime arguments in comptime mode.
                value = self._evaluate_expression(argument, comptime=True)
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

    def _ensure_comptime_block(self, stmt: Statement) -> None:
        """Ensure every statement inside a comptime-controlled block is itself
        marked `comptime`. Raises ValueError if a non-comptime statement is
        found. This prevents runtime-visible side effects from occurring during
        comptime loops.
        """
        def check_statements(stmts: list[Statement]) -> None:
            for s in stmts:
                if not getattr(s, "comptime", False):
                    # Provide a succinct error message indicating the offending
                    # statement type and, if applicable, the target variable.
                    if isinstance(s, Assignment):
                        raise ValueError(f"Non-comptime statement inside comptime block: assignment to {s.variable.name}")
                    raise ValueError(f"Non-comptime statement inside comptime block: {s.__class__.__name__}")

                # Recurse into nested control structures
                if isinstance(s, If):
                    check_statements(s.body)
                    for _cond, body in s.elifs:
                        check_statements(body)
                    if s.else_body:
                        check_statements(s.else_body)
                elif isinstance(s, While):
                    check_statements(s.body)
                elif isinstance(s, For):
                    check_statements(s.init)
                    if s.post:
                        check_statements(s.post)
                    check_statements(s.body)

        if isinstance(stmt, While):
            check_statements(stmt.body)
        elif isinstance(stmt, For):
            check_statements(stmt.init)
            if stmt.post:
                check_statements(stmt.post)
            check_statements(stmt.body)

    def _execute_statement(self, stmt: Statement, comptime: bool = True) -> None:
        if isinstance(stmt, Definition):
            self._define(stmt)
        elif isinstance(stmt, FunctionDefinition):
            self._define_function(stmt)
        elif isinstance(stmt, Declaration):
            self._declare(stmt)
            # Declarations executed during comptime are comptime-only variables
            self._comptime_only_vars.add(stmt.variable.name)
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
            value = self._evaluate_expression(stmt.value, comptime=comptime)
            self._set_variable_value(stmt.variable, value)
        elif isinstance(stmt, Print):
            # Execute comptime prints immediately during the pass so the user
            # sees output when compiling.
            value = self._evaluate_expression(stmt.expression, comptime=comptime)
            print(value)
        elif isinstance(stmt, ExpressionStatement):
            self._evaluate_expression(stmt.expression, comptime=comptime)
        elif isinstance(stmt, If):
            cond = self._evaluate_expression(stmt.condition, comptime=comptime)
            taken = False
            if cond:
                for s in stmt.body:
                    self._execute_statement(s, comptime=comptime)
                taken = True
            if not taken:
                for econd, ebody in stmt.elifs:
                    if self._evaluate_expression(econd, comptime=comptime):
                        for s in ebody:
                            self._execute_statement(s, comptime=comptime)
                        taken = True
                        break
            if not taken and stmt.else_body:
                for s in stmt.else_body:
                    self._execute_statement(s, comptime=comptime)
        elif isinstance(stmt, While):
            # Non-strict execution of while (used when executing inside
            # functions at comptime). This executes all body statements and
            # relies on assignment checks above to validate comptime writes.
            cond = self._evaluate_expression(stmt.condition, comptime=comptime)
            while cond:
                for s in stmt.body:
                    self._execute_statement(s, comptime=comptime)
                cond = self._evaluate_expression(stmt.condition, comptime=comptime)
        elif isinstance(stmt, For):
            # Execute init
            for s in stmt.init:
                self._execute_statement(s, comptime=comptime)

            # condition
            if stmt.condition is None:
                cond = 1
            else:
                cond = self._evaluate_expression(stmt.condition, comptime=comptime)

            while cond:
                for s in stmt.body:
                    self._execute_statement(s, comptime=comptime)
                for s in stmt.post:
                    self._execute_statement(s, comptime=comptime)
                if stmt.condition is None:
                    cond = 1
                else:
                    cond = self._evaluate_expression(stmt.condition, comptime=comptime)
        elif isinstance(stmt, While):
            cond = self._evaluate_expression(stmt.condition, comptime=comptime)
            while cond:
                for s in stmt.body:
                    self._execute_statement(s, comptime=comptime)
                cond = self._evaluate_expression(stmt.condition, comptime=comptime)
        elif isinstance(stmt, For):
            # init
            for s in stmt.init:
                self._execute_statement(s, comptime=comptime)

            # condition
            if stmt.condition is None:
                cond = 1
            else:
                cond = self._evaluate_expression(stmt.condition, comptime=comptime)

            while cond:
                for s in stmt.body:
                    self._execute_statement(s, comptime=comptime)
                for s in stmt.post:
                    self._execute_statement(s, comptime=comptime)
                if stmt.condition is None:
                    cond = 1
                else:
                    cond = self._evaluate_expression(stmt.condition, comptime=comptime)

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
                elif isinstance(stmt, While):
                    stmt = self._transform_statement_with_substitutions(stmt, substitutions)
                elif isinstance(stmt, For):
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
                        cond_val = self._evaluate_expression(stmt.condition, comptime=True)
                        selected: List[Statement] | None = None
                        if cond_val:
                            selected = stmt.body
                        else:
                            for econd, ebody in stmt.elifs:
                                if self._evaluate_expression(econd, comptime=True):
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
                                    value = self._evaluate_expression(inner.value, comptime=True)
                                    self._set_variable_value(inner.variable, value)
                                else:
                                    transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                    value = self._evaluate_expression(transformed.value, comptime=True)
                                    self._set_variable_value(transformed.variable, value)
                                    self._append_pending_specializations(new_ast)
                                    new_ast.append(transformed)
                            elif isinstance(inner, Print):
                                if inner.comptime:
                                    value = self._evaluate_expression(inner.expression, comptime=True)
                                    print(value)
                                else:
                                    transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                    self._append_pending_specializations(new_ast)
                                    new_ast.append(transformed)
                            elif isinstance(inner, ExpressionStatement):
                                if inner.comptime:
                                    self._evaluate_expression(inner.expression, comptime=True)
                                else:
                                    transformed = self._transform_statement_with_substitutions(inner, substitutions)
                                    self._evaluate_expression(transformed.expression, comptime=True)
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
                        value = self._evaluate_expression(stmt.value, comptime=True)
                        self._set_variable_value(stmt.variable, value)
                    elif isinstance(stmt, Print):
                        value = self._evaluate_expression(stmt.expression, comptime=True)
                        print(value)
                    elif isinstance(stmt, ExpressionStatement):
                        self._evaluate_expression(stmt.expression, comptime=True)
                    elif isinstance(stmt, While):
                        # Top-level comptime loops must not contain non-comptime
                        # statements that would produce runtime-visible side
                        # effects.
                        self._ensure_comptime_block(stmt)
                        self._execute_statement(stmt)
                    elif isinstance(stmt, For):
                        self._ensure_comptime_block(stmt)
                        self._execute_statement(stmt)
                    else:
                        # drop unknown comptime statements
                        pass

        return new_ast
