from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TextIO

from .ast_nodes import (
    For,
    FunctionDeclaration,
    If,
    ModuleDeclaration,
    SourceSpan,
    Statement,
    Try,
    TypeDeclaration,
    TypeReference,
    VariableDeclaration,
    ViewDeclaration,
    While,
)
from .builtin_types import is_builtin_type
from .parser import Lexer, ParseError, Token, parse
from .lsp_analysis import (
    BUILTIN_TYPES,
    JACK_KEYWORDS,
    ProjectAnalysis,
    ProjectAnalyzer,
    SemanticModel,
    SemanticSymbol,
    identifier_prefix,
    offset_at_position,
    path_from_uri,
    uri_from_path,
    valid_identifier,
)


JsonValue = dict[str, object]


@dataclass
class Document:
    uri: str
    text: str
    version: int | None = None


@dataclass
class SymbolEntry:
    name: str
    kind: int
    detail: str | None
    hover: str
    target_span: SourceSpan
    selection_span: SourceSpan


BUILTIN_HOVER_TYPES = {'void', 'str', 'c_char', 'c_void', 'type'}
MEMBER_SYMBOL_KINDS = {6, 8}


def diagnostics_for_source(source: str) -> list[JsonValue]:
    try:
        parse(source)
    except ParseError as err:
        return [
            {
                'range': range_from_span(err.span) if err.span is not None else range_from_error(str(err), source),
                'severity': 1,
                'source': 'jack',
                'message': str(err),
            }
        ]
    return []


def document_symbols_for_source(source: str) -> list[JsonValue]:
    try:
        statements = parse(source)
    except ParseError:
        return []
    return [symbol for statement in statements for symbol in _statement_symbols(statement)]


def hover_for_source(source: str, position: JsonValue) -> JsonValue | None:
    token = token_at_position(source, position)
    if token is None or token.kind != 'IDENT':
        return None

    if _is_builtin_hover_name(str(token.value)):
        return {
            'contents': _markdown(f'builtin type {token.value}'),
            'range': range_from_span(token.span),
        }

    entry = symbol_entry_at_token(source, token)
    if entry is None:
        return None
    return {
        'contents': _markdown(entry.hover),
        'range': range_from_span(token.span),
    }


def definition_for_source(source: str, uri: str, position: JsonValue) -> list[JsonValue]:
    token = token_at_position(source, position)
    if token is None or token.kind != 'IDENT':
        return []
    entry = symbol_entry_at_token(source, token)
    if entry is None:
        return []
    return [
        {
            'uri': uri,
            'range': range_from_span(entry.selection_span),
        }
    ]


def symbol_entry_at_token(source: str, token: Token) -> SymbolEntry | None:
    try:
        entries = symbol_entries_for_source(source)
        tokens = Lexer(source).tokenize()
    except ParseError:
        return None

    token_name = str(token.value)
    matching = [entry for entry in entries if _symbol_name_matches(entry, token_name)]
    if not matching:
        return None

    exact = [entry for entry in matching if span_contains_span(entry.selection_span, token.span)]
    if exact:
        return exact[0]

    if _is_member_access_token(tokens, token):
        members = [entry for entry in matching if entry.kind in MEMBER_SYMBOL_KINDS]
        if members:
            matching = members

    preceding = [entry for entry in matching if entry.selection_span.start_offset <= token.span.start_offset]
    if preceding:
        return max(preceding, key=lambda entry: entry.selection_span.start_offset)
    return matching[0]


def symbol_entries_for_source(source: str) -> list[SymbolEntry]:
    statements = parse(source)
    tokens = Lexer(source).tokenize()
    entries: list[SymbolEntry] = []
    for statement in statements:
        _collect_statement_entries(statement, tokens, entries)
    return entries


def token_at_position(source: str, position: JsonValue) -> Token | None:
    line = position.get('line')
    character = position.get('character')
    if not isinstance(line, int) or not isinstance(character, int):
        return None
    target_line = line + 1
    target_column = character + 1
    try:
        tokens = Lexer(source).tokenize()
    except ParseError:
        return None
    for token in tokens:
        if token.kind == 'EOF':
            continue
        if span_contains_position(token.span, target_line, target_column):
            return token
    return None


def span_contains_position(span: SourceSpan, line: int, column: int) -> bool:
    if line < span.start_line or line > span.end_line:
        return False
    if line == span.start_line and column < span.start_column:
        return False
    if line == span.end_line and column >= span.end_column:
        return False
    return True


def span_contains_span(outer: SourceSpan, inner: SourceSpan) -> bool:
    return outer.start_offset <= inner.start_offset and inner.end_offset <= outer.end_offset


