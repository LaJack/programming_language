import json
import unittest

from jack.ast_nodes import SourceSpan
from jack.lsp_server import (
    Document,
    LanguageServer,
    diagnostics_for_source,
    definition_for_source,
    document_symbols_for_source,
    hover_for_source,
    range_from_error,
    range_from_span,
)


class LspDiagnosticsTests(unittest.TestCase):
    def test_valid_source_has_no_parse_diagnostics(self):
        self.assertEqual([], diagnostics_for_source('i32 value = 1;'))

    def test_parse_error_is_reported_as_lsp_diagnostic(self):
        diagnostics = diagnostics_for_source('i32 value = ;')

        self.assertEqual(1, len(diagnostics))
        diagnostic = diagnostics[0]
        self.assertEqual(1, diagnostic['severity'])
        self.assertEqual('jack', diagnostic['source'])
        self.assertIn('Expected expression.', diagnostic['message'])
        self.assertEqual({'line': 0, 'character': 12}, diagnostic['range']['start'])

    def test_multiple_parse_errors_are_reported_in_source_order(self):
        diagnostics = diagnostics_for_source('i32 first = ;\ni32 second = ;')

        self.assertEqual(2, len(diagnostics))
        self.assertEqual([0, 1], [item['range']['start']['line'] for item in diagnostics])

    def test_lsp_range_uses_structured_source_span(self):
        self.assertEqual(
            {
                'start': {'line': 1, 'character': 2},
                'end': {'line': 1, 'character': 5},
            },
            range_from_span(SourceSpan(2, 3, 2, 6, 10, 13)),
        )

    def test_error_range_falls_back_to_start_of_file_without_position(self):
        self.assertEqual(
            {
                'start': {'line': 0, 'character': 0},
                'end': {'line': 0, 'character': 1},
            },
            range_from_error('Unterminated block comment.', '/*'),
        )


class LspDocumentSymbolTests(unittest.TestCase):
    def test_document_symbols_include_top_level_and_struct_children(self):
        symbols = document_symbols_for_source('''
            module app.main;

            struct Driver {
                i32 id;

                init(&inout self, i32 id) {
                    self.id = id;
                }

                void reset(&inout self) {
                    self.id = 0;
                }
            }

            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 global_value = 1;
        ''')

        self.assertEqual(['app.main', 'Driver', 'add', 'global_value'], [symbol['name'] for symbol in symbols])
        self.assertEqual(2, symbols[0]['kind'])
        self.assertEqual(23, symbols[1]['kind'])
        self.assertEqual(12, symbols[2]['kind'])
        self.assertEqual(13, symbols[3]['kind'])
        self.assertEqual(['id', 'init', 'reset'], [symbol['name'] for symbol in symbols[1]['children']])
        self.assertEqual([8, 9, 6], [symbol['kind'] for symbol in symbols[1]['children']])
        self.assertEqual('i32', symbols[1]['children'][0]['detail'])
        self.assertEqual('void', symbols[1]['children'][2]['detail'])

    def test_document_symbols_retain_recovered_declarations(self):
        self.assertEqual(
            ['value', 'later'],
            [
                symbol['name']
                for symbol in document_symbols_for_source(
                    'i32 value = ; i32 later = 2;'
                )
            ],
        )


