from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .ast_nodes import (
    Assignment,
    CompositeExpression,
    Declaration,
    ExpressionStatement,
    Expression,
    FunctionCall,
    FunctionDefinition,
    FunctionParameter,
    Literal,
    Print,
    Return,
    Statement,
    Variable,
    If,
)


class ParseError(SyntaxError):
    pass


@dataclass(frozen=True)
class Token:
    type: str
    value: str
    line: int
    column: int


TOKEN_PATTERN = re.compile(
    r"""
    (?P<WHITESPACE>[ \t\r\n]+)
    |(?P<COMMENT>//[^\n]*)
    |(?P<F64>\d+\.\d+)
    |(?P<I32>\d+)
    |(?P<STRING>"(?:\\.|[^"\\])*")
    |(?P<ID>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<SYMBOL>==|!=|<=|>=|\|\||&&|[{}(),;.=+\-*/<>!&|])
    |(?P<MISMATCH>.)
    """,
    re.VERBOSE,
)


class Parser:
    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0
        self._function_depth = 0

    def parse(self) -> list[Statement]:
        statements: list[Statement] = []
        while not self._check("EOF"):
            statements.extend(self._statement())
        return statements

    def _statement(self) -> list[Statement]:
        comptime = self._match_keyword("comptime")

        if self._match_keyword("print"):
            self._consume_value("(", "Expected '(' after print")
            expression = self._expression()
            self._consume_value(")", "Expected ')' after print expression")
            self._consume_value(";", "Expected ';' after print statement")
            return [Print(comptime, expression)]

        if self._match_keyword("return"):
            if self._function_depth == 0:
                raise self._error("'return' is only allowed inside a function")
            expression = self._expression()
            self._consume_value(";", "Expected ';' after return expression")
            return [Return(comptime, expression)]

        if self._match_keyword("if"):
            self._consume_value("(", "Expected '(' after if")
            condition = self._expression()
            self._consume_value(")", "Expected ')' after if condition")
            self._consume_value("{", "Expected '{' before if body")
            body: list[Statement] = []
            while not self._check_value("}"):
                if self._check("EOF"):
                    raise ParseError("Unterminated if body")
                body.extend(self._statement())
            self._consume_value("}", "Expected '}' after if body")

            elifs: list[tuple[Expression, list[Statement]]] = []
            while self._match_keyword("elif"):
                self._consume_value("(", "Expected '(' after elif")
                econd = self._expression()
                self._consume_value(")", "Expected ')' after elif condition")
                self._consume_value("{", "Expected '{' before elif body")
                ebody: list[Statement] = []
                while not self._check_value("}"):
                    if self._check("EOF"):
                        raise ParseError("Unterminated elif body")
                    ebody.extend(self._statement())
                self._consume_value("}", "Expected '}' after elif body")
                elifs.append((econd, ebody))

            else_body: list[Statement] | None = None
            if self._match_keyword("else"):
                self._consume_value("{", "Expected '{' before else body")
                else_body = []
                while not self._check_value("}"):
                    if self._check("EOF"):
                        raise ParseError("Unterminated else body")
                    else_body.extend(self._statement())
                self._consume_value("}", "Expected '}' after else body")

            return [If(comptime, condition, body, elifs, else_body)]

        if self._check("ID") and self._check("ID", offset=1):
            return self._typed_statement(comptime)

        if self._check("ID"):
            expression = self._expression()
            if isinstance(expression, Variable):
                self._consume_value("=", "Expected '=' in assignment")
                value = self._expression()
                self._consume_value(";", "Expected ';' after assignment")
                return [Assignment(comptime, expression, value)]
            self._consume_value(";", "Expected ';' after expression statement")
            return [ExpressionStatement(comptime, expression)]

        token = self._peek()
        raise ParseError(f"Unexpected token {token.value!r} at {token.line}:{token.column}")

    def _typed_statement(self, comptime: bool) -> list[Statement]:
        type_name = self._consume("ID", "Expected type name").value
        name = self._consume("ID", "Expected identifier").value

        if self._match_value("("):
            parameters = self._parameters()
            self._consume_value("{", "Expected '{' before function body")
            body: list[Statement] = []
            self._function_depth += 1
            try:
                while not self._check_value("}"):
                    if self._check("EOF"):
                        raise ParseError("Unterminated function body")
                    body.extend(self._statement())
            finally:
                self._function_depth -= 1
            self._consume_value("}", "Expected '}' after function body")
            return [
                FunctionDefinition(
                    comptime=comptime,
                    return_type=type_name,
                    name=name,
                    parameters=parameters,
                    body=body,
                )
            ]

        declaration = Declaration(comptime, Variable(name), type_name)
        if self._match_value(";"):
            return [declaration]

        if self._match_value("="):
            expression = self._expression()
        else:
            expression = self._expression()

        self._consume_value(";", "Expected ';' after declaration")
        return [declaration, Assignment(comptime, Variable(name), expression)]

    def _parameters(self) -> list[FunctionParameter]:
        parameters: list[FunctionParameter] = []
        if self._match_value(")"):
            return parameters

        while True:
            comptime = self._match_keyword("comptime")
            type_name = self._consume("ID", "Expected parameter type").value
            name = self._consume("ID", "Expected parameter name").value
            parameters.append(FunctionParameter(comptime, name, type_name))

            if self._match_value(")"):
                return parameters
            self._consume_value(",", "Expected ',' between parameters")

    def _expression(self) -> Expression:
        return self._logical_or()

    def _logical_or(self) -> Expression:
        expression = self._logical_and()
        while self._match_value("||"):
            operator = self._previous().value
            right = self._logical_and()
            expression = CompositeExpression(operator, expression, right)
        return expression

    def _logical_and(self) -> Expression:
        expression = self._equality()
        while self._match_value("&&"):
            operator = self._previous().value
            right = self._equality()
            expression = CompositeExpression(operator, expression, right)
        return expression

    def _equality(self) -> Expression:
        expression = self._comparison()
        while self._match_value("==") or self._match_value("!="):
            operator = self._previous().value
            right = self._comparison()
            expression = CompositeExpression(operator, expression, right)
        return expression

    def _comparison(self) -> Expression:
        expression = self._term()
        while self._match_value("<") or self._match_value(">") or self._match_value("<=") or self._match_value(">="):
            operator = self._previous().value
            right = self._term()
            expression = CompositeExpression(operator, expression, right)
        return expression

    def _term(self) -> Expression:
        expression = self._factor()
        while self._match_value("+") or self._match_value("-"):
            operator = self._previous().value
            right = self._factor()
            expression = CompositeExpression(operator, expression, right)
        return expression

    def _factor(self) -> Expression:
        expression = self._primary()
        while self._match_value("*") or self._match_value("/"):
            operator = self._previous().value
            right = self._primary()
            expression = CompositeExpression(operator, expression, right)
        return expression

    def _primary(self) -> Expression:
        if self._match("I32"):
            return Literal("i32", self._previous().value)
        if self._match("F64"):
            return Literal("f64", self._previous().value)
        if self._match("STRING"):
            return Literal("string", self._previous().value[1:-1])
        if self._match_value("("):
            expression = self._expression()
            self._consume_value(")", "Expected ')' after expression")
            return expression
        if self._check("ID"):
            name = self._variable_name()
            if self._match_value("("):
                return FunctionCall(name, self._arguments())
            return Variable(name)

        token = self._peek()
        raise ParseError(f"Expected expression at {token.line}:{token.column}")

    def _arguments(self) -> list[Expression]:
        arguments: list[Expression] = []
        if self._match_value(")"):
            return arguments

        while True:
            arguments.append(self._expression())
            if self._match_value(")"):
                return arguments
            self._consume_value(",", "Expected ',' between arguments")

    def _variable_name(self) -> str:
        parts = [self._consume("ID", "Expected identifier").value]
        while self._match_value("."):
            parts.append(self._consume("ID", "Expected field name after '.'").value)
        return ".".join(parts)

    def _match_keyword(self, value: str) -> bool:
        if self._check("ID") and self._peek().value == value:
            self._advance()
            return True
        return False

    def _match_value(self, value: str) -> bool:
        if self._check_value(value):
            self._advance()
            return True
        return False

    def _match(self, token_type: str) -> bool:
        if self._check(token_type):
            self._advance()
            return True
        return False

    def _consume_value(self, value: str, message: str) -> Token:
        if self._check_value(value):
            return self._advance()
        raise self._error(message)

    def _consume(self, token_type: str, message: str) -> Token:
        if self._check(token_type):
            return self._advance()
        raise self._error(message)

    def _check_value(self, value: str, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token.value == value

    def _check(self, token_type: str, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token.type == token_type

    def _advance(self) -> Token:
        token = self._peek()
        if token.type != "EOF":
            self._pos += 1
        return token

    def _previous(self) -> Token:
        return self._tokens[self._pos - 1]

    def _peek(self, offset: int = 0) -> Token:
        index = min(self._pos + offset, len(self._tokens) - 1)
        return self._tokens[index]

    def _error(self, message: str) -> ParseError:
        token = self._peek()
        return ParseError(f"{message} at {token.line}:{token.column}")


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    line_start = 0

    for match in TOKEN_PATTERN.finditer(source):
        kind = match.lastgroup
        value = match.group()
        column = match.start() - line_start + 1

        if kind in {"WHITESPACE", "COMMENT"}:
            line_breaks = value.count("\n")
            if line_breaks:
                line += line_breaks
                line_start = match.start() + value.rfind("\n") + 1
            continue
        if kind == "MISMATCH":
            raise ParseError(f"Unexpected character {value!r} at {line}:{column}")

        tokens.append(Token(kind or "", value, line, column))

    tokens.append(Token("EOF", "", line, len(source) - line_start + 1))
    return tokens


def parse(source: str) -> list[Statement]:
    """Parse source text into an AST."""
    return Parser(tokenize(source)).parse()


def parse_sources(sources: Sequence[Path | str]) -> list[Statement]:
    """Parse source files into an AST."""
    ast: list[Statement] = []
    for source in sources:
        ast.extend(parse(Path(source).read_text()))
    return ast