def _collect_statement_entries(
    statement: Statement, tokens: list[Token], entries: list[SymbolEntry]
) -> None:
    if isinstance(statement, ModuleDeclaration):
        entries.append(_entry(statement.name, 2, statement, tokens, f'module {statement.name}', 'module'))
        return

    if isinstance(statement, TypeDeclaration):
        entries.append(_entry(statement.name, 23, statement, tokens, f'struct {statement.name}', 'type'))
        for field in statement.fields:
            entries.append(
                _entry(
                    field.name,
                    8,
                    field,
                    tokens,
                    f'field {_type_label(field.type)} {field.name}',
                    'variable',
                    detail=_type_label(field.type),
                )
            )
        for method in statement.methods:
            entries.append(_function_entry(method, tokens, method_kind=True, owner_name=statement.name))
            _collect_function_scope(method, tokens, entries, owner_name=statement.name)
        return

    if isinstance(statement, ViewDeclaration):
        entries.append(_entry(statement.name, 11, statement, tokens, f'view {statement.name}', 'type'))
        for field in statement.fields:
            field_label = f'{field.mode} {_type_label(field.type)} {field.name}'
            entries.append(
                _entry(
                    field.name,
                    8,
                    field,
                    tokens,
                    f'view field {field_label}',
                    'view_field',
                    detail=f'{field.mode} {_type_label(field.type)}',
                )
            )
        return

    if isinstance(statement, FunctionDeclaration):
        entries.append(_function_entry(statement, tokens))
        _collect_function_scope(statement, tokens, entries)
        return

    if isinstance(statement, VariableDeclaration):
        entries.append(
            _entry(
                statement.name,
                13,
                statement,
                tokens,
                f'{_type_label(statement.type)} {statement.name}',
                'variable',
                detail=_type_label(statement.type),
            )
        )
        return

    if isinstance(statement, If):
        for branch in statement.branches:
            for nested in branch.body:
                _collect_statement_entries(nested, tokens, entries)
        for nested in statement.else_body or []:
            _collect_statement_entries(nested, tokens, entries)
        return

    if isinstance(statement, While):
        for nested in statement.body:
            _collect_statement_entries(nested, tokens, entries)
        return

    if isinstance(statement, For):
        if statement.initializer is not None:
            _collect_statement_entries(statement.initializer, tokens, entries)
        if statement.update is not None:
            _collect_statement_entries(statement.update, tokens, entries)
        for nested in statement.body:
            _collect_statement_entries(nested, tokens, entries)
        return

    if isinstance(statement, Try):
        for nested in statement.body:
            _collect_statement_entries(nested, tokens, entries)
        for catch in statement.catches:
            for nested in catch.body:
                _collect_statement_entries(nested, tokens, entries)


def _collect_function_scope(
    declaration: FunctionDeclaration,
    tokens: list[Token],
    entries: list[SymbolEntry],
    owner_name: str | None = None,
) -> None:
    parameters = declaration.parameters
    if declaration.self_parameter is not None:
        parameters = [declaration.self_parameter, *parameters]

    for parameter in parameters:
        entries.append(
            _entry(
                parameter.name,
                13,
                parameter,
                tokens,
                f'parameter {_parameter_label(parameter, owner_name)}',
                'variable',
                detail=_type_label(parameter.type),
            )
        )
    for statement in declaration.body:
        _collect_statement_entries(statement, tokens, entries)


def _function_entry(
    declaration: FunctionDeclaration,
    tokens: list[Token],
    method_kind: bool = False,
    owner_name: str | None = None,
) -> SymbolEntry:
    if declaration.name == 'init':
        kind = 9
        hover = f'constructor {declaration.name}({_parameter_labels(declaration, owner_name)})'
    elif method_kind:
        kind = 6
        hover = f'method {_type_label(declaration.return_type)} {declaration.name}({_parameter_labels(declaration, owner_name)})'
    else:
        kind = 12
        hover = f'{_type_label(declaration.return_type)} {declaration.name}({_parameter_labels(declaration, owner_name)})'
    return _entry(
        declaration.name,
        kind,
        declaration,
        tokens,
        hover,
        'function',
        detail=_type_label(declaration.return_type),
    )


def _parameter_labels(
    declaration: FunctionDeclaration, owner_name: str | None = None
) -> str:
    parameters = declaration.parameters
    if declaration.self_parameter is not None:
        parameters = [declaration.self_parameter, *parameters]
    return ', '.join(_parameter_label(parameter, owner_name) for parameter in parameters)


def _parameter_label(parameter: VariableDeclaration, owner_name: str | None = None) -> str:
    if (
        parameter.name == 'self'
        and parameter.type.name == 'self'
        and parameter.type.borrow is not None
    ):
        if owner_name is None:
            return f'&{parameter.type.borrow} self'
        return f'&{parameter.type.borrow} {owner_name} self'
    return f'{_type_label(parameter.type)} {parameter.name}'


def _entry(
    name: str,
    kind: int,
    node: Statement,
    tokens: list[Token],
    hover: str,
    role: str,
    detail: str | None = None,
) -> SymbolEntry:
    target_span = node.span or SourceSpan(1, 1, 1, 1, 0, 0)
    selection_span = _name_span(tokens, node, name, role) or target_span
    return SymbolEntry(name, kind, detail, hover, target_span, selection_span)