class LspHoverAndDefinitionTests(unittest.TestCase):
    SOURCE = '''
        struct Driver {
            i32 id;

            void reset(&inout self, i32 id) {
                self.id = id;
            }
        }

        i32 add(i32 left, i32 right) {
            return left + right;
        }

        Driver driver;
        i32 total = add(1, 2);
    '''

    def test_hover_reports_structs_functions_variables_and_fields(self):
        driver_type_hover = hover_for_source(self.SOURCE, {'line': 13, 'character': 9})
        add_hover = hover_for_source(self.SOURCE, {'line': 14, 'character': 20})
        field_hover = hover_for_source(self.SOURCE, {'line': 5, 'character': 21})

        self.assertIn('struct Driver', driver_type_hover['contents']['value'])
        self.assertIn('i32 add(i32 left, i32 right)', add_hover['contents']['value'])
        self.assertIn('field i32 id', field_hover['contents']['value'])

    def test_hover_reports_builtin_types(self):
        hover = hover_for_source('i32 value = 1;', {'line': 0, 'character': 1})

        self.assertIn('builtin type i32', hover['contents']['value'])

    def test_hover_is_empty_outside_identifiers(self):
        self.assertIsNone(hover_for_source('i32 value = 1;', {'line': 0, 'character': 10}))

    def test_definition_reports_symbol_location(self):
        locations = definition_for_source(
            self.SOURCE,
            'file:///sample.jack',
            {'line': 14, 'character': 20},
        )

        self.assertEqual(1, len(locations))
        self.assertEqual('file:///sample.jack', locations[0]['uri'])
        self.assertEqual({'line': 9, 'character': 12}, locations[0]['range']['start'])

    def test_definition_prefers_field_for_member_access(self):
        locations = definition_for_source(
            self.SOURCE,
            'file:///sample.jack',
            {'line': 5, 'character': 21},
        )

        self.assertEqual(1, len(locations))
        self.assertEqual({'line': 2, 'character': 16}, locations[0]['range']['start'])

    def test_definition_uses_declaration_name_for_variable_span(self):
        locations = definition_for_source(
            'i32 value = value;',
            'file:///sample.jack',
            {'line': 0, 'character': 13},
        )

        self.assertEqual(1, len(locations))
        self.assertEqual({'line': 0, 'character': 4}, locations[0]['range']['start'])

    def test_explicit_method_self_is_hoverable_and_definable(self):
        source = (
            'struct Counter {\n'
            '    i32 value;\n'
            '\n'
            '    i32 get(&in self) {\n'
            '        return self.value;\n'
            '    }\n'
            '}\n'
        )

        hover = hover_for_source(source, {'line': 4, 'character': 16})
        locations = definition_for_source(
            source,
            'file:///counter.jack',
            {'line': 4, 'character': 16},
        )

        self.assertIn('parameter &in Counter self', hover['contents']['value'])
        self.assertEqual(1, len(locations))
        self.assertEqual({'line': 3, 'character': 16}, locations[0]['range']['start'])

    def test_definition_is_empty_for_builtins_and_unknown_names(self):
        self.assertEqual([], definition_for_source('i32 value = 1;', 'file:///sample.jack', {'line': 0, 'character': 1}))
        self.assertEqual([], definition_for_source('value = 1;', 'file:///sample.jack', {'line': 0, 'character': 1}))


class LspProtocolTests(unittest.TestCase):
    def test_initialize_response_advertises_full_document_sync(self):
        server = LanguageServer(None, None, None)
        result = server._initialize_result({})

        self.assertEqual('jack-lsp', result['serverInfo']['name'])
        self.assertEqual(
            {'openClose': True, 'change': 1, 'save': {'includeText': True}},
            result['capabilities']['textDocumentSync'],
        )
        self.assertTrue(result['capabilities']['documentSymbolProvider'])
        self.assertTrue(result['capabilities']['hoverProvider'])
        self.assertTrue(result['capabilities']['definitionProvider'])
        self.assertEqual(['.'], result['capabilities']['completionProvider']['triggerCharacters'])
        self.assertTrue(result['capabilities']['referencesProvider'])
        self.assertTrue(result['capabilities']['renameProvider']['prepareProvider'])
        self.assertTrue(result['capabilities']['semanticTokensProvider']['full'])
        self.assertEqual(['(', ','], result['capabilities']['signatureHelpProvider']['triggerCharacters'])
        self.assertTrue(result['capabilities']['codeActionProvider'])

    def test_hover_and_definition_requests_use_open_document_text(self):
        server = LanguageServer(None, None, None)
        server.documents['file:///sample.jack'] = Document(
            'file:///sample.jack',
            'i32 add(i32 left, i32 right) { return left + right; } i32 total = add(1, 2);',
            1,
        )

        hover = server._hover({
            'textDocument': {'uri': 'file:///sample.jack'},
            'position': {'line': 0, 'character': 67},
        })
        definition = server._definition({
            'textDocument': {'uri': 'file:///sample.jack'},
            'position': {'line': 0, 'character': 67},
        })

        self.assertIn('i32 add(i32 left, i32 right)', hover['contents']['value'])
        self.assertEqual({'line': 0, 'character': 4}, definition[0]['range']['start'])

    def test_document_symbol_request_uses_open_document_text(self):
        server = LanguageServer(None, None, None)
        server.documents['file:///driver.jack'] = Document(
            'file:///driver.jack',
            'struct Driver { i32 id; }',
            1,
        )

        symbols = server._document_symbols({
            'textDocument': {'uri': 'file:///driver.jack'},
        })

        self.assertEqual(['Driver'], [symbol['name'] for symbol in symbols])
        self.assertEqual(['id'], [symbol['name'] for symbol in symbols[0]['children']])

    def test_write_message_uses_lsp_content_length_header(self):
        class Output:
            def __init__(self):
                self.data = bytearray()

            def write(self, value):
                self.data.extend(value)

            def flush(self):
                pass

        output = Output()
        server = LanguageServer(None, output, None)
        server._write_message({'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}})

        raw = bytes(output.data)
        header, body = raw.split(b'\r\n\r\n', 1)
        length = int(header.decode('ascii').split(':', 1)[1].strip())
        self.assertEqual(length, len(body))
        self.assertEqual({'jsonrpc': '2.0', 'id': 1, 'result': {'ok': True}}, json.loads(body))


if __name__ == '__main__':
    unittest.main()
