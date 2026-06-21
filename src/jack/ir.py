from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IRModule:
    globals: list["IRGlobal"]
    functions: list["IRFunction"]


@dataclass
class IRGlobal:
    name: str
    type: str
    initializer: "IRLiteral"


@dataclass
class IRParameter:
    name: str
    type: str


@dataclass
class IRFunction:
    name: str
    return_type: str
    parameters: list[IRParameter]
    body: list["IRStatement"]


@dataclass
class IRStatement:
    pass


@dataclass
class IRDeclaration(IRStatement):
    name: str
    type: str


@dataclass
class IRAssignment(IRStatement):
    name: str
    type: str
    value: "IRExpression"


@dataclass
class IRPrint(IRStatement):
    expression: "IRExpression"


@dataclass
class IRExpressionStatement(IRStatement):
    expression: "IRExpression"


@dataclass
class IRReturn(IRStatement):
    expression: "IRExpression"


@dataclass
class IRIf(IRStatement):
    condition: "IRExpression"
    then_body: list["IRStatement"]
    else_body: list["IRStatement"]


@dataclass
class IRWhile(IRStatement):
    condition: "IRExpression"
    body: list["IRStatement"]


@dataclass
class IRExpression:
    type: str


@dataclass
class IRLiteral(IRExpression):
    value: str


@dataclass
class IRVariable(IRExpression):
    name: str


@dataclass
class IRBinary(IRExpression):
    operator: str
    left: IRExpression
    right: IRExpression


@dataclass
class IRCall(IRExpression):
    name: str
    arguments: list[IRExpression]