def _name_span(tokens: list[Token], node: Statement, name: str, role: str) -> SourceSpan | None:
    if node.span is None:
        return None
    if role == 'module':
        return _module_name_span(tokens, node.span, name)

    scoped_tokens = [token for token in tokens if _token_inside(token, node.span)]
    candidates = [token for token in scoped_tokens if token.kind == 'IDENT' and token.value == name]
    if not candidates:
        return None

    if role == 'type':
        for index, token in enumerate(scoped_tokens):
            if token in candidates and index > 0 and scoped_tokens[index - 1].value == 'struct':
                return token.span
        return candidates[0].span

    if role == 'function':
        for index, token in enumerate(scoped_tokens):
            if token in candidates and index + 1 < len(scoped_tokens) and scoped_tokens[index + 1].value == '(':
                return token.span
        return candidates[0].span

    if role == 'view_field':
        for index, token in enumerate(scoped_tokens):
            if token in candidates and index > 0 and scoped_tokens[index - 1].kind == 'IDENT':
                return token.span
        return candidates[-1].span

    if role == 'variable' and isinstance(node, VariableDeclaration) and node.type.span is not None:
        after_type = [token for token in candidates if token.span.start_offset >= node.type.span.end_offset]
        if after_type:
            return after_type[0].span

    return candidates[-1].span


def _module_name_span(tokens: list[Token], span: SourceSpan, name: str) -> SourceSpan | None:
    parts = name.split('.')
    scoped_tokens = [token for token in tokens if _token_inside(token, span)]
    for index, token in enumerate(scoped_tokens):
        if token.kind != 'IDENT' or token.value != parts[0]:
            continue
        cursor = index
        matched = True
        for part in parts[1:]:
            if cursor + 2 >= len(scoped_tokens):
                matched = False
                break
            if scoped_tokens[cursor + 1].value != '.' or scoped_tokens[cursor + 2].value != part:
                matched = False
                break
            cursor += 2
        if matched:
            return SourceSpan(
                token.span.start_line,
                token.span.start_column,
                scoped_tokens[cursor].span.end_line,
                scoped_tokens[cursor].span.end_column,
                token.span.start_offset,
                scoped_tokens[cursor].span.end_offset,
                token.span.source_path,
            )
    return None


def _token_inside(token: Token, span: SourceSpan) -> bool:
    return token.kind != 'EOF' and span.start_offset <= token.span.start_offset and token.span.end_offset <= span.end_offset


def _is_member_access_token(tokens: list[Token], token: Token) -> bool:
    for index, candidate in enumerate(tokens):
        if candidate.span == token.span:
            return index > 0 and tokens[index - 1].value == '.'
    return False


def _symbol_name_matches(entry: SymbolEntry, token_name: str) -> bool:
    return token_name == entry.name or token_name == entry.name.split('.')[-1]


def _is_builtin_hover_name(name: str) -> bool:
    return is_builtin_type(name) or name in BUILTIN_HOVER_TYPES


def _markdown(value: str) -> JsonValue:
    return {'kind': 'markdown', 'value': f'```jack\n{value}\n```'}


def _statement_symbols(statement: Statement) -> list[JsonValue]:
    if isinstance(statement, ModuleDeclaration):
        return [_document_symbol(statement.name, 2, statement)]
    if isinstance(statement, TypeDeclaration):
        children = [
            _document_symbol(field.name, 8, field, _type_label(field.type))
            for field in statement.fields
        ]
        children.extend(_function_symbol(method, method_kind=True) for method in statement.methods)
        return [_document_symbol(statement.name, 23, statement, children=children)]
    if isinstance(statement, ViewDeclaration):
        children = [
            _document_symbol(field.name, 8, field, f'{field.mode} {_type_label(field.type)}')
            for field in statement.fields
        ]
        return [_document_symbol(statement.name, 11, statement, children=children)]
    if isinstance(statement, FunctionDeclaration):
        return [_function_symbol(statement)]
    if isinstance(statement, VariableDeclaration):
        return [_document_symbol(statement.name, 13, statement, _type_label(statement.type))]
    return []


def _function_symbol(declaration: FunctionDeclaration, method_kind: bool = False) -> JsonValue:
    if declaration.name == 'init':
        kind = 9
    elif method_kind:
        kind = 6
    else:
        kind = 12
    return _document_symbol(declaration.name, kind, declaration, _type_label(declaration.return_type))


def _document_symbol(
    name: str,
    kind: int,
    node: Statement,
    detail: str | None = None,
    children: list[JsonValue] | None = None,
) -> JsonValue:
    item: JsonValue = {
        'name': name,
        'kind': kind,
        'range': range_from_span(node.span) if node.span is not None else _zero_range(),
        'selectionRange': range_from_span(node.span) if node.span is not None else _zero_range(),
    }
    if detail:
        item['detail'] = detail
    if children is not None:
        item['children'] = children
    return item


