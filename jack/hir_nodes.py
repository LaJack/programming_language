from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

try:
    from .source_model import SourceSpan, TypeReference
except ImportError:
    from source_model import SourceSpan, TypeReference


HIRCallKind = Literal['function', 'method', 'len', 'builtin_conversion']


@dataclass(frozen=True, kw_only=True)
class HIRNode:
    span: SourceSpan | None = field(default=None, compare=False)


@dataclass(frozen=True, kw_only=True)
class HIRProgram(HIRNode):
    declarations: list[HIRDeclaration] = field(default_factory=list)
    body: list[HIRStatement] = field(default_factory=list)
    top_level: list[HIRStatement] = field(default_factory=list)
    entry_module: str | None = None
    module_dependencies: dict[str | None, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class HIRVariableSymbol(HIRNode):
    name: str
    type_ref: TypeReference
    comptime: bool = False
    public: bool = False
    module_name: str | None = None
    source_name: str | None = None
    extern: bool = False
    abi: str | None = None
    synthetic: bool = False
    passing_mode: str = 'copy'

    @property
    def type(self) -> TypeReference:
        return self.type_ref


@dataclass(frozen=True, kw_only=True)
class HIRViewFieldSymbol(HIRNode):
    name: str
    type_ref: TypeReference
    mode: str

    @property
    def type(self) -> TypeReference:
        return self.type_ref


@dataclass(frozen=True, kw_only=True)
class HIRStatement(HIRNode):
    module_name: str | None = field(default=None, compare=False)
    source_name: str | None = field(default=None, compare=False)


@dataclass(frozen=True, kw_only=True)
class HIRDeclaration(HIRStatement):
    pass


@dataclass(frozen=True, kw_only=True)
class HIRBlock(HIRStatement):
    body: list[HIRStatement] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class HIRModuleDeclaration(HIRDeclaration):
    name: str


@dataclass(frozen=True, kw_only=True)
class HIRImportDeclaration(HIRDeclaration):
    module_name: str
    alias: str | None = None
    symbols: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class HIRTypeDeclaration(HIRDeclaration):
    name: str
    fields: list[HIRVariableSymbol]
    methods: list[HIRFunctionDeclaration] = field(default_factory=list)
    public: bool = False
    module_name: str | None = None
    source_name: str | None = None
    extern: bool = False
    abi: str | None = None

    @property
    def parameters(self) -> list[HIRVariableSymbol]:
        return []


@dataclass(frozen=True, kw_only=True)
class HIRViewDeclaration(HIRDeclaration):
    name: str
    fields: list[HIRViewFieldSymbol]
    public: bool = False
    module_name: str | None = None
    source_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class HIRFunctionDeclaration(HIRDeclaration):
    name: str
    parameters: list[HIRVariableSymbol]
    body: list[HIRStatement]
    return_type: TypeReference
    self_parameter: HIRVariableSymbol | None = None
    raises: list[TypeReference] = field(default_factory=list)
    raises_inferred: bool = False
    public: bool = False
    module_name: str | None = None
    source_name: str | None = None
    extern: bool = False
    abi: str | None = None
    interface_name: str | None = None
    synthetic: bool = False


@dataclass(frozen=True, kw_only=True)
class HIRGlobalVariable(HIRDeclaration):
    symbol: HIRVariableSymbol
    initializer: HIRExpression | None = None
    constructor_call: HIRCallExpression | None = None


@dataclass(frozen=True, kw_only=True)
class HIRExpression(HIRNode):
    type_ref: TypeReference
    read_type: TypeReference | None


@dataclass(frozen=True, kw_only=True)
class HIRLiteralExpression(HIRExpression):
    value: object
    literal_type: str


@dataclass(frozen=True, kw_only=True)
class HIRVariableExpression(HIRExpression):
    name: str


@dataclass(frozen=True, kw_only=True)
class HIRFieldAccessExpression(HIRExpression):
    target: HIRExpression
    field_name: str
    owner_type_name: str
    from_view: bool = False


@dataclass(frozen=True, kw_only=True)
class HIRBorrowExpression(HIRExpression):
    mode: str
    expr: HIRExpression


@dataclass(frozen=True, kw_only=True)
class HIRMoveExpression(HIRExpression):
    expr: HIRExpression


@dataclass(frozen=True, kw_only=True)
class HIRIndexExpression(HIRExpression):
    target: HIRExpression
    index: HIRExpression


@dataclass(frozen=True, kw_only=True)
class HIRSliceExpression(HIRExpression):
    target: HIRExpression
    start: HIRExpression | None = None
    end: HIRExpression | None = None


@dataclass(frozen=True, kw_only=True)
class HIRCompositeExpression(HIRExpression):
    left: HIRExpression
    operator: str
    right: HIRExpression


@dataclass(frozen=True, kw_only=True)
class HIRFormattedStringExpression(HIRExpression):
    parts: list[str | HIRExpression]


@dataclass(frozen=True, kw_only=True)
class HIRStructLiteralField(HIRNode):
    name: str
    expr: HIRExpression


@dataclass(frozen=True, kw_only=True)
class HIRStructLiteralExpression(HIRExpression):
    fields: list[HIRStructLiteralField]


@dataclass(frozen=True, kw_only=True)
class HIRCallTarget:
    kind: HIRCallKind
    name: str
    return_type: TypeReference
    parameters: list[HIRVariableSymbol] = field(default_factory=list)
    self_parameter: HIRVariableSymbol | None = None
    raises: list[TypeReference] = field(default_factory=list)
    extern: bool = False
    abi: str | None = None
    owner_type_name: str | None = None
    receiver_name: str | None = None


@dataclass(frozen=True, kw_only=True)
class HIRCallExpression(HIRExpression):
    target: HIRCallTarget
    arguments: list[HIRExpression]
    receiver: HIRExpression | None = None
    implicit_self_argument: HIRBorrowExpression | None = None


@dataclass(frozen=True, kw_only=True)
class HIRVariableDeclaration(HIRStatement):
    symbol: HIRVariableSymbol
    initializer: HIRExpression | None = None
    constructor_call: HIRCallExpression | None = None


@dataclass(frozen=True, kw_only=True)
class HIRAssignment(HIRStatement):
    target: HIRExpression
    expr: HIRExpression
    target_type: TypeReference


@dataclass(frozen=True, kw_only=True)
class HIRExpressionStatement(HIRStatement):
    expr: HIRExpression


@dataclass(frozen=True, kw_only=True)
class HIRReturn(HIRStatement):
    expr: HIRExpression | None = None


@dataclass(frozen=True, kw_only=True)
class HIRRaise(HIRStatement):
    expr: HIRExpression
    error_type: TypeReference


@dataclass(frozen=True, kw_only=True)
class HIRRethrow(HIRStatement):
    pass


@dataclass(frozen=True, kw_only=True)
class HIRPrint(HIRStatement):
    expr: HIRExpression
    label: str | None = None


@dataclass(frozen=True, kw_only=True)
class HIRIfBranch(HIRNode):
    condition: HIRExpression
    body: list[HIRStatement]


@dataclass(frozen=True, kw_only=True)
class HIRIf(HIRStatement):
    branches: list[HIRIfBranch]
    else_body: list[HIRStatement] | None = None


@dataclass(frozen=True, kw_only=True)
class HIRWhile(HIRStatement):
    condition: HIRExpression
    body: list[HIRStatement]


@dataclass(frozen=True, kw_only=True)
class HIRFor(HIRStatement):
    initializer: HIRStatement | None = None
    condition: HIRExpression | None = None
    update: HIRStatement | None = None
    body: list[HIRStatement] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class HIRCatchClause(HIRNode):
    error_type: TypeReference
    name: str | None
    body: list[HIRStatement]


@dataclass(frozen=True, kw_only=True)
class HIRTry(HIRStatement):
    body: list[HIRStatement]
    catches: list[HIRCatchClause]
