
from typing import List, Tuple
from dataclasses import dataclass

@dataclass
class Statement:
    comptime: bool

@dataclass
class Expression:
    pass

@dataclass
class CompositeExpression(Expression):
    operator: str
    first_operand: Expression
    second_operand: Expression

@dataclass
class Variable(Expression):
    name: str

@dataclass
class Literal(Expression):
    type: str
    value: str

@dataclass
class Declaration(Statement):
    variable: Variable
    type: str

@dataclass
class Field:
    name: str
    type: str

@dataclass
class Definition(Statement):
    name: str
    fields: List[Field]

@dataclass
class Assignment(Statement):
    variable: Variable
    value: Expression

@dataclass
class Print(Statement):
    expression: Expression