def _type_label(type_ref: TypeReference | None) -> str | None:
    if type_ref is None:
        return None
    name = type_ref.name
    if type_ref.arguments:
        arguments = ', '.join(
            _type_label(argument) if isinstance(argument, TypeReference) else '<expr>'
            for argument in type_ref.arguments
        )
        name = f'{name}({arguments})'
    if type_ref.array_size is not None:
        name = f'{name}[...]'
    elif type_ref.is_slice:
        name = f'{name}[]'
    if type_ref.borrow is not None:
        name = f'&{type_ref.borrow} {name}'
    return name


def _zero_range() -> JsonValue:
    return {
        'start': {'line': 0, 'character': 0},
        'end': {'line': 0, 'character': 0},
    }


def range_from_span(span: SourceSpan) -> JsonValue:
    start_line = max(span.start_line - 1, 0)
    start_character = max(span.start_column - 1, 0)
    end_line = max(span.end_line - 1, 0)
    end_character = max(span.end_column - 1, 0)
    if end_line == start_line and end_character <= start_character:
        end_character = start_character + 1
    return {
        'start': {'line': start_line, 'character': start_character},
        'end': {'line': end_line, 'character': end_character},
    }


def range_from_error(message: str, source: str) -> JsonValue:
    match = re.search(r'at (\d+):(\d+)', message)
    if match is None:
        line = 0
        character = 0
    else:
        line = max(int(match.group(1)) - 1, 0)
        character = max(int(match.group(2)) - 1, 0)

    lines = source.splitlines()
    if not lines:
        line = 0
        character = 0
        end_character = 0
    else:
        line = min(line, len(lines) - 1)
        character = min(character, len(lines[line]))
        end_character = min(character + 1, len(lines[line]))

    return {
        'start': {'line': line, 'character': character},
        'end': {'line': line, 'character': end_character},
    }


