from __future__ import annotations

try:
    from .ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        ImportDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        StructLiteralExpression,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )
except ImportError:
    from ast_nodes import (
        Assignment,
        BorrowExpression,
        CatchClause,
        CompositeExpression,
        Expression,
        FormattedStringExpression,
        For,
        FunctionCall,
        FunctionDeclaration,
        If,
        IndexExpression,
        LiteralExpression,
        ModuleDeclaration,
        ImportDeclaration,
        Print,
        Raise,
        Rethrow,
        Return,
        SliceExpression,
        StructLiteralExpression,
        Statement,
        Try,
        TypeDeclaration,
        TypeExpression,
        TypeReference,
        VariableDeclaration,
        VariableExpression,
        ViewDeclaration,
        While,
    )


class JackEmitError(Exception):
    pass


def emit_jack(ast: list[Statement]) -> str:
    return JackEmitPass().emit(ast)


class JackEmitPass:
    INDENT = '    '

    def emit(self, ast: list[Statement]) -> str:
        chunks = [self._statement(statement, 0) for statement in ast]
        chunks = [chunk for chunk in chunks if chunk]
        return '\n\n'.join(chunks) + ('\n' if chunks else '')

    def _statement(self, statement: Statement, level: int) -> str:
        if type(statement) is ModuleDeclaration:
            return self._line(level, f'module {statement.name};')
        if type(statement) is ImportDeclaration:
            return self._line(level, self._import_declaration(statement))
        if type(statement) is TypeDeclaration:
            return self._type_declaration(statement, level)
        if type(statement) is ViewDeclaration:
            return self._view_declaration(statement, level)
        if type(statement) is FunctionDeclaration:
            return self._function_declaration(statement, level, method=False)
        if type(statement) is VariableDeclaration:
            return self._line(level, self._variable_declaration(statement) + ';')
        if type(statement) is Assignment:
            return self._line(level, f'{self._assignment_target(statement.name)} = {self._expression(statement.expr)};')
        if type(statement) is FunctionCall:
            return self._line(level, f'{self._function_call(statement)};')
        if type(statement) is Return:
            if statement.expr is None:
                return self._line(level, 'return;')
            return self._line(level, f'return {self._expression(statement.expr)};')
        if type(statement) is Raise:
            return self._line(level, f'raise {self._expression(statement.expr)};')
        if type(statement) is Rethrow:
            return self._line(level, 'rethrow;')
        if type(statement) is Print:
            if statement.expr is not None:
                return self._line(level, f'print({self._expression(statement.expr)});')
            return self._line(level, f'print({statement.name});')
        if type(statement) is If:
            return self._if_statement(statement, level)
        if type(statement) is While:
            return self._while_statement(statement, level)
        if type(statement) is For:
            return self._for_statement(statement, level)
        if type(statement) is Try:
            return self._try_statement(statement, level)
        raise JackEmitError(f'Unknown statement type "{type(statement).__name__}".')

    def _import_declaration(self, statement: ImportDeclaration) -> str:
        target = statement.module_name
        if statement.symbols is not None:
            target += '.{' + ', '.join(statement.symbols) + '}'
        if statement.alias is not None:
            target += f' as {statement.alias}'
        return f'import {target};'
    def _type_declaration(self, declaration: TypeDeclaration, level: int) -> str:
        prefix = self._declaration_prefix(declaration)
        if declaration.extern:
            return self._line(level, f'{prefix}type {declaration.name};')

        parameters = ''
        if declaration.parameters:
            parameters = '(' + ', '.join(self._parameter(parameter) for parameter in declaration.parameters) + ')'
        lines = [self._line(level, f'{prefix}struct {declaration.name}{parameters} {{')]

        for field in declaration.fields:
            lines.append(self._line(level + 1, self._field_declaration(field) + ';'))

        if declaration.fields and declaration.methods:
            lines.append('')

        for index, method in enumerate(declaration.methods):
            if index > 0:
                lines.append('')
            lines.append(self._function_declaration(method, level + 1, method=True))

        lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _view_declaration(self, declaration: ViewDeclaration, level: int) -> str:
        prefix = self._declaration_prefix(declaration, allow_extern=False)
        lines = [self._line(level, f'{prefix}view {declaration.name} {{')]
        for field in declaration.fields:
            lines.append(
                self._line(
                    level + 1,
                    f'{field.mode} {self._type_reference(field.type)} {field.name};',
                )
            )
        lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _function_declaration(
        self, declaration: FunctionDeclaration, level: int, method: bool
    ) -> str:
        prefix = self._declaration_prefix(declaration)
        parameters = ', '.join(
            self._parameter(parameter)
            for parameter in self._function_parameters(declaration, method)
        )
        header_name = declaration.name
        if method and declaration.name in {'init', 'deinit'} and self._type_reference(declaration.return_type) == 'void':
            header = f'{prefix}{header_name}({parameters}){self._raises_clause(declaration)}'
        else:
            header = f'{prefix}{self._type_reference(declaration.return_type)} {header_name}({parameters}){self._raises_clause(declaration)}'

        if declaration.extern:
            return self._line(level, header + ';')

        lines = [self._line(level, header + ' {')]
        lines.extend(self._block_lines(declaration.body, level + 1))
        lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _variable_declaration(self, declaration: VariableDeclaration) -> str:
        prefix = self._declaration_prefix(declaration)
        source = f'{prefix}{self._type_reference(declaration.type)} {declaration.name}'
        if declaration.constructor_args:
            args = ', '.join(self._expression(argument) for argument in declaration.constructor_args)
            return f'{source}({args})'
        if declaration.expr is not None:
            return f'{source} = {self._expression(declaration.expr)}'
        return source

    def _field_declaration(self, declaration: VariableDeclaration) -> str:
        prefix = 'comptime ' if declaration.comptime else ''
        source = f'{prefix}{self._type_reference(declaration.type)} {declaration.name}'
        if declaration.expr is not None:
            source += f' = {self._expression(declaration.expr)}'
        return source

    def _parameter(self, declaration: VariableDeclaration) -> str:
        prefix = 'comptime ' if declaration.comptime else ''
        if (
            declaration.name == 'self'
            and declaration.type.name == 'self'
            and declaration.type.borrow is not None
        ):
            return f'{prefix}&{declaration.type.borrow} self'
        return f'{prefix}{self._type_reference(declaration.type)} {declaration.name}'

    def _function_parameters(
        self, declaration: FunctionDeclaration, method: bool
    ) -> list[VariableDeclaration]:
        if method:
            if declaration.self_parameter is None:
                raise ValueError(f'Method "{declaration.name}" must declare an explicit self parameter.')
            return [declaration.self_parameter, *declaration.parameters]
        return declaration.parameters

    def _raises_clause(self, declaration: FunctionDeclaration) -> str:
        if declaration.raises_inferred:
            return ' raises'
        if not declaration.raises:
            return ''
        return ' raises ' + ', '.join(self._type_reference(error) for error in declaration.raises)

    def _if_statement(self, statement: If, level: int) -> str:
        lines: list[str] = []
        for index, branch in enumerate(statement.branches):
            keyword = 'if' if index == 0 else 'elif'
            lines.append(self._line(level, f'{keyword} ({self._expression(branch.condition)}) {{'))
            lines.extend(self._block_lines(branch.body, level + 1))
            lines.append(self._line(level, '}'))
        if statement.else_body is not None:
            lines[-1] += ' else {'
            lines.extend(self._block_lines(statement.else_body, level + 1))
            lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _while_statement(self, statement: While, level: int) -> str:
        lines = [self._line(level, f'while ({self._expression(statement.condition)}) {{')]
        lines.extend(self._block_lines(statement.body, level + 1))
        lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _for_statement(self, statement: For, level: int) -> str:
        initializer = '' if statement.initializer is None else self._for_part(statement.initializer)
        condition = '' if statement.condition is None else self._expression(statement.condition)
        update = '' if statement.update is None else self._for_part(statement.update)
        lines = [self._line(level, f'for ({initializer}; {condition}; {update}) {{')]
        lines.extend(self._block_lines(statement.body, level + 1))
        lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _try_statement(self, statement: Try, level: int) -> str:
        lines = [self._line(level, 'try {')]
        lines.extend(self._block_lines(statement.body, level + 1))
        lines.append(self._line(level, '}'))
        for catch in statement.catches:
            lines[-1] += f' catch {self._catch_name(catch)} {{'
            lines.extend(self._block_lines(catch.body, level + 1))
            lines.append(self._line(level, '}'))
        return '\n'.join(lines)

    def _for_part(self, statement: Statement) -> str:
        if type(statement) is VariableDeclaration:
            return self._variable_declaration(statement)
        if type(statement) is Assignment:
            return f'{self._assignment_target(statement.name)} = {self._expression(statement.expr)}'
        if type(statement) is FunctionCall:
            return self._function_call(statement)
        raise JackEmitError(f'Unsupported for clause statement "{type(statement).__name__}".')

    def _catch_name(self, catch: CatchClause) -> str:
        name = self._type_reference(catch.error_type)
        if catch.name is not None:
            name += f' {catch.name}'
        return name

    def _block_lines(self, statements: list[Statement], level: int) -> list[str]:
        return [self._statement(statement, level) for statement in statements]

    def _declaration_prefix(self, statement: Statement, allow_extern: bool = True) -> str:
        parts: list[str] = []
        if statement.public:
            parts.append('pub')
        if statement.comptime:
            parts.append('comptime')
        if allow_extern and getattr(statement, 'extern', False):
            abi = getattr(statement, 'abi', None)
            if abi is None:
                parts.append('extern')
            else:
                parts.append(f'extern {self._string_literal(abi)}')
        return ''.join(part + ' ' for part in parts)

    def _type_reference(self, type_ref: TypeReference) -> str:
        prefix = ''
        if type_ref.borrow is not None:
            prefix = f'&{type_ref.borrow} '
        source = prefix + type_ref.name
        if type_ref.arguments:
            source += '(' + ', '.join(self._type_argument(argument) for argument in type_ref.arguments) + ')'
        if type_ref.is_slice:
            source += '[]'
        elif type_ref.array_size is not None:
            source += f'[{self._expression(type_ref.array_size)}]'
        return source

    def _type_argument(self, argument: object) -> str:
        if type(argument) is TypeReference:
            return self._type_reference(argument)
        if isinstance(argument, Expression):
            return self._expression(argument)
        return str(argument)

    def _assignment_target(self, target: str | Expression) -> str:
        if type(target) is str:
            return target
        return self._expression(target)

    def _expression(self, expression: Expression, parent_precedence: int = 0) -> str:
        precedence = self._precedence(expression)
        if type(expression) is LiteralExpression:
            source = self._literal(expression)
        elif type(expression) is VariableExpression:
            source = expression.name
        elif type(expression) is FunctionCall:
            source = self._function_call(expression)
        elif type(expression) is CompositeExpression:
            source = (
                f'{self._expression(expression.left, precedence)} '
                f'{expression.operator} '
                f'{self._expression(expression.right, precedence + 1)}'
            )
        elif type(expression) is FormattedStringExpression:
            source = self._formatted_string(expression)
        elif type(expression) is StructLiteralExpression:
            source = self._struct_literal(expression)
        elif type(expression) is BorrowExpression:
            source = f'&{expression.mode} {self._expression(expression.expr, precedence)}'
        elif type(expression) is IndexExpression:
            source = f'{self._expression(expression.target, precedence)}[{self._expression(expression.index)}]'
        elif type(expression) is SliceExpression:
            source = self._slice_expression(expression, precedence)
        elif type(expression) is TypeExpression:
            source = self._type_reference(expression.type_ref)
        else:
            raise JackEmitError(f'Unknown expression type "{type(expression).__name__}".')

        if precedence < parent_precedence:
            return f'({source})'
        return source

    def _function_call(self, call: FunctionCall) -> str:
        return f'{call.function_name}({", ".join(self._expression(argument) for argument in call.parameters)})'

    def _struct_literal(self, expression: StructLiteralExpression) -> str:
        fields = ', '.join(
            f'{field.name} = {self._expression(field.expr)}'
            for field in expression.fields
        )
        return f'{self._type_reference(expression.type_ref)} {{{fields}}}'

    def _slice_expression(self, expression: SliceExpression, precedence: int) -> str:
        start = '' if expression.start is None else self._expression(expression.start)
        end = '' if expression.end is None else self._expression(expression.end)
        return f'{self._expression(expression.target, precedence)}[{start}..{end}]'

    def _literal(self, literal: LiteralExpression) -> str:
        if literal.type == 'bool' or type(literal.value) is bool:
            return 'true' if bool(literal.value) else 'false'
        if literal.type == 'str' or type(literal.value) is str:
            return self._string_literal(str(literal.value))
        if literal.type == 'type' and type(literal.value) is TypeReference:
            return self._type_reference(literal.value)
        if literal.value is None:
            return 'void()'
        return str(literal.value)

    def _formatted_string(self, expression: FormattedStringExpression) -> str:
        parts: list[str] = []
        for part in expression.parts:
            if type(part) is str:
                parts.append(self._formatted_text(part))
            elif isinstance(part, Expression):
                parts.append('{' + self._expression(part) + '}')
            else:
                parts.append(self._formatted_text(str(part)))
        return 'f"' + ''.join(parts) + '"'

    def _formatted_text(self, text: str) -> str:
        return self._escape_string_body(text).replace('{', '{{').replace('}', '}}')

    def _string_literal(self, value: str) -> str:
        return '"' + self._escape_string_body(value) + '"'

    def _escape_string_body(self, value: str) -> str:
        escaped: list[str] = []
        for char in value:
            if char == '\\':
                escaped.append('\\\\')
            elif char == '"':
                escaped.append('\\"')
            elif char == '\n':
                escaped.append('\\n')
            elif char == '\r':
                escaped.append('\\r')
            elif char == '\t':
                escaped.append('\\t')
            elif char == '\0':
                escaped.append('\\0')
            else:
                escaped.append(char)
        return ''.join(escaped)

    def _precedence(self, expression: Expression) -> int:
        if type(expression) is CompositeExpression:
            if expression.operator == '+':
                return 2
            return 1
        if type(expression) in {BorrowExpression}:
            return 3
        if type(expression) in {FunctionCall, IndexExpression, SliceExpression}:
            return 4
        return 5

    def _line(self, level: int, source: str) -> str:
        return self.INDENT * level + source
