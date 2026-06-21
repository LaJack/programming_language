from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, cast

from .ast_nodes import (
    Assignment,
    CompositeExpression,
    Declaration,
    Definition,
    ExpressionStatement,
    Expression,
    Field,
    FunctionCall,
    FunctionDefinition,
    Literal,
    Print,
    Return,
    If,
    Statement,
    Variable,
)
from .parser import parse_sources


class SymbolTable:
    pass

class Interpreter:
    """Interpreter for a custom programming language.

    This class evaluates an AST (Abstract Syntax Tree) by executing statements
    such as definitions, declarations, assignments, and print statements.
    It maintains a symbol table for variables and types.
    """

    def __init__(self):
        """Initialize the interpreter with a symbol table and built-in types."""
        self._symbols = SymbolTable()

        self._interpreter_functions: Dict[type, Callable[[Any], None]] = {
            Definition: self.define,
            Declaration: self.declare,
            Assignment: self.assign,
            Print: self.print,
            ExpressionStatement: self.evaluate_expression_statement,
            If: self.if_statement,
            FunctionDefinition: self.define_function,
        }
        self._functions: Dict[str, FunctionDefinition] = {}

        self._define_builtin_types()

    def evaluate(self, ast: List[Statement]):
        """Evaluate a list of statements from the AST."""
        for statement in ast:
            callback = self._interpreter_functions[statement.__class__]
            callback(statement)

    def define(self, definition: Definition) -> None:
        """Define a new type based on the Definition statement."""
        setattr(self._symbols, definition.name, type(definition.name, (), {
            field.name: getattr(self._symbols, field.type)() for field in definition.fields
        }))

    def declare(self, declaration: Declaration) -> None:
        """Declare a variable of a given type."""
        setattr(self._symbols, declaration.variable.name, getattr(self._symbols, declaration.type)())

    def assign(self, assignment: Assignment) -> None:
        """Assign a value to a variable."""
        value = self.evaluate_expression(assignment.value)
        self.set_variable_value(assignment.variable, value)

    def evaluate_expression(self, expression: Expression) -> object:
        """Evaluate an expression and return its value."""
        if isinstance(expression, Literal):
            return (cast(Callable[[str], object], getattr(self._symbols, expression.type)))(expression.value)
        elif isinstance(expression, Variable):
            return self.get_variable_value(expression)
        elif isinstance(expression, CompositeExpression):
            left_value = self.evaluate_expression(expression.first_operand)
            right_value = self.evaluate_expression(expression.second_operand)
            if expression.operator == "+":
                return left_value + right_value
            elif expression.operator == "-":
                return left_value - right_value
            elif expression.operator == "*":
                return left_value * right_value
            elif expression.operator == "/":
                return left_value / right_value
            elif expression.operator == "==":
                return 1 if left_value == right_value else 0
            elif expression.operator == "!=":
                return 1 if left_value != right_value else 0
            elif expression.operator == "<":
                return 1 if left_value < right_value else 0
            elif expression.operator == "<=":
                return 1 if left_value <= right_value else 0
            elif expression.operator == ">":
                return 1 if left_value > right_value else 0
            elif expression.operator == ">=":
                return 1 if left_value >= right_value else 0
            elif expression.operator == "&&":
                return 1 if (bool(left_value) and bool(right_value)) else 0
            elif expression.operator == "||":
                return 1 if (bool(left_value) or bool(right_value)) else 0
            else:
                raise ValueError(f"Unsupported operator: {expression.operator}")
        elif isinstance(expression, FunctionCall):
            return self.call_function(expression)
        else:
            raise ValueError(f"Unsupported expression type: {expression.__class__}")

    def print(self, print_statement: Print):
        """Print the value of an expression."""
        value = self.evaluate_expression(print_statement.expression)
        print(value)

    def evaluate_expression_statement(self, statement: ExpressionStatement) -> None:
        """Evaluate an expression for its side effects."""
        self.evaluate_expression(statement.expression)

    def get_variable_value(self, variable: Variable) -> object:
        """Get the value of a variable, supporting dotted access."""
        fields = variable.name.split(".")
        obj = self._symbols
        for field in fields:
            obj = getattr(obj, field)
        return obj
    
    def set_variable_value(self, variable: Variable, value: object) -> None:
        """Set the value of a variable, supporting dotted access."""
        fields = variable.name.split(".")
        obj = self._symbols
        for field in fields[:-1]:
            obj = getattr(obj, field)
        setattr(obj, fields[-1], value)

    def define_function(self, definition: FunctionDefinition) -> None:
        """Register a function definition."""
        self._functions[definition.name] = definition

    def if_statement(self, if_stmt: If) -> None:
        """Evaluate an if/elif/else statement at runtime."""
        cond = self.evaluate_expression(if_stmt.condition)
        taken = False
        if cond:
            for st in if_stmt.body:
                callback = self._interpreter_functions[st.__class__]
                callback(st)
            taken = True
        if not taken:
            for econd, ebody in if_stmt.elifs:
                if self.evaluate_expression(econd):
                    for st in ebody:
                        callback = self._interpreter_functions[st.__class__]
                        callback(st)
                    taken = True
                    break
        if not taken and if_stmt.else_body:
            for st in if_stmt.else_body:
                callback = self._interpreter_functions[st.__class__]
                callback(st)

    def call_function(self, call: FunctionCall) -> object:
        """Call a previously registered function."""
        if call.name not in self._functions:
            raise ValueError(f"Unknown function: {call.name}")

        definition = self._functions[call.name]
        if len(call.arguments) != len(definition.parameters):
            raise ValueError(
                f"Function {call.name} expects {len(definition.parameters)} arguments, "
                f"got {len(call.arguments)}"
            )

        argument_values = [self.evaluate_expression(arg) for arg in call.arguments]
        previous_symbols = self._symbols
        self._symbols = self._create_child_symbols(previous_symbols)

        try:
            for parameter, value in zip(definition.parameters, argument_values):
                setattr(self._symbols, parameter.name, value)

            for statement in definition.body:
                if isinstance(statement, Return):
                    return self.evaluate_expression(statement.expression)
                callback = self._interpreter_functions[statement.__class__]
                callback(statement)
        finally:
            self._symbols = previous_symbols

        return None

    def _define_builtin_types(self) -> None:
        setattr(self._symbols, "i32", int)
        setattr(self._symbols, "f64", float)
        setattr(self._symbols, "string", str)

    def _create_child_symbols(self, parent: SymbolTable) -> SymbolTable:
        symbols = SymbolTable()
        for name, value in vars(parent).items():
            setattr(symbols, name, value)
        return symbols


def interpret(ast: List[Statement]) -> None:
    """Interpret an AST."""
    Interpreter().evaluate(ast)


def interpret_sources(sources: Sequence[Path]) -> None:
    """Parse and interpret source files."""
    interpret(parse_sources(sources))