class LanguageServer:
    def __init__(self, stdin: BinaryIO, stdout: BinaryIO, stderr: TextIO) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self.documents: dict[str, Document] = {}
        self.shutting_down = False
        self.workspace_roots: list[Path] = []
        self.module_roots: list[Path] = []
        self.import_overrides: dict[str, str] = {}
        self.analysis_delay = 0.2
        self.analyzer: ProjectAnalyzer | None = None
        self.semantic_model: SemanticModel | None = None
        self.published_semantic_uris: set[str] = set()
        self.analysis_generation = 0
        self.analysis_timer: threading.Timer | None = None
        self.analysis_executor: ThreadPoolExecutor | None = None
        self.write_lock = threading.Lock()

    def run(self) -> None:
        try:
            while True:
                message = self._read_message()
                if message is None:
                    return
                if self._handle_message(message):
                    return
        finally:
            self._stop_analysis()

    def _stop_analysis(self) -> None:
        if self.analysis_timer is not None:
            self.analysis_timer.cancel()
        if self.analysis_executor is not None:
            self.analysis_executor.shutdown(wait=False, cancel_futures=True)

    def _handle_message(self, message: JsonValue) -> bool:
        method = message.get('method')
        request_id = message.get('id')
        params = message.get('params')
        if not isinstance(params, dict):
            params = {}

        try:
            if method == 'initialize':
                self._respond(request_id, self._initialize_result(params))
            elif method == 'initialized':
                pass
            elif method == 'shutdown':
                self.shutting_down = True
                self._stop_analysis()
                self._respond(request_id, None)
            elif method == 'exit':
                return True
            elif method == 'textDocument/didOpen':
                self._did_open(params)
            elif method == 'textDocument/didChange':
                self._did_change(params)
            elif method == 'textDocument/didSave':
                self._did_save(params)
            elif method == 'textDocument/didClose':
                self._did_close(params)
            elif method == 'textDocument/documentSymbol' and request_id is not None:
                self._respond(request_id, self._document_symbols(params))
            elif method == 'textDocument/hover' and request_id is not None:
                self._respond(request_id, self._hover(params))
            elif method == 'textDocument/definition' and request_id is not None:
                self._respond(request_id, self._definition(params))
            elif method == 'textDocument/completion' and request_id is not None:
                self._respond(request_id, self._completion(params))
            elif method == 'textDocument/references' and request_id is not None:
                self._respond(request_id, self._references(params))
            elif method == 'textDocument/prepareRename' and request_id is not None:
                self._respond(request_id, self._prepare_rename(params))
            elif method == 'textDocument/rename' and request_id is not None:
                self._respond(request_id, self._rename(params))
            elif method == 'workspace/didChangeWatchedFiles':
                self._did_change_watched_files(params)
            elif request_id is not None:
                self._respond_error(request_id, -32601, f'Method not found: {method}')
        except Exception as err:  # pragma: no cover - defensive LSP boundary
            self._log(f'{type(err).__name__}: {err}', message_type=1)
            if request_id is not None:
                self._respond_error(request_id, -32603, str(err))

        return False

    def _initialize_result(self, params: JsonValue) -> JsonValue:
        self._configure_project(params)
        return {
            'capabilities': {
                'textDocumentSync': {
                    'openClose': True,
                    'change': 1,
                    'save': {'includeText': True},
                },
                'documentSymbolProvider': True,
                'hoverProvider': True,
                'definitionProvider': True,
                'completionProvider': {
                    'resolveProvider': False,
                    'triggerCharacters': ['.'],
                },
                'referencesProvider': True,
                'renameProvider': {'prepareProvider': True},
            },
            'serverInfo': {
                'name': 'jack-lsp',
                'version': '0.1.0',
            },
        }

    def _did_open(self, params: JsonValue) -> None:
        text_document = params.get('textDocument')
        if not isinstance(text_document, dict):
            return
        uri = text_document.get('uri')
        text = text_document.get('text')
        if not isinstance(uri, str) or not isinstance(text, str):
            return
        version = text_document.get('version')
        self.documents[uri] = Document(uri, text, version if isinstance(version, int) else None)
        self._publish_diagnostics(uri)
        self._schedule_analysis(uri)

    def _did_change(self, params: JsonValue) -> None:
        text_document = params.get('textDocument')
        content_changes = params.get('contentChanges')
        if not isinstance(text_document, dict) or not isinstance(content_changes, list):
            return
        uri = text_document.get('uri')
        if not isinstance(uri, str) or not content_changes:
            return

        last_change = content_changes[-1]
        if not isinstance(last_change, dict) or not isinstance(last_change.get('text'), str):
            return

        document = self.documents.get(uri, Document(uri, ''))
        version = text_document.get('version')
        document.text = last_change['text']
        document.version = version if isinstance(version, int) else document.version
        self.documents[uri] = document
        self._publish_diagnostics(uri)
        self._schedule_analysis(uri)

    def _did_save(self, params: JsonValue) -> None:
        text_document = params.get('textDocument')
        if not isinstance(text_document, dict):
            return
        uri = text_document.get('uri')
        if not isinstance(uri, str):
            return
        text = params.get('text')
        if isinstance(text, str):
            document = self.documents.get(uri, Document(uri, ''))
            document.text = text
            self.documents[uri] = document
        self._publish_diagnostics(uri)
        self._schedule_analysis(uri, full_comptime=True, delay=0)

    def _did_close(self, params: JsonValue) -> None:
        text_document = params.get('textDocument')
        if not isinstance(text_document, dict):
            return
        uri = text_document.get('uri')
        if not isinstance(uri, str):
            return
        self.documents.pop(uri, None)
        self._send_notification(
            'textDocument/publishDiagnostics',
            {'uri': uri, 'diagnostics': []},
        )
        self._schedule_analysis(self._first_document_uri(), delay=0)

    def _document_symbols(self, params: JsonValue) -> list[JsonValue]:
        text_document = params.get('textDocument')
        if not isinstance(text_document, dict):
            return []
        uri = text_document.get('uri')
        if not isinstance(uri, str):
            return []
        document = self.documents.get(uri)
        if document is None:
            return []
        return document_symbols_for_source(document.text)

    def _hover(self, params: JsonValue) -> JsonValue | None:
        document = self._document_from_position_request(params)
        position = params.get('position')
        if document is None or not isinstance(position, dict):
            return None
        semantic = self._semantic_symbol_at(document, position)
        if semantic is None:
            return hover_for_source(document.text, position)
        symbol, occurrence = semantic
        detail = symbol.signature
        qualifiers = []
        if symbol.public:
            qualifiers.append('public')
        if symbol.extern:
            qualifiers.append('extern')
        qualifiers.append(f'module {symbol.module_name}')
        return {
            'contents': _markdown(
                detail + ('\n\n' + ', '.join(qualifiers) if qualifiers else '')
            ),
            'range': range_from_span(occurrence.span),
        }

    def _definition(self, params: JsonValue) -> list[JsonValue]:
        document = self._document_from_position_request(params)
        position = params.get('position')
        if document is None or not isinstance(position, dict):
            return []
        semantic = self._semantic_symbol_at(document, position)
        if semantic is None:
            return definition_for_source(document.text, document.uri, position)
        symbol, _ = semantic
        return [{
            'uri': uri_from_path(symbol.path),
            'range': range_from_span(symbol.selection_span),
        }]

    def _completion(self, params: JsonValue) -> list[JsonValue]:
        document = self._document_from_position_request(params)
        position = params.get('position')
        if document is None or not isinstance(position, dict):
            return []
        line = position.get('line')
        character = position.get('character')
        if not isinstance(line, int) or not isinstance(character, int):
            return []
        path = path_from_uri(document.uri)
        if path is None:
            return []
        offset = offset_at_position(document.text, line, character)
        prefix, start = identifier_prefix(document.text, offset)
        candidates: list[tuple[str, int, str]] = []
        model = self.semantic_model
        before = document.text[:start]
        member_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\.\s*$', before)
        if model is not None and member_match is not None:
            receiver = member_match.group(1)
            receiver_offset = member_match.start(1)
            occurrence = model.occurrence_at(path, receiver_offset)
            if occurrence is not None:
                receiver_symbol = model.symbols.get(occurrence.symbol_id)
                owner = self._type_symbol(receiver_symbol.resolved_type if receiver_symbol else None)
                if owner is not None:
                    for symbol_id in model.members.get(owner.id, []):
                        symbol = model.symbols[symbol_id]
                        candidates.append((symbol.name, _completion_kind(symbol), symbol.signature))
            if not candidates:
                alias_module = self._import_alias_module(document.text, receiver)
                if alias_module is not None:
                    for symbol in model.symbols.values():
                        if symbol.module_name == alias_module and symbol.public and symbol.container_id is None:
                            candidates.append((symbol.name, _completion_kind(symbol), symbol.signature))
        elif re.search(r'\bimport\s+[A-Za-z0-9_.]*$', document.text[:offset]):
            if model is not None:
                candidates.extend((name, 9, f'module {name}') for name in sorted(model.modules))
        else:
            if model is not None:
                for symbol in model.visible_symbols(path, offset):
                    candidates.append((symbol.name, _completion_kind(symbol), symbol.signature))
            candidates.extend((name, 7, f'builtin type {name}') for name in sorted(BUILTIN_TYPES))
            candidates.extend((name, 14, name) for name in sorted(JACK_KEYWORDS))
        seen = set()
        result = []
        for label, kind, detail in sorted(candidates, key=lambda item: item[0]):
            if label in seen or (prefix and not label.startswith(prefix)):
                continue
            seen.add(label)
            result.append({
                'label': label,
                'kind': kind,
                'detail': detail,
                'textEdit': {
                    'range': {
                        'start': {'line': line, 'character': character - len(prefix)},
                        'end': {'line': line, 'character': character},
                    },
                    'newText': label,
                },
            })
        return result

    def _references(self, params: JsonValue) -> list[JsonValue]:
        document = self._document_from_position_request(params)
        position = params.get('position')
        if document is None or not isinstance(position, dict) or self.semantic_model is None:
            return []
        semantic = self._semantic_symbol_at(document, position)
        if semantic is None:
            return []
        symbol, _ = semantic
        context = params.get('context')
        include_declaration = bool(
            isinstance(context, dict) and context.get('includeDeclaration')
        )
        return [
            {'uri': uri_from_path(item.path), 'range': range_from_span(item.span)}
            for item in self.semantic_model.occurrences
            if item.symbol_id == symbol.id
            and (include_declaration or not item.declaration)
        ]

    def _prepare_rename(self, params: JsonValue) -> JsonValue | None:
        document = self._document_from_position_request(params)
        position = params.get('position')
        if document is None or not isinstance(position, dict):
            return None
        semantic = self._semantic_symbol_at(document, position)
        if semantic is None or not semantic[0].renameable:
            return None
        symbol, occurrence = semantic
        if not self._model_is_current_for(symbol.id):
            return None
        return {'range': range_from_span(occurrence.span), 'placeholder': symbol.name}

    def _rename(self, params: JsonValue) -> JsonValue:
        document = self._document_from_position_request(params)
        position = params.get('position')
        new_name = params.get('newName')
        if (
            document is None or not isinstance(position, dict)
            or not isinstance(new_name, str) or not valid_identifier(new_name)
        ):
            raise ValueError('Rename requires a valid non-keyword Jack identifier.')
        semantic = self._semantic_symbol_at(document, position)
        if semantic is None or not semantic[0].renameable:
            raise ValueError('This Jack symbol cannot be renamed.')
        symbol, _ = semantic
        if not self._model_is_current_for(symbol.id):
            raise ValueError('The semantic project model is stale; wait for analysis and retry.')
        if self._rename_collides(symbol, new_name):
            raise ValueError(f'Renaming "{symbol.name}" to "{new_name}" would conflict with an existing symbol.')
        assert self.semantic_model is not None
        grouped: dict[Path, list[JsonValue]] = {}
        for item in self.semantic_model.occurrences:
            if item.symbol_id == symbol.id:
                grouped.setdefault(item.path, []).append({
                    'range': range_from_span(item.span), 'newText': new_name,
                })
        changes = []
        for path, edits in sorted(grouped.items(), key=lambda item: str(item[0])):
            uri = uri_from_path(path)
            open_document = self.documents.get(uri)
            changes.append({
                'textDocument': {
                    'uri': uri,
                    'version': open_document.version if open_document is not None else None,
                },
                'edits': sorted(
                    edits,
                    key=lambda edit: (
                        edit['range']['start']['line'], edit['range']['start']['character']
                    ),
                    reverse=True,
                ),
            })
        return {'documentChanges': changes}

    def _semantic_symbol_at(
        self, document: Document, position: JsonValue
    ) -> tuple[SemanticSymbol, object] | None:
        model = self.semantic_model
        path = path_from_uri(document.uri)
        line = position.get('line')
        character = position.get('character')
        if (
            model is None or path is None
            or not isinstance(line, int) or not isinstance(character, int)
        ):
            return None
        occurrence = model.occurrence_at(
            path, offset_at_position(document.text, line, character)
        )
        if occurrence is None:
            return None
        symbol = model.symbols.get(occurrence.symbol_id)
        return None if symbol is None else (symbol, occurrence)

    def _type_symbol(self, resolved_type: str | None) -> SemanticSymbol | None:
        if self.semantic_model is None or resolved_type is None:
            return None
        for symbol in self.semantic_model.symbols.values():
            if (
                symbol.kind in {'type', 'view'}
                and symbol.resolved_type == resolved_type
            ):
                return symbol
        source_name = resolved_type.split('$')[-1]
        return next((
            symbol for symbol in self.semantic_model.symbols.values()
            if symbol.kind in {'type', 'view'} and symbol.name == source_name
        ), None)

    @staticmethod
    def _import_alias_module(source: str, alias: str) -> str | None:
        match = re.search(
            rf'\bimport\s+([A-Za-z_][A-Za-z0-9_.]*)\s+as\s+{re.escape(alias)}\s*;',
            source,
        )
        return match.group(1) if match is not None else None

    def _model_is_current_for(self, symbol_id: str) -> bool:
        if self.semantic_model is None:
            return False
        affected = {
            item.path for item in self.semantic_model.occurrences
            if item.symbol_id == symbol_id
        }
        for path in affected:
            document = self.documents.get(uri_from_path(path))
            if document is None:
                continue
            if self.semantic_model.versions.get(path) != document.version:
                return False
        return True

    def _rename_collides(self, symbol: SemanticSymbol, new_name: str) -> bool:
        assert self.semantic_model is not None
        for other in self.semantic_model.symbols.values():
            if other.id == symbol.id or other.name != new_name:
                continue
            if symbol.container_id is not None:
                if other.container_id == symbol.container_id:
                    return True
            elif symbol.kind in {'variable', 'parameter', 'catch'}:
                if other.path == symbol.path and other.kind in {
                    'variable', 'parameter', 'catch'
                }:
                    return True
            elif other.container_id is None and other.module_name == symbol.module_name:
                return True
        return False

    def _configure_project(self, params: JsonValue) -> None:
        roots: list[Path] = []
        workspace_folders = params.get('workspaceFolders')
        if isinstance(workspace_folders, list):
            for folder in workspace_folders:
                if isinstance(folder, dict) and isinstance(folder.get('uri'), str):
                    path = path_from_uri(folder['uri'])
                    if path is not None:
                        roots.append(path)
        root_uri = params.get('rootUri')
        if not roots and isinstance(root_uri, str):
            path = path_from_uri(root_uri)
            if path is not None:
                roots.append(path)
        options = params.get('initializationOptions')
        if not isinstance(options, dict):
            options = {}
        module_roots = options.get('moduleRoots')
        self.module_roots = [
            Path(value).resolve() for value in module_roots or []
            if isinstance(value, str)
        ] if isinstance(module_roots, list) else []
        stubs = options.get('stubs')
        self.import_overrides = {
            str(name): str(value) for name, value in stubs.items()
        } if isinstance(stubs, dict) else {}
        delay = options.get('analysisDelay')
        self.analysis_delay = max(float(delay) / 1000, 0) if isinstance(delay, (int, float)) else 0.2
        self.workspace_roots = roots
        self.analyzer = ProjectAnalyzer(
            roots,
            module_roots=self.module_roots,
            import_overrides=self.import_overrides,
        ) if roots else None

    def _schedule_analysis(
        self,
        uri: str | None,
        *,
        full_comptime: bool = False,
        delay: float | None = None,
    ) -> None:
        if uri is None or self.analyzer is None:
            return
        focus = path_from_uri(uri)
        if focus is None:
            return
        self.analysis_generation += 1
        generation = self.analysis_generation
        if self.analysis_timer is not None:
            self.analysis_timer.cancel()
        wait = self.analysis_delay if delay is None else delay
        self.analysis_timer = threading.Timer(
            wait,
            self._submit_analysis,
            args=(generation, focus, full_comptime),
        )
        self.analysis_timer.daemon = True
        self.analysis_timer.start()

    def _submit_analysis(
        self, generation: int, focus: Path, full_comptime: bool
    ) -> None:
        if self.analyzer is None or self.shutting_down:
            return
        if self.analysis_executor is None:
            self.analysis_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix='jack-lsp-analysis'
            )
        overlays = {}
        versions = {}
        for uri, document in list(self.documents.items()):
            path = path_from_uri(uri)
            if path is not None:
                overlays[path] = document.text
                versions[path] = document.version
        future = self.analysis_executor.submit(
            self.analyzer.analyze,
            focus,
            overlays=overlays,
            versions=versions,
            full_comptime=full_comptime,
        )
        future.add_done_callback(
            lambda completed: self._analysis_finished(generation, focus, completed)
        )

    def _analysis_finished(self, generation: int, focus: Path, future) -> None:
        if generation != self.analysis_generation or self.shutting_down:
            return
        try:
            result: ProjectAnalysis = future.result()
        except Exception as err:  # pragma: no cover - defensive worker boundary
            self._log(f'Project analysis failed: {type(err).__name__}: {err}', 1)
            return
        if not result.deferred_comptime or self.semantic_model is None:
            self.semantic_model = result.model
        diagnostics_by_uri: dict[str, list[JsonValue]] = {}
        for diagnostic in result.diagnostics:
            span = diagnostic.span
            path = (
                Path(span.source_path)
                if span is not None and span.source_path is not None
                else focus
            )
            diagnostics_by_uri.setdefault(uri_from_path(path), []).append({
                'range': range_from_span(span) if span is not None else _zero_range(),
                'severity': diagnostic.severity,
                'source': 'jack',
                'message': diagnostic.message,
            })
        current_uris = set(diagnostics_by_uri)
        for uri in sorted(self.published_semantic_uris | current_uris):
            document = self.documents.get(uri)
            self._send_notification(
                'textDocument/publishDiagnostics',
                {
                    'uri': uri,
                    'version': document.version if document is not None else None,
                    'diagnostics': diagnostics_by_uri.get(uri, []),
                },
            )
        self.published_semantic_uris = current_uris

    def _did_change_watched_files(self, params: JsonValue) -> None:
        changes = params.get('changes')
        if not isinstance(changes, list):
            return
        uri = self._first_document_uri()
        if uri is None:
            for change in changes:
                if isinstance(change, dict) and isinstance(change.get('uri'), str):
                    uri = change['uri']
                    break
        self._schedule_analysis(uri, delay=0)

    def _first_document_uri(self) -> str | None:
        return next(iter(self.documents), None)

    def _document_from_position_request(self, params: JsonValue) -> Document | None:
        text_document = params.get('textDocument')
        if not isinstance(text_document, dict):
            return None
        uri = text_document.get('uri')
        if not isinstance(uri, str):
            return None
        return self.documents.get(uri)

    def _publish_diagnostics(self, uri: str) -> None:
        document = self.documents.get(uri)
        if document is None:
            return
        self._send_notification(
            'textDocument/publishDiagnostics',
            {
                'uri': uri,
                'version': document.version,
                'diagnostics': diagnostics_for_source(document.text),
            },
        )

    def _read_message(self) -> JsonValue | None:
        headers: dict[str, str] = {}
        while True:
            line = self.stdin.readline()
            if line == b'':
                return None
            if line in {b'\r\n', b'\n'}:
                break
            name, _, value = line.decode('ascii').partition(':')
            headers[name.lower()] = value.strip()

        length_value = headers.get('content-length')
        if length_value is None:
            self._log('Received LSP message without Content-Length.', message_type=1)
            return None

        body = self.stdin.read(int(length_value))
        if body == b'':
            return None
        return json.loads(body.decode('utf-8'))

    def _respond(self, request_id: object, result: object) -> None:
        self._write_message({'jsonrpc': '2.0', 'id': request_id, 'result': result})

    def _respond_error(self, request_id: object, code: int, message: str) -> None:
        self._write_message(
            {
                'jsonrpc': '2.0',
                'id': request_id,
                'error': {'code': code, 'message': message},
            }
        )

    def _send_notification(self, method: str, params: object) -> None:
        self._write_message({'jsonrpc': '2.0', 'method': method, 'params': params})

    def _log(self, message: str, message_type: int = 3) -> None:
        self._send_notification(
            'window/logMessage',
            {
                'type': message_type,
                'message': message,
            },
        )
        print(message, file=self.stderr)

    def _write_message(self, message: JsonValue) -> None:
        body = json.dumps(message, separators=(',', ':')).encode('utf-8')
        header = f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii')
        with self.write_lock:
            self.stdout.write(header)
            self.stdout.write(body)
            self.stdout.flush()


def _completion_kind(symbol: SemanticSymbol) -> int:
    return {
        'method': 2,
        'function': 3,
        'field': 5,
        'global': 6,
        'variable': 6,
        'parameter': 6,
        'catch': 6,
        'type': 7,
        'view': 8,
        'module': 9,
    }.get(symbol.kind, 1)


def main() -> int:
    LanguageServer(sys.stdin.buffer, sys.stdout.buffer, sys.stderr).run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
