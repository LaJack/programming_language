from typing import Any, Callable, Dict, cast

from ast.ast import *


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
        }

        # Define built-in types
        setattr(self._symbols, "i32", int)

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
        if expression.__class__ == Literal:
            return (cast(Callable[[str], object], getattr(self._symbols, cast(Literal, expression).type)))(cast(Literal, expression).value)
        elif expression.__class__ == Variable:
            return self.get_variable_value(cast(Variable, expression))
        elif expression.__class__ == CompositeExpression:
            left_value = self.evaluate_expression(cast(CompositeExpression, expression).first_operand)
            right_value = self.evaluate_expression(cast(CompositeExpression, expression).second_operand)
            if cast(CompositeExpression, expression).operator == "+":
                return left_value + right_value
            elif cast(CompositeExpression, expression).operator == "-":
                return left_value - right_value
            elif cast(CompositeExpression, expression).operator == "*":
                return left_value * right_value
            elif cast(CompositeExpression, expression).operator == "/":
                return left_value / right_value
            else:
                raise ValueError(f"Unsupported operator: {cast(CompositeExpression, expression).operator}")
        else:
            raise ValueError(f"Unsupported expression type: {expression.__class__}")

    def print(self, print_statement: Print):
        """Print the value of an expression."""
        value = self.evaluate_expression(print_statement.expression)
        print(value)

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

# Test the interpreter with sample AST statements
interpreter = Interpreter()
interpreter.evaluate([
    # Int
    Declaration(Variable("x"), "i32"),
    Declaration(Variable("y"), "i32"),
    Assignment(Variable("x"), Literal("i32", "42")),
    Assignment(Variable("y"), Variable("x")),
    Assignment(Variable("y"), Literal("i32", "84")),
    Print(Variable("x")),
    Print(Variable("y")),
    Print(CompositeExpression("+", Variable("x"), Variable("y"))),

    # Point
    Definition("Point", [Field("x", "i32"), Field("y", "i32")]),
    Declaration(Variable("point"), "Point"),

    Assignment(Variable("point.x"), Literal("i32", "5")),
    Assignment(Variable("point.y"), Literal("i32", "10")),

    Print(Variable("point.x")),
    Print(Variable("point.y")),

    # Line
    Definition("Line", [Field("start", "Point"), Field("end", "Point")]),
    Declaration(Variable("line"), "Line"),

    Assignment(Variable("line.start.x"), Literal("i32", "50")),
    Assignment(Variable("line.start.y"), Literal("i32", "100")),
    Assignment(Variable("line.end"), Variable("point")),

    Print(Variable("line")),
    Print(Variable("line.start")),
    Print(Variable("line.start.x")),
    Print(Variable("line.start.y")),
    Print(Variable("line.end")),
    Print(Variable("line.end.x")),
    Print(Variable("line.end.y")),
])