
from typing import List, Tuple


class Statement:
    pass

class Expression:
    pass

class CompositeExpression(Expression):
    def __init__(self, operator: str, first_operand: Expression, second_operand: Expression):
        self.operator = operator
        self.first_operand = first_operand
        self.second_operand = second_operand

class Variable(Expression):
    def __init__(self, name: str):
        self.name = name

class Literal(Expression):
    def __init__(self, type:str, value: str):
        self.type = type
        self.value = value

class Declaration(Statement):
    def __init__(self, variable: Variable, type: str):
        self.variable = variable
        self.type = type

class Field:
    def __init__(self, name: str, type: str):
        self.name = name
        self.type = type

class Definition(Statement):
    def __init__(self, name: str, fields: List[Field]):
        self.name = name
        self.fields = fields

class Assignment(Statement):
    def __init__(self, variable: Variable, value: Expression):
        self.variable = variable
        self.value = value

class Print(Statement):
    def __init__(self, expression: Expression):
        self.expression = expression