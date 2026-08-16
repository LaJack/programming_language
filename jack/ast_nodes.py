from dataclasses import dataclass, field
from typing import List

try:
    from .source_model import SourceSpan, TypeReference
except ImportError:
    from source_model import SourceSpan, TypeReference


@dataclass(kw_only=True)
class AstNode:
    span: SourceSpan | None = field(default=None, compare=False)


# Expressions
@dataclass
class Expression(AstNode):
    pass


@dataclass
class InvalidExpression(Expression):
    message: str


@dataclass
class CompositeExpression(Expression):
    left: Expression
    right: Expression
    operator: str


@dataclass
class LiteralExpression(Expression):
    value: object
    type: str


@dataclass
class FormattedStringExpression(Expression):
    parts: List[object]


@dataclass
class StructLiteralField(AstNode):
    name: str
    expr: Expression


@dataclass
class StructLiteralExpression(Expression):
    type_ref: 'TypeReference'
    fields: List[StructLiteralField]


@dataclass
class VariableExpression(Expression):
    name: str


@dataclass
class BorrowExpression(Expression):
    mode: str
    expr: Expression


@dataclass
class MoveExpression(Expression):
    expr: Expression


@dataclass
class IndexExpression(Expression):
    target: Expression
    index: Expression


@dataclass
class SliceExpression(Expression):
    target: Expression
    start: Expression | None = None
    end: Expression | None = None


# Types
@dataclass
class TypeExpression(Expression):
    type_ref: TypeReference


# Statements
@dataclass
class ImportBinding(AstNode):
    module_name: str
    alias: str | None = None
    symbols: List[str] | None = None


@dataclass(kw_only=True)
class Statement(AstNode):
    comptime: bool = False
    public: bool = False
    module_name: str | None = None
    source_name: str | None = None
    imports: List[ImportBinding] = field(default_factory=list)
    qualified_imports: List[ImportBinding] = field(default_factory=list)


@dataclass
class InvalidStatement(Statement):
    message: str


@dataclass
class ModuleDeclaration(Statement):
    name: str


@dataclass
class ImportDeclaration(Statement):
    module_name: str
    alias: str | None = None
    symbols: List[str] | None = None


@dataclass
class VariableDeclaration(Statement):
    name: str
    type: TypeReference
    expr: Expression | None = None
    constructor_args: List[Expression] = field(default_factory=list)
    extern: bool = False
    abi: str | None = None
    passing_mode: str = 'copy'


@dataclass
class TypeDeclaration(Statement):
    name: str
    fields: List[VariableDeclaration]
    parameters: List[VariableDeclaration] = field(default_factory=list)
    methods: List['FunctionDeclaration'] = field(default_factory=list)
    extern: bool = False
    abi: str | None = None


@dataclass
class ViewField(AstNode):
    name: str
    type: TypeReference
    mode: str


@dataclass
class ViewDeclaration(Statement):
    name: str
    fields: List[ViewField]


@dataclass
class FunctionDeclaration(Statement):
    name: str
    parameters: List[VariableDeclaration]
    body: List[Statement]
    return_type: TypeReference
    extern: bool = False
    abi: str | None = None
    raises: List[TypeReference] = field(default_factory=list)
    raises_inferred: bool = False
    self_parameter: VariableDeclaration | None = None


@dataclass
class IfBranch(AstNode):
    condition: Expression
    body: List[Statement]


@dataclass
class If(Statement):
    branches: List[IfBranch]
    else_body: List[Statement] | None = None


@dataclass
class While(Statement):
    condition: Expression
    body: List[Statement]


@dataclass
class For(Statement):
    initializer: Statement | None = None
    condition: Expression | None = None
    update: Statement | None = None
    body: List[Statement] = field(default_factory=list)


@dataclass
class CatchClause(AstNode):
    error_type: TypeReference
    name: str | None
    body: List[Statement]


@dataclass
class Try(Statement):
    body: List[Statement]
    catches: List[CatchClause]


@dataclass
class Assignment(Statement):
    name: str | Expression
    expr: Expression


@dataclass
class FunctionCall(Statement, Expression):
    function_name: str
    parameters: List[Expression]


@dataclass
class Return(Statement):
    expr: Expression | None = None


@dataclass
class Raise(Statement):
    expr: Expression


@dataclass
class Rethrow(Statement):
    pass


@dataclass
class Print(Statement):
    name: str
    expr: Expression | None = None
