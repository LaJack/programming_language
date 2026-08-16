from dataclasses import dataclass
from typing import Generic, Iterable, TypeVar

try:
    from .ast_nodes import (
        Assignment,
        BorrowExpression,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        StructLiteralExpression,
        FunctionCall,
        FunctionDeclaration,
        If,
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
        Statement,
        Try,
        TypeDeclaration,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
except ImportError:
    from ast_nodes import (
        Assignment,
        BorrowExpression,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        StructLiteralExpression,
        FunctionCall,
        FunctionDeclaration,
        If,
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
        Statement,
        Try,
        TypeDeclaration,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )


Value = TypeVar('Value')
Scope = TypeVar('Scope')


@dataclass
class ReturnSignal(Generic[Value]):
    value: Value


class ExecutionEngine(Generic[Value, Scope]):
    LOOP_ITERATION_LIMIT: int | None = None

    def _execute_statements(
        self, statements: Iterable[Statement], scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        for statement in statements:
            returned = self._execute_statement(statement, scope, allow_return)
            if returned is not None:
                return returned

        return None

    def _execute_statement(
        self, statement: Statement, scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        if statement.comptime and not self._allows_comptime_statement(statement, scope):
            self._unexpected_comptime_statement(statement)

        if type(statement) in {ModuleDeclaration, ImportDeclaration, ViewDeclaration}:
            return None
        if type(statement) is VariableDeclaration:
            self._execute_variable_declaration(statement, scope)
        elif type(statement) is Assignment:
            self._execute_assignment(statement, scope)
        elif type(statement) is Print:
            self._execute_print(statement, scope)
        elif type(statement) is TypeDeclaration:
            self._execute_type_declaration(statement, scope)
        elif type(statement) is FunctionDeclaration:
            self._execute_function_declaration(statement, scope)
        elif type(statement) is FunctionCall:
            self._eval_function_call(statement, scope)
        elif type(statement) is Raise:
            self._execute_raise(statement, scope)
        elif type(statement) is Rethrow:
            self._execute_rethrow(statement, scope)
        elif type(statement) is Return:
            if not allow_return:
                self._return_outside_function()
            return ReturnSignal(self._eval_return(statement, scope))
        elif type(statement) is If:
            return self._execute_if(statement, scope, allow_return)
        elif type(statement) is While:
            return self._execute_while(statement, scope, allow_return)
        elif type(statement) is For:
            return self._execute_for(statement, scope, allow_return)
        elif type(statement) is Try:
            return self._execute_try(statement, scope, allow_return)
        else:
            self._unknown_statement(statement)

        return None

    def _execute_if(
        self, statement: If, scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        for branch in statement.branches:
            if self._is_truthy(self._eval_expression(branch.condition, scope)):
                return self._execute_block(branch.body, scope, allow_return)
        if statement.else_body is not None:
            return self._execute_block(statement.else_body, scope, allow_return)
        return None

    def _execute_while(
        self, statement: While, scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        iterations = 0
        while self._is_truthy(self._eval_expression(statement.condition, scope)):
            self._check_loop_limit(iterations, 'while')
            returned = self._execute_block(statement.body, scope, allow_return)
            if returned is not None:
                return returned
            iterations += 1
        return None

    def _execute_for(
        self, statement: For, scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        loop_scope = self._child_scope(scope)
        if statement.initializer is not None:
            self._execute_statement(statement.initializer, loop_scope, allow_return=False)

        iterations = 0
        while statement.condition is None or self._is_truthy(
            self._eval_expression(statement.condition, loop_scope)
        ):
            self._check_loop_limit(iterations, 'for')
            returned = self._execute_block(statement.body, loop_scope, allow_return)
            if returned is not None:
                return returned
            if statement.update is not None:
                self._execute_statement(statement.update, loop_scope, allow_return=False)
            iterations += 1
        return None

    def _execute_block(
        self, statements: Iterable[Statement], scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        return self._execute_statements(statements, self._child_scope(scope), allow_return)

    def _execute_try(
        self, statement: Try, scope: Scope, allow_return: bool
    ) -> ReturnSignal[Value] | None:
        raise NotImplementedError

    def _eval_expression(self, expression: Expression, scope: Scope) -> Value:
        if type(expression) is LiteralExpression:
            return self._eval_literal(expression, scope)
        if type(expression) is CompositeExpression:
            left = self._eval_expression(expression.left, scope)
            right = self._eval_expression(expression.right, scope)
            return self._eval_composite_operator(expression.operator, left, right)
        if type(expression) is VariableExpression:
            return self._eval_variable(expression, scope)
        if type(expression) is FunctionCall:
            return self._eval_function_call(expression, scope)
        if type(expression) is FormattedStringExpression:
            return self._eval_formatted_string(expression, scope)
        if type(expression) is BorrowExpression:
            return self._eval_borrow(expression, scope)
        if type(expression) is MoveExpression:
            return self._eval_expression(expression.expr, scope)
        if type(expression) is IndexExpression:
            return self._eval_index(expression, scope)
        if type(expression) is SliceExpression:
            return self._eval_slice(expression, scope)
        if type(expression) is StructLiteralExpression:
            return self._eval_struct_literal(expression, scope)
        self._unknown_expression(expression)

    def _eval_return(self, statement: Return, scope: Scope) -> Value:
        if statement.expr is None:
            return self._void_return_value()
        return self._eval_expression(statement.expr, scope)

    def _check_loop_limit(self, iterations: int, loop_name: str) -> None:
        if self.LOOP_ITERATION_LIMIT is not None and iterations >= self.LOOP_ITERATION_LIMIT:
            self._loop_iteration_limit_exceeded(loop_name)

    def _allows_comptime_statement(self, statement: Statement, scope: Scope) -> bool:
        return False

    def _child_scope(self, scope: Scope) -> Scope:
        raise NotImplementedError

    def _execute_variable_declaration(
        self, declaration: VariableDeclaration, scope: Scope
    ) -> None:
        raise NotImplementedError

    def _execute_assignment(self, assignment: Assignment, scope: Scope) -> None:
        raise NotImplementedError

    def _execute_print(self, statement: Print, scope: Scope) -> None:
        raise NotImplementedError

    def _execute_type_declaration(self, declaration: TypeDeclaration, scope: Scope) -> None:
        raise NotImplementedError

    def _execute_function_declaration(
        self, declaration: FunctionDeclaration, scope: Scope
    ) -> None:
        raise NotImplementedError

    def _execute_raise(self, statement: Raise, scope: Scope) -> None:
        raise NotImplementedError

    def _execute_rethrow(self, statement: Rethrow, scope: Scope) -> None:
        raise NotImplementedError

    def _eval_literal(self, literal: LiteralExpression, scope: Scope) -> Value:
        raise NotImplementedError

    def _eval_variable(self, variable: VariableExpression, scope: Scope) -> Value:
        raise NotImplementedError

    def _eval_function_call(self, function_call: FunctionCall, scope: Scope) -> Value:
        raise NotImplementedError

    def _eval_formatted_string(
        self, expression: FormattedStringExpression, scope: Scope
    ) -> Value:
        raise NotImplementedError

    def _eval_borrow(self, expression: BorrowExpression, scope: Scope) -> Value:
        raise NotImplementedError

    def _eval_index(self, expression: IndexExpression, scope: Scope) -> Value:
        raise NotImplementedError

    def _eval_slice(self, expression: SliceExpression, scope: Scope) -> Value:
        raise NotImplementedError

    def _eval_struct_literal(
        self, expression: StructLiteralExpression, scope: Scope
    ) -> Value:
        raise NotImplementedError

    def _eval_composite_operator(self, operator: str, left: Value, right: Value) -> Value:
        raise NotImplementedError

    def _is_truthy(self, value: Value) -> bool:
        raise NotImplementedError

    def _void_return_value(self) -> Value:
        raise NotImplementedError

    def _return_outside_function(self) -> None:
        raise NotImplementedError

    def _unexpected_comptime_statement(self, statement: Statement) -> None:
        raise NotImplementedError

    def _unknown_statement(self, statement: Statement) -> None:
        raise NotImplementedError

    def _unknown_expression(self, expression: Expression) -> None:
        raise NotImplementedError

    def _unknown_operator(self, operator: str) -> None:
        raise NotImplementedError

    def _loop_iteration_limit_exceeded(self, loop_name: str) -> None:
        raise NotImplementedError
