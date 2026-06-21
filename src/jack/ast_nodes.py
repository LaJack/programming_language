
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
class FunctionCall(Expression):
    name: str
    arguments: List[Expression]

@dataclass
class Declaration(Statement):
    variable: Variable
    type: str

@dataclass
class Field:
    name: str
    type: str

@dataclass
class FunctionParameter:
    comptime: bool
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

@dataclass
class ExpressionStatement(Statement):
    expression: Expression

@dataclass
class Return(Statement):
    expression: Expression

@dataclass
class FunctionDefinition(Statement):
    return_type: str
    name: str
    parameters: List[FunctionParameter]
    body: List[Statement]


@dataclass
class If(Statement):
    condition: Expression
    body: List[Statement]
    elifs: List[Tuple[Expression, List[Statement]]]
    else_body: List[Statement] | None = None


@dataclass
class While(Statement):
    condition: Expression
    body: List[Statement]


@dataclass
class For(Statement):
    init: List[Statement]
    condition: Expression | None
    post: List[Statement]
    body: List[Statement]
