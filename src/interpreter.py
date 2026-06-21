from typing import Any, Callable, Dict, cast
try:
    from src.ast_nodes import *  # type: ignore
except Exception:
    from ast_nodes import *  # type: ignore


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
            else:
                raise ValueError(f"Unsupported operator: {expression.operator}")
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
    Declaration(False, Variable("x"), "i32"),
    Declaration(False, Variable("y"), "i32"),
    Assignment(False, Variable("x"), Literal("i32", "42")),
    Assignment(False, Variable("y"), Variable("x")),
    Assignment(False, Variable("y"), Literal("i32", "84")),
    Print(False,Variable("x")),
    Print(False,Variable("y")),
    Print(False, CompositeExpression("+", Variable("x"), Variable("y"))),

    # Point
    Definition(False, "Point", [Field("x", "i32"), Field("y", "i32")]),
    Declaration(False, Variable("point"), "Point"),

    Assignment(False, Variable("point.x"), Literal("i32", "5")),
    Assignment(False, Variable("point.y"), Literal("i32", "10")),

    Print(False, Variable("point.x")),
    Print(False, Variable("point.y")),

    # Line
    Definition(False, "Line", [Field("start", "Point"), Field("end", "Point")]),
    Declaration(False, Variable("line"), "Line"),

    Assignment(False, Variable("line.start.x"), Literal("i32", "50")),
    Assignment(False, Variable("line.start.y"), Literal("i32", "100")),
    Assignment(False, Variable("line.end"), Variable("point")),

    Print(False, Variable("line")),
    Print(False, Variable("line.start")),
    Print(False, Variable("line.start.x")),
    Print(False, Variable("line.start.y")),
    Print(False, Variable("line.end")),
    Print(False, Variable("line.end.x")),
    Print(False, Variable("line.end.y")),
])