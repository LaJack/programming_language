from dataclasses import dataclass, field
import copy
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
class UnaryExpression(Expression):
    operator: str
    expr: Expression


@dataclass
class LiteralExpression(Expression):
    value: object
    type: str

    def __deepcopy__(self, memo):
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        if self.value is None or type(self.value) in {bool, int, float, str, bytes}:
            value = self.value
        else:
            value = copy.deepcopy(self.value, memo)
        cloned = LiteralExpression(value, self.type, span=self.span)
        memo[id(self)] = cloned
        return cloned


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
class EnumVariantExpression(Expression):
    type_ref: 'TypeReference'
    variant_name: str
    arguments: List[Expression] | None = None


@dataclass
class VariableExpression(Expression):
    name: str


@dataclass
class MemberExpression(Expression):
    target: Expression
    member: str
    member_span: SourceSpan | None = field(default=None, compare=False)


def expression_name(expression: Expression) -> str | None:
    if isinstance(expression, VariableExpression):
        return expression.name
    if isinstance(expression, MemberExpression):
        target = expression_name(expression.target)
        return None if target is None else f'{target}.{expression.member}'
    return None


def name_expression(name: str, span: SourceSpan | None = None) -> Expression:
    parts = name.split('.')
    expression: Expression = VariableExpression(parts[0], span=span)
    for member in parts[1:]:
        expression = MemberExpression(expression, member, span=span)
    return expression


@dataclass
class BorrowExpression(Expression):
    mode: str
    expr: Expression


@dataclass
class MoveExpression(Expression):
    expr: Expression


@dataclass
class DereferenceExpression(Expression):
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
    constraints: List[TypeReference] = field(default_factory=list)
    constant: bool = False
    comptime_initializer: bool = False


@dataclass
class TypeDeclaration(Statement):
    name: str
    fields: List[VariableDeclaration]
    parameters: List[VariableDeclaration] = field(default_factory=list)
    methods: List['FunctionDeclaration'] = field(default_factory=list)
    extern: bool = False
    abi: str | None = None
    language_item: str | None = None
    language_item_type: TypeReference | None = None


@dataclass
class EnumVariant(AstNode):
    name: str
    parameters: List[VariableDeclaration] = field(default_factory=list)


@dataclass
class EnumDeclaration(Statement):
    name: str
    variants: List[EnumVariant]
    parameters: List[VariableDeclaration] = field(default_factory=list)
    methods: List['FunctionDeclaration'] = field(default_factory=list)


@dataclass
class InterfaceDeclaration(Statement):
    name: str
    methods: List['FunctionDeclaration']


@dataclass
class InterfaceUse(AstNode):
    name: str


@dataclass
class ImplementationDeclaration(Statement):
    type_name: str
    interface: TypeReference
    parameters: List[VariableDeclaration] = field(default_factory=list)
    methods: List['FunctionDeclaration'] = field(default_factory=list)
    uses: List[InterfaceUse] = field(default_factory=list)


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
    interface_name: str | None = None
    synthetic: bool = False
    unsafe: bool = False


@dataclass
class IfBranch(AstNode):
    condition: Expression
    body: List[Statement]


@dataclass
class If(Statement):
    branches: List[IfBranch]
    else_body: List[Statement] | None = None


@dataclass
class MatchBinding(AstNode):
    name: str | None


@dataclass
class MatchArm(AstNode):
    variant_name: str | None
    bindings: List[MatchBinding] = field(default_factory=list)
    body: List[Statement] | None = None
    expr: Expression | None = None


@dataclass
class Match(Statement, Expression):
    scrutinee: Expression
    arms: List[MatchArm]


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
class Block(Statement):
    body: List[Statement]


@dataclass
class UnsafeBlock(Statement):
    body: List[Statement]


@dataclass(init=False)
class Assignment(Statement):
    target: Expression
    expr: Expression

    def __init__(self, name: str | Expression | None = None, expr: Expression | None = None,
                 *, target: Expression | None = None, **metadata):
        if target is not None:
            if name is not None:
                raise TypeError('Specify either name or target, not both.')
            name = target
        if name is None or expr is None:
            raise TypeError('Assignment requires a target and an expression.')
        super().__init__(**metadata)
        if isinstance(name, str):
            self.target = name_expression(name)
        elif isinstance(name, VariableExpression) and '.' in name.name:
            self.target = name_expression(name.name, name.span)
        else:
            self.target = name
        self.expr = expr

    @property
    def name(self) -> str | Expression:
        return expression_name(self.target) or self.target

    @name.setter
    def name(self, target: str | Expression) -> None:
        self.target = name_expression(target) if isinstance(target, str) else target


@dataclass(init=False)
class FunctionCall(Statement, Expression):
    callee: Expression
    parameters: List[Expression]
    interface_name: str | None = None

    def __init__(self, function_name: str | Expression | None = None,
                 parameters: List[Expression] | None = None,
                 interface_name: str | None = None, *, callee: Expression | None = None,
                 **metadata):
        if callee is not None:
            if function_name is not None:
                raise TypeError('Specify either function_name or callee, not both.')
            function_name = callee
        if function_name is None:
            raise TypeError('FunctionCall requires a callee.')
        super().__init__(**metadata)
        self.callee = name_expression(function_name) if isinstance(function_name, str) else function_name
        self.parameters = [] if parameters is None else parameters
        self.interface_name = interface_name

    @property
    def function_name(self) -> str:
        return expression_name(self.callee) or ''

    @function_name.setter
    def function_name(self, name: str) -> None:
        previous = self.callee
        self.callee = name_expression(name, previous.span)
        current = self.callee
        while isinstance(current, MemberExpression) and isinstance(previous, MemberExpression):
            if current.member != previous.member:
                break
            current.member_span = previous.member_span
            current.span = previous.span
            current, previous = current.target, previous.target
        current.span = previous.span


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
