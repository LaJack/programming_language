from dataclasses import dataclass
from pathlib import Path

try:
    from .ast_nodes import (
        Assignment,
        AstNode,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        StructLiteralExpression,
        StructLiteralField,
        FunctionCall,
        FunctionDeclaration,
        If,
        IfBranch,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        ImportDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        SourceSpan,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        ViewField,
        While,
    )
except ImportError:
    from ast_nodes import (
        Assignment,
        AstNode,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        StructLiteralExpression,
        StructLiteralField,
        FunctionCall,
        FunctionDeclaration,
        If,
        IfBranch,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        ImportDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        SourceSpan,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        ViewField,
        While,
    )


@dataclass(frozen=True)
class Token:
    kind: str
    value: object
    span: SourceSpan

    @property
    def line(self) -> int:
        return self.span.start_line

    @property
    def column(self) -> int:
        return self.span.start_column

    @property
    def end_line(self) -> int:
        return self.span.end_line

    @property
    def end_column(self) -> int:
        return self.span.end_column

    @property
    def offset(self) -> int:
        return self.span.start_offset

    @property
    def end_offset(self) -> int:
        return self.span.end_offset


class ParseError(Exception):
    def __init__(self, message: str, span: SourceSpan | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.span = span


class Lexer:
    SYMBOLS = set('{}();,+.=<>![]&')
    TWO_CHAR_SYMBOLS = {'==', '!=', '<=', '>=', '..'}

    def __init__(self, source: str, source_path: str | Path | None = None) -> None:
        self.source = source
        self.source_path = (
            None if source_path is None else str(Path(source_path).resolve())
        )
        self.index = 0
        self.line = 1
        self.column = 1

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []

        while not self._is_at_end():
            char = self._peek()

            if char in ' \t\r':
                self._advance()
            elif char == '\n':
                self._advance_newline()
            elif char == '/' and self._peek_next() == '/':
                self._skip_line_comment()
            elif char == '/' and self._peek_next() == '*':
                self._skip_block_comment()
            elif char == 'f' and self._peek_next() == '"':
                tokens.append(self._formatted_string())
            elif char.isalpha() or char == '_':
                tokens.append(self._identifier())
            elif char.isdigit():
                tokens.append(self._number())
            elif char == '"':
                tokens.append(self._string())
            elif char + self._peek_next() in self.TWO_CHAR_SYMBOLS:
                tokens.append(self._fixed_token(char + self._peek_next()))
            elif char in self.SYMBOLS:
                tokens.append(self._fixed_token(char))
            else:
                raise ParseError(
                    f'Unexpected character {char!r} at {self.line}:{self.column}.',
                    self._span_from(
                        self.line,
                        self.column,
                        self.index,
                        self.line,
                        self.column + 1,
                        self.index + 1,
                    ),
                )

        tokens.append(self._make_token('EOF', '', self.line, self.column, self.index))
        return tokens

    def _identifier(self) -> Token:
        line = self.line
        column = self.column
        start = self.index
        while not self._is_at_end() and (self._peek().isalnum() or self._peek() == '_'):
            self._advance()
        return self._make_token('IDENT', self.source[start:self.index], line, column, start)

    def _number(self) -> Token:
        line = self.line
        column = self.column
        start = self.index
        while not self._is_at_end() and self._peek().isdigit():
            self._advance()
        if (
            not self._is_at_end()
            and self._peek() == '.'
            and self.index + 1 < len(self.source)
            and self.source[self.index + 1].isdigit()
        ):
            self._advance()
            while not self._is_at_end() and self._peek().isdigit():
                self._advance()
            return self._make_token('FLOAT', self.source[start:self.index], line, column, start)
        return self._make_token('INT', self.source[start:self.index], line, column, start)

    def _string(self) -> Token:
        line = self.line
        column = self.column
        start = self.index
        self._advance()

        chars: list[str] = []
        escapes = {
            '"': '"',
            '\\': '\\',
            'n': '\n',
            'r': '\r',
            't': '\t',
            '0': '\0',
        }

        while not self._is_at_end():
            char = self._advance()
            if char == '"':
                return self._make_token('STRING', ''.join(chars), line, column, start)
            if char == '\n':
                raise ParseError(f'Unterminated string literal at {line}:{column}.', self._span_from(line, column, start))
            if char == '\\':
                if self._is_at_end():
                    raise ParseError(f'Unterminated string literal at {line}:{column}.', self._span_from(line, column, start))
                escaped = self._advance()
                if escaped not in escapes:
                    raise ParseError(
                        f'Unknown escape sequence "\\{escaped}" at {self.line}:{self.column - 1}.',
                        self._span_from(
                            self.line,
                            self.column - 1,
                            self.index - 1,
                            self.line,
                            self.column,
                            self.index,
                        ),
                    )
                chars.append(escapes[escaped])
            else:
                chars.append(char)

        raise ParseError(f'Unterminated string literal at {line}:{column}.', self._span_from(line, column, start))

    def _formatted_string(self) -> Token:
        line = self.line
        column = self.column
        start = self.index
        self._advance()
        self._advance()

        chars: list[str] = []
        while not self._is_at_end():
            char = self._advance()
            if char == '"':
                return self._make_token('FSTRING', ''.join(chars), line, column, start)
            if char == '\n':
                raise ParseError(f'Unterminated formatted string literal at {line}:{column}.', self._span_from(line, column, start))
            if char == '\\':
                if self._is_at_end():
                    raise ParseError(f'Unterminated formatted string literal at {line}:{column}.', self._span_from(line, column, start))
                chars.append(char)
                chars.append(self._advance())
            else:
                chars.append(char)

        raise ParseError(f'Unterminated formatted string literal at {line}:{column}.', self._span_from(line, column, start))

    def _skip_line_comment(self) -> None:
        while not self._is_at_end() and self._peek() != '\n':
            self._advance()

    def _skip_block_comment(self) -> None:
        line = self.line
        column = self.column
        start = self.index
        self._advance()
        self._advance()

        while not self._is_at_end():
            if self._peek() == '*' and self._peek_next() == '/':
                self._advance()
                self._advance()
                return
            if self._peek() == '\n':
                self._advance_newline()
            else:
                self._advance()

        raise ParseError('Unterminated block comment.', self._span_from(line, column, start))

    def _fixed_token(self, value: str) -> Token:
        line = self.line
        column = self.column
        start = self.index
        for _ in value:
            self._advance()
        return self._make_token(value, value, line, column, start)

    def _make_token(
        self, kind: str, value: object, line: int, column: int, start: int
    ) -> Token:
        return Token(kind, value, self._span_from(line, column, start))

    def _span_from(
        self,
        start_line: int,
        start_column: int,
        start_offset: int,
        end_line: int | None = None,
        end_column: int | None = None,
        end_offset: int | None = None,
    ) -> SourceSpan:
        return SourceSpan(
            start_line,
            start_column,
            self.line if end_line is None else end_line,
            self.column if end_column is None else end_column,
            start_offset,
            self.index if end_offset is None else end_offset,
            self.source_path,
        )

    def _peek(self) -> str:
        return self.source[self.index]

    def _peek_next(self) -> str:
        if self.index + 1 >= len(self.source):
            return '\0'
        return self.source[self.index + 1]

    def _advance(self) -> str:
        char = self.source[self.index]
        self.index += 1
        self.column += 1
        return char

    def _advance_newline(self) -> None:
        self.index += 1
        self.line += 1
        self.column = 1

    def _is_at_end(self) -> bool:
        return self.index >= len(self.source)


class Parser:
    KEYWORDS = {
        'as',
        'bool',
        'catch',
        'comptime',
        'elif',
        'else',
        'extern',
        'false',
        'for',
        'if',
        'import',
        'module',
        'in',
        'inout',
        'out',
        'print',
        'pub',
        'raise',
        'raises',
        'return',
        'rethrow',
        'struct',
        'true',
        'try',
        'view',
        'while',
    }
    STATEMENT_KEYWORDS = {'for', 'if', 'print', 'raise', 'return', 'rethrow', 'try', 'while'}

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.current = 0

    def parse(self) -> list[Statement]:
        statements: list[Statement] = []

        while not self._check('EOF'):
            statements.append(self._declaration())

        return statements

    def _with_span(
        self, node: AstNode, start_token: Token, end_token: Token | None = None
    ) -> AstNode:
        node.span = self._span_from_tokens(start_token, end_token)
        return node

    def _span_from_tokens(
        self, start_token: Token, end_token: Token | None = None
    ) -> SourceSpan:
        if end_token is None:
            end_token = self._previous()
        return SourceSpan(
            start_token.span.start_line,
            start_token.span.start_column,
            end_token.span.end_line,
            end_token.span.end_column,
            start_token.span.start_offset,
            end_token.span.end_offset,
            start_token.span.source_path,
        )

    def _declaration(self) -> Statement:
        start_token = self._peek()
        return self._with_span(self._declaration_inner(), start_token)

    def _declaration_inner(self) -> Statement:
        if self._match_keyword('module'):
            return self._module_declaration()

        if self._match_keyword('import'):
            return self._import_declaration()

        if self._check_statement_keyword():
            return self._statement()

        is_public = self._match_keyword('pub')
        is_comptime = self._match_keyword('comptime')
        is_extern = self._match_keyword('extern')
        extern_abi = self._extern_abi() if is_extern else None

        if self._check_keyword('module') or self._check_keyword('import'):
            raise self._error(self._peek(), 'Module and import declarations cannot be modified.')

        if self._check_statement_keyword():
            if is_public:
                raise self._error(self._peek(), 'pub can only mark declarations.')
            if is_extern:
                raise self._error(self._peek(), 'extern can only mark type, variable, or function declarations.')
            statement = self._statement()
            statement.comptime = is_comptime
            return statement

        if is_extern and self._match_keyword('type'):
            name = self._identifier_value('Expected extern type name.')
            self._consume(';', 'Expected ; after extern type declaration.')
            declaration = TypeDeclaration(name, [], extern=True, abi=extern_abi)
            declaration.comptime = is_comptime
            declaration.public = is_public
            return declaration
        if self._match_keyword('struct'):
            if is_extern:
                raise self._error(self._previous(), 'extern can only mark type, variable, or function declarations.')
            declaration = self._type_declaration()
            declaration.comptime = is_comptime
            declaration.public = is_public
            return declaration

        if self._match_keyword('view'):
            if is_extern:
                raise self._error(self._previous(), 'extern can only mark type, variable, or function declarations.')
            if is_comptime:
                raise self._error(self._previous(), 'comptime cannot mark view declarations.')
            declaration = self._view_declaration()
            declaration.public = is_public
            return declaration

        start = self.current
        if self._can_start_type_reference():
            try:
                declared_type = self._type_reference()
            except ParseError:
                self.current = start
            else:
                if self._check('IDENT'):
                    name = self._identifier_value('Expected declaration name.')

                    if self._check('('):
                        if is_extern:
                            self._advance()
                            declaration = self._finish_extern_function_declaration(name, declared_type, is_comptime, extern_abi)
                            declaration.public = is_public
                            return declaration

                        if self._matching_paren_is_followed_by_function_suffix():
                            self._advance()
                            declaration = self._finish_function_declaration(name, declared_type, is_comptime)
                            declaration.public = is_public
                            return declaration

                        self._advance()
                        constructor_args = self._finish_argument_list('constructor')
                        self._consume(';', 'Expected ; after constructed variable declaration.')
                        return VariableDeclaration(
                            name,
                            declared_type,
                            constructor_args=constructor_args,
                            comptime=is_comptime,
                            public=is_public,
                        )

                    if is_extern:
                        self._consume(';', 'Expected ; after extern variable declaration.')
                        return VariableDeclaration(
                            name,
                            declared_type,
                            comptime=is_comptime,
                            public=is_public,
                            extern=True,
                            abi=extern_abi,
                        )

                    initializer = None
                    if self._match('='):
                        initializer = self._expression()
                    self._consume(';', 'Expected ; after variable declaration.')
                    return VariableDeclaration(
                        name, declared_type, initializer, comptime=is_comptime, public=is_public
                    )

        self.current = start
        if is_public:
            raise self._error(self._peek(), 'pub can only mark declarations.')
        if is_extern:
            raise self._error(self._peek(), 'extern can only mark type, variable, or function declarations.')
        statement = self._statement()
        statement.comptime = is_comptime
        return statement

    def _module_declaration(self) -> ModuleDeclaration:
        name = self._module_name('Expected module name.')
        self._consume(';', 'Expected ; after module declaration.')
        return ModuleDeclaration(name)

    def _import_declaration(self) -> ImportDeclaration:
        module_name, symbols = self._import_target()
        alias = None
        if self._match_keyword('as'):
            if symbols is not None:
                raise self._error(self._previous(), 'Selective imports cannot use an alias.')
            alias = self._identifier_value('Expected import alias.')
        self._consume(';', 'Expected ; after import declaration.')
        return ImportDeclaration(module_name, alias, symbols)

    def _import_target(self) -> tuple[str, list[str] | None]:
        parts = [self._identifier_value('Expected module name.')]
        symbols = None
        while self._match('.'):
            if self._match('{'):
                symbols = self._import_symbols()
                break
            parts.append(self._identifier_value('Expected module name after dot.'))
        return '.'.join(parts), symbols

    def _import_symbols(self) -> list[str]:
        symbols: list[str] = []
        if not self._check('}'):
            while True:
                symbols.append(self._identifier_value('Expected imported symbol name.'))
                if not self._match(','):
                    break
        self._consume('}', 'Expected } after imported symbols.')
        return symbols

    def _module_name(self, message: str) -> str:
        parts = [self._identifier_value(message)]
        while self._match('.'):
            parts.append(self._identifier_value('Expected module name after dot.'))
        return '.'.join(parts)
    def _type_declaration(self) -> TypeDeclaration:
        name = self._identifier_value('Expected struct name.')
        parameters: list[VariableDeclaration] = []
        if self._match('('):
            if not self._check(')'):
                while True:
                    parameters.append(self._parameter())
                    if not self._match(','):
                        break
            self._consume(')', 'Expected ) after struct parameters.')

        self._consume('{', 'Expected { before struct members.')

        fields: list[VariableDeclaration] = []
        methods: list[FunctionDeclaration] = []
        while not self._check('}'):
            if self._check('EOF'):
                raise self._error(self._peek(), 'Expected } after struct members.')
            member_start_token = self._peek()
            member_comptime = self._match_keyword('comptime')
            if self._check_special_method_name() and self._check_next('('):
                member_name = self._identifier_value('Expected method name.')
                self._consume('(', f'Expected ( after {member_name}.')
                method = self._with_span(
                    self._finish_function_declaration(
                        member_name, TypeReference('void'), member_comptime
                    ),
                    member_start_token,
                )
                methods.append(self._method_declaration(name, method))
                continue

            member_type = self._type_reference()
            member_name = self._identifier_value('Expected member name.')

            if self._match('('):
                method = self._with_span(
                    self._finish_function_declaration(member_name, member_type, member_comptime),
                    member_start_token,
                )
                methods.append(self._method_declaration(name, method))
                continue

            self._consume(';', 'Expected ; after field declaration.')
            fields.append(
                self._with_span(
                    VariableDeclaration(member_name, member_type, comptime=member_comptime),
                    member_start_token,
                )
            )

        self._consume('}', 'Expected } after struct members.')
        self._match(';')
        return TypeDeclaration(name, fields, parameters, methods)

    def _method_declaration(self, type_name: str, method: FunctionDeclaration) -> FunctionDeclaration:
        if method.parameters and method.parameters[0].name == 'self':
            method.self_parameter = method.parameters[0]
            method.parameters = method.parameters[1:]
        return method

    def _view_declaration(self) -> ViewDeclaration:
        name = self._identifier_value('Expected view name.')
        self._consume('{', 'Expected { before view fields.')

        fields: list[ViewField] = []
        while not self._check('}'):
            if self._check('EOF'):
                raise self._error(self._peek(), 'Expected } after view fields.')
            field_start_token = self._peek()
            mode = self._borrow_mode('in view field declaration')
            field_type = self._type_reference()
            field_name = self._identifier_value('Expected view field name.')
            self._consume(';', 'Expected ; after view field declaration.')
            fields.append(
                self._with_span(
                    ViewField(field_name, field_type, mode),
                    field_start_token,
                )
            )

        self._consume('}', 'Expected } after view fields.')
        self._match(';')
        return ViewDeclaration(name, fields)

    def _finish_function_declaration(
        self, name: str, return_type: TypeReference, is_comptime: bool
    ) -> FunctionDeclaration:
        parameters = self._function_parameters()
        raises, raises_inferred = self._raises_clause()
        body = self._block()
        return FunctionDeclaration(
            name,
            parameters,
            body,
            return_type=return_type,
            comptime=is_comptime,
            raises=raises,
            raises_inferred=raises_inferred,
        )

    def _finish_extern_function_declaration(
        self, name: str, return_type: TypeReference, is_comptime: bool, abi: str | None
    ) -> FunctionDeclaration:
        parameters = self._function_parameters()
        raises, raises_inferred = self._raises_clause()
        self._consume(';', 'Expected ; after extern function declaration.')
        return FunctionDeclaration(
            name,
            parameters,
            [],
            return_type=return_type,
            comptime=is_comptime,
            extern=True,
            abi=abi,
            raises=raises,
            raises_inferred=raises_inferred,
        )

    def _extern_abi(self) -> str | None:
        if self._match('STRING'):
            return str(self._previous().value)
        return None

    def _raises_clause(self) -> tuple[list[TypeReference], bool]:
        if not self._match_keyword('raises'):
            return [], False
        if self._check('{') or self._check(';'):
            return [], True

        raises = [self._type_reference()]
        while self._match(','):
            raises.append(self._type_reference())
        return raises, False

    def _function_parameters(self) -> list[VariableDeclaration]:
        parameters: list[VariableDeclaration] = []
        if not self._check(')'):
            while True:
                parameters.append(self._parameter())
                if not self._match(','):
                    break
        self._consume(')', 'Expected ) after function parameters.')
        return parameters

    def _parameter(self) -> VariableDeclaration:
        start_token = self._peek()
        is_comptime = self._match_keyword('comptime')
        shorthand_start = self.current
        if self._match('&'):
            borrow_token = self._previous()
            borrow = self._borrow_mode('in self parameter')
            if self._check('IDENT') and self._peek().value == 'self' and (self._check_next(')') or self._check_next(',')):
                self_token = self._advance()
                parameter_type = self._with_span(
                    TypeReference('self', borrow=borrow),
                    borrow_token,
                    self_token,
                )
                return self._with_span(
                    VariableDeclaration('self', parameter_type, comptime=is_comptime),
                    start_token,
                    self_token,
                )
            self.current = shorthand_start

        parameter_type = self._type_reference()
        parameter_name = self._identifier_value('Expected parameter name.')
        return self._with_span(
            VariableDeclaration(parameter_name, parameter_type, comptime=is_comptime),
            start_token,
        )

    def _type_reference(self) -> TypeReference:
        start_token = self._peek()
        return self._with_span(self._type_reference_inner(), start_token)

    def _type_reference_inner(self) -> TypeReference:
        borrow = None
        if self._match('&'):
            borrow = self._borrow_mode('in type reference')

        name = self._type_name_value('Expected type.')
        arguments: list[object] = []
        if self._match('('):
            if not self._check(')'):
                while True:
                    arguments.append(self._type_argument())
                    if not self._match(','):
                        break
            self._consume(')', 'Expected ) after type arguments.')

        array_size = None
        is_slice = False
        if self._match('['):
            if self._match(']'):
                is_slice = True
            else:
                array_size = self._expression()
                self._consume(']', 'Expected ] after array size.')

        return TypeReference(name, arguments, array_size=array_size, is_slice=is_slice, borrow=borrow)

    def _type_argument(self) -> object:
        if self._check('&'):
            return self._type_reference()
        if self._check_keyword('bool') and self._check_next_type_suffix_or_arguments():
            return self._type_reference()
        if self._check('IDENT') and (self._check_next('.') or self._check_next_type_suffix_or_arguments()):
            return self._type_reference()
        return self._expression()

    def _check_next_type_suffix_or_arguments(self) -> bool:
        return self._check_next('(') or self._check_next('[')

    def _can_start_type_reference(self) -> bool:
        return self._check('&') or self._check('IDENT')

    def _block(self) -> list[Statement]:
        self._consume('{', 'Expected { before block.')

        statements: list[Statement] = []
        while not self._check('}'):
            if self._check('EOF'):
                raise self._error(self._peek(), 'Expected } after block.')
            statements.append(self._declaration())

        self._consume('}', 'Expected } after block.')
        return statements

    def _statement(self) -> Statement:
        start_token = self._peek()
        return self._with_span(self._statement_inner(), start_token)

    def _statement_inner(self) -> Statement:
        if self._match_keyword('raise'):
            return self._raise_statement()

        if self._match_keyword('rethrow'):
            self._consume(';', 'Expected ; after rethrow statement.')
            return Rethrow()

        if self._match_keyword('return'):
            if self._match(';'):
                return Return()
            value = self._expression()
            self._consume(';', 'Expected ; after return value.')
            return Return(value)

        if self._match_keyword('print'):
            self._consume('(', 'Expected ( after print.')
            expr = self._expression()
            self._consume(')', 'Expected ) after print expression.')
            self._consume(';', 'Expected ; after print statement.')
            if type(expr) is FormattedStringExpression:
                return Print('', expr)
            if type(expr) is VariableExpression:
                return Print(expr.name)
            return Print(self._print_label(expr), expr)

        if self._match_keyword('if'):
            return self._if_statement()

        if self._match_keyword('while'):
            return self._while_statement()

        if self._match_keyword('for'):
            return self._for_statement()

        if self._match_keyword('try'):
            return self._try_statement()

        return self._simple_statement(consume_semicolon=True)

    def _raise_statement(self) -> Raise:
        expr = self._expression()
        self._consume(';', 'Expected ; after raise statement.')
        return Raise(expr)

    def _if_statement(self) -> If:
        branches = [IfBranch(self._parenthesized_expression('if'), self._block())]

        while self._match_keyword('elif'):
            branches.append(IfBranch(self._parenthesized_expression('elif'), self._block()))

        else_body = None
        if self._match_keyword('else'):
            else_body = self._block()

        return If(branches, else_body)

    def _while_statement(self) -> While:
        return While(self._parenthesized_expression('while'), self._block())

    def _for_statement(self) -> For:
        self._consume('(', 'Expected ( after for.')
        initializer = self._for_initializer()
        condition = None if self._check(';') else self._expression()
        self._consume(';', 'Expected ; after for condition.')
        update = None if self._check(')') else self._for_header_statement(allow_variable_declaration=False)
        self._consume(')', 'Expected ) after for clauses.')
        return For(initializer, condition, update, self._block())

    def _try_statement(self) -> Try:
        body = self._block()
        catches: list[CatchClause] = []
        while self._match_keyword('catch'):
            error_type = self._type_reference()
            binding_name = None
            if not self._check('{'):
                binding_name = self._identifier_value('Expected catch binding name or { after catch type.')
            catches.append(CatchClause(error_type, binding_name, self._block()))

        if not catches:
            raise self._error(self._previous(), 'Expected catch after try block.')
        return Try(body, catches)

    def _parenthesized_expression(self, owner: str) -> Expression:
        self._consume('(', f'Expected ( after {owner}.')
        expr = self._expression()
        self._consume(')', f'Expected ) after {owner} condition.')
        return expr

    def _for_initializer(self) -> Statement | None:
        if self._match(';'):
            return None
        initializer = self._for_header_statement(allow_variable_declaration=True)
        self._consume(';', 'Expected ; after for initializer.')
        return initializer

    def _for_header_statement(self, allow_variable_declaration: bool) -> Statement:
        start_token = self._peek()
        is_comptime = self._match_keyword('comptime')
        start = self.current

        if allow_variable_declaration and self._can_start_type_reference():
            try:
                declared_type = self._type_reference()
            except ParseError:
                self.current = start
            else:
                if self._check('IDENT'):
                    name = self._identifier_value('Expected declaration name.')
                    if self._check('('):
                        self._advance()
                        constructor_args = self._finish_argument_list('constructor')
                        return self._with_span(
                            VariableDeclaration(
                                name, declared_type, constructor_args=constructor_args, comptime=is_comptime
                            ),
                            start_token,
                        )

                    initializer = None
                    if self._match('='):
                        initializer = self._expression()
                    return self._with_span(
                        VariableDeclaration(name, declared_type, initializer, comptime=is_comptime),
                        start_token,
                    )

        self.current = start
        statement = self._simple_statement(consume_semicolon=False)
        statement.comptime = is_comptime
        return statement

    def _simple_statement(self, consume_semicolon: bool) -> Statement:
        start = self.current
        target_name = self._name()
        if self._match('('):
            call = self._finish_function_call(target_name)
            if consume_semicolon:
                self._consume(';', 'Expected ; after function call.')
            return call

        self.current = start
        target = self._assignment_target()
        if self._match('='):
            value = self._expression()
            if consume_semicolon:
                self._consume(';', 'Expected ; after assignment.')
            if type(target) is VariableExpression:
                return Assignment(target.name, value)
            return Assignment(target, value)

        raise self._error(self._previous(), 'Expected assignment or function call statement.')

    def _expression(self) -> Expression:
        start_token = self._peek()
        return self._with_span(self._comparison(), start_token)

    def _comparison(self) -> Expression:
        expr = self._addition()

        while self._match('==', '!=', '<', '>', '<=', '>='):
            operator = self._previous().value
            right = self._addition()
            expr = CompositeExpression(expr, right, operator)

        return expr

    def _addition(self) -> Expression:
        expr = self._borrow()

        while self._match('+'):
            operator = self._previous().value
            right = self._borrow()
            expr = CompositeExpression(expr, right, operator)

        return expr

    def _borrow(self) -> Expression:
        if self._match('&'):
            mode = self._borrow_mode('')
            return BorrowExpression(mode, self._postfix())
        return self._postfix()

    def _borrow_mode(self, context: str) -> str:
        for mode in ('inout', 'in', 'out'):
            if self._match_keyword(mode):
                return mode
        suffix = f' {context}' if context else ''
        raise self._error(self._peek(), f'Expected in, out, or inout after &{suffix}.')

    def _postfix(self) -> Expression:
        return self._postfix_from(self._primary())

    def _postfix_from(self, expr: Expression) -> Expression:
        while self._match('['):
            if self._match('..'):
                start = None
                end = None if self._check(']') else self._expression()
                self._consume(']', 'Expected ] after slice expression.')
                expr = SliceExpression(expr, start, end)
                continue

            first = self._expression()
            if self._match('..'):
                end = None if self._check(']') else self._expression()
                self._consume(']', 'Expected ] after slice expression.')
                expr = SliceExpression(expr, first, end)
            else:
                self._consume(']', 'Expected ] after index expression.')
                expr = IndexExpression(expr, first)

        return expr

    def _assignment_target(self) -> Expression:
        return self._postfix_from(VariableExpression(self._name()))

    def _primary(self) -> Expression:
        if self._match_keyword('true'):
            return LiteralExpression(True, 'bool')

        if self._match_keyword('false'):
            return LiteralExpression(False, 'bool')

        if self._match('INT'):
            return LiteralExpression(int(self._previous().value), 'i32')

        if self._match('FLOAT'):
            return LiteralExpression(float(self._previous().value), 'f64')

        if self._match('STRING'):
            return LiteralExpression(self._previous().value, 'str')

        if self._match('FSTRING'):
            token = self._previous()
            return self._formatted_string_expression(token.value, token)

        if self._match_keyword('bool'):
            if self._match('('):
                return self._finish_function_call('bool')
            raise self._error(self._previous(), 'Expected bool conversion call.')

        if self._match('('):
            expr = self._expression()
            self._consume(')', 'Expected ) after expression.')
            return expr

        if self._check('IDENT'):
            start = self.current
            try:
                type_ref = self._type_reference()
            except ParseError:
                self.current = start
            else:
                if self._match('{'):
                    return self._struct_literal_expression(type_ref)
                self.current = start

            name = self._name()
            if self._match('('):
                return self._finish_function_call(name)
            return VariableExpression(name)

        raise self._error(self._peek(), 'Expected expression.')

    def _struct_literal_expression(self, type_ref: TypeReference) -> StructLiteralExpression:
        fields: list[StructLiteralField] = []
        if not self._check('}'):
            while True:
                name = self._identifier_value('Expected struct literal field name.')
                self._consume('=', 'Expected = after struct literal field name.')
                fields.append(StructLiteralField(name, self._expression()))
                if not (self._match(',') or self._match(';')):
                    break
                if self._check('}'):
                    break
        self._consume('}', 'Expected } after struct literal fields.')
        return StructLiteralExpression(type_ref, fields)

    def _finish_function_call(self, function_name: str) -> FunctionCall:
        if function_name in {'sizeof', 'alignof'}:
            return FunctionCall(function_name, self._finish_type_query_argument_list(function_name))
        return FunctionCall(function_name, self._finish_argument_list('function'))

    def _finish_type_query_argument_list(self, function_name: str) -> list[Expression]:
        if self._check(')'):
            raise self._error(self._peek(), f'{function_name} expects one type argument.')

        type_ref = self._type_reference()
        if self._match(','):
            raise self._error(self._previous(), f'{function_name} expects exactly one type argument.')
        self._consume(')', f'Expected ) after {function_name} type argument.')
        return [TypeExpression(type_ref, span=type_ref.span)]

    def _finish_argument_list(self, owner: str) -> list[Expression]:
        parameters: list[Expression] = []

        if not self._check(')'):
            while True:
                parameters.append(self._expression())
                if not self._match(','):
                    break

        self._consume(')', f'Expected ) after {owner} arguments.')
        return parameters

    def _name(self) -> str:
        parts = [self._identifier_value('Expected name.')]
        while self._match('.'):
            parts.append(self._identifier_value('Expected field name after dot.'))
        return '.'.join(parts)

    def _formatted_string_expression(
        self, raw: str, token: Token
    ) -> FormattedStringExpression:
        parts: list[object] = []
        text: list[str] = []
        index = 0

        while index < len(raw):
            char = raw[index]
            if char == '{':
                if index + 1 < len(raw) and raw[index + 1] == '{':
                    text.append('{')
                    index += 2
                    continue
                if text:
                    parts.append(''.join(text))
                    text = []
                end = self._formatted_expression_end(raw, index + 1, token)
                expression_source = raw[index + 1:end].strip()
                if not expression_source:
                    raise ParseError(
                        f'Expected expression in formatted string at {token.line}:{token.column}.',
                        token.span,
                    )
                parts.append(self._parse_embedded_expression(expression_source, token))
                index = end + 1
                continue

            if char == '}':
                if index + 1 < len(raw) and raw[index + 1] == '}':
                    text.append('}')
                    index += 2
                    continue
                raise ParseError(
                    f'Unmatched }} in formatted string at {token.line}:{token.column}.',
                    token.span,
                )

            if char == '\\':
                decoded, index = self._decode_string_escape(raw, index, token)
                text.append(decoded)
                continue

            text.append(char)
            index += 1

        if text:
            parts.append(''.join(text))
        return FormattedStringExpression(parts)

    def _formatted_expression_end(self, raw: str, start: int, token: Token) -> int:
        index = start
        paren_depth = 0
        brace_depth = 0
        while index < len(raw):
            char = raw[index]
            if char == '"':
                index = self._skip_embedded_string(raw, index, token)
                continue
            if char == '(':
                paren_depth += 1
            elif char == ')':
                if paren_depth > 0:
                    paren_depth -= 1
            elif char == '{':
                brace_depth += 1
            elif char == '}':
                if brace_depth > 0:
                    brace_depth -= 1
                elif paren_depth == 0:
                    return index
            index += 1

        raise ParseError(f'Unterminated formatted expression at {token.line}:{token.column}.', token.span)

    def _skip_embedded_string(self, raw: str, start: int, token: Token) -> int:
        index = start + 1
        while index < len(raw):
            char = raw[index]
            if char == '"':
                return index + 1
            if char == '\\':
                index += 2
            else:
                index += 1
        raise ParseError(f'Unterminated string in formatted expression at {token.line}:{token.column}.', token.span)

    def _decode_string_escape(self, raw: str, index: int, token: Token) -> tuple[str, int]:
        escapes = {
            '"': '"',
            '\\': '\\',
            'n': '\n',
            'r': '\r',
            't': '\t',
            '0': '\0',
        }
        if index + 1 >= len(raw):
            raise ParseError(f'Unterminated escape in formatted string at {token.line}:{token.column}.', token.span)
        escaped = raw[index + 1]
        if escaped not in escapes:
            raise ParseError(
                f'Unknown escape sequence "\\{escaped}" in formatted string at {token.line}:{token.column}.',
                token.span,
            )
        return escapes[escaped], index + 2

    def _parse_embedded_expression(self, source: str, token: Token) -> Expression:
        parser = Parser(Lexer(source).tokenize())
        expression = parser._expression()
        if not parser._check('EOF'):
            raise ParseError(
                f'Expected end of formatted expression at {token.line}:{token.column}.',
                token.span,
            )
        return expression

    def _print_label(self, expression: Expression) -> str:
        if type(expression) is LiteralExpression:
            if expression.type == 'str':
                return f'"{expression.value}"'
            return str(expression.value)
        if type(expression) is FormattedStringExpression:
            return 'f"..."'
        if type(expression) is VariableExpression:
            return expression.name
        if type(expression) is CompositeExpression:
            return (
                f'{self._print_label(expression.left)} '
                f'{expression.operator} '
                f'{self._print_label(expression.right)}'
            )
        if type(expression) is FunctionCall:
            arguments = ', '.join(self._print_label(argument) for argument in expression.parameters)
            return f'{expression.function_name}({arguments})'
        if type(expression) is TypeExpression:
            return self._type_name_label(expression.type_ref)
        if type(expression) is StructLiteralExpression:
            fields = ', '.join(
                f'{field.name} = {self._print_label(field.expr)}'
                for field in expression.fields
            )
            return f'{self._type_name_label(expression.type_ref)} {{{fields}}}'
        if type(expression) is BorrowExpression:
            return f'&{expression.mode} {self._print_label(expression.expr)}'
        if type(expression) is IndexExpression:
            return f'{self._print_label(expression.target)}[{self._print_label(expression.index)}]'
        if type(expression) is SliceExpression:
            start = '' if expression.start is None else self._print_label(expression.start)
            end = '' if expression.end is None else self._print_label(expression.end)
            return f'{self._print_label(expression.target)}[{start}..{end}]'
        return '<expr>'

    def _type_name_label(self, type_ref: TypeReference) -> str:
        name = type_ref.name
        if type_ref.arguments:
            arguments = ', '.join(
                self._type_name_label(argument)
                if type(argument) is TypeReference
                else self._print_label(argument)
                for argument in type_ref.arguments
            )
            name = f'{name}({arguments})'
        if type_ref.array_size is not None:
            name = f'{name}[{self._print_label(type_ref.array_size)}]'
        elif type_ref.is_slice:
            name = f'{name}[]'
        if type_ref.borrow is not None:
            name = f'&{type_ref.borrow} {name}'
        return name

    def _identifier_value(self, message: str) -> str:
        token = self._consume('IDENT', message)
        if token.value in self.KEYWORDS:
            raise self._error(token, f'Keyword {token.value!r} cannot be used as a name.')
        return token.value

    def _type_name_value(self, message: str) -> str:
        parts = [self._type_identifier_value(message)]
        while self._match('.'):
            parts.append(self._type_identifier_value('Expected type name after dot.'))
        return '.'.join(parts)

    def _type_identifier_value(self, message: str) -> str:
        token = self._consume('IDENT', message)
        if token.value in self.KEYWORDS and token.value != 'bool':
            raise self._error(token, f'Keyword {token.value!r} cannot be used as a type.')
        return token.value

    def _check_statement_keyword(self) -> bool:
        return self._check('IDENT') and self._peek().value in self.STATEMENT_KEYWORDS

    def _check_special_method_name(self) -> bool:
        return self._check('IDENT') and self._peek().value in {'init', 'deinit'}

    def _matching_paren_is_followed_by_function_suffix(self) -> bool:
        depth = 0
        index = self.current
        while index < len(self.tokens):
            token = self.tokens[index]
            if token.kind == '(':
                depth += 1
            elif token.kind == ')':
                depth -= 1
                if depth == 0:
                    if index + 1 >= len(self.tokens):
                        return False
                    following = self.tokens[index + 1]
                    return following.kind == '{' or (
                        following.kind == 'IDENT' and following.value == 'raises'
                    )
            index += 1
        return False

    def _check_keyword(self, keyword: str) -> bool:
        return self._check('IDENT') and self._peek().value == keyword

    def _match_keyword(self, keyword: str) -> bool:
        if self._check_keyword(keyword):
            self._advance()
            return True
        return False

    def _match(self, *kinds: str) -> bool:
        if self._check(*kinds):
            self._advance()
            return True
        return False

    def _consume(self, kind: str, message: str) -> Token:
        if self._check(kind):
            return self._advance()
        raise self._error(self._peek(), message)

    def _check(self, *kinds: str) -> bool:
        return self._peek().kind in kinds

    def _check_next(self, kind: str) -> bool:
        if self.current + 1 >= len(self.tokens):
            return False
        return self.tokens[self.current + 1].kind == kind

    def _advance(self) -> Token:
        if not self._check('EOF'):
            self.current += 1
        return self._previous()

    def _peek(self) -> Token:
        return self.tokens[self.current]

    def _previous(self) -> Token:
        return self.tokens[self.current - 1]

    def _error(self, token: Token, message: str) -> ParseError:
        if token.kind == 'EOF':
            return ParseError(
                f'{message} Reached end of input at {token.line}:{token.column}.',
                token.span,
            )
        return ParseError(
            f'{message} Found {token.value!r} at {token.line}:{token.column}.',
            token.span,
        )


def parse(
    source: str, source_path: str | Path | None = None
) -> list[Statement]:
    return Parser(Lexer(source, source_path=source_path).tokenize()).parse()
