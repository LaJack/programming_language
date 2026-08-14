import unittest

from jack.ast_nodes import SourceSpan, ViewDeclaration
from jack.c_emit_pass import emit_c
from jack.interpreter import Interpreter
from jack.jack_emit_pass import emit_jack
from jack.lsp_server import definition_for_source, document_symbols_for_source, hover_for_source
from jack.parser import ParseError, parse
from jack.semantic_pass import SemanticError, validate_runtime_ast


class ViewDeclarationTests(unittest.TestCase):
    SOURCE = '\n        pub view DecodeView {\n            in u8[] src;\n            inout usize cursor;\n            out Packet packet;\n        }\n    '

    def test_parser_reads_view_fields_with_capabilities(self):
        ast = parse(self.SOURCE)

        view = ast[0]
        self.assertEqual(ViewDeclaration, type(view))
        self.assertTrue(view.public)
        self.assertEqual('DecodeView', view.name)
        self.assertEqual(['src', 'cursor', 'packet'], [field.name for field in view.fields])
        self.assertEqual(['in', 'inout', 'out'], [field.mode for field in view.fields])
        self.assertEqual('u8', view.fields[0].type.name)
        self.assertTrue(view.fields[0].type.is_slice)
        self.assertEqual('usize', view.fields[1].type.name)
        self.assertEqual('Packet', view.fields[2].type.name)

    def test_parser_attaches_view_spans(self):
        view = parse('view A { in i32 a; };')[0]

        self.assertEqual(SourceSpan(1, 1, 1, 22, 0, 21), view.span)
        self.assertEqual(SourceSpan(1, 10, 1, 19, 9, 18), view.fields[0].span)
        self.assertEqual(SourceSpan(1, 13, 1, 16, 12, 15), view.fields[0].type.span)

    def test_parser_rejects_comptime_and_extern_views(self):
        with self.assertRaisesRegex(ParseError, 'comptime cannot mark view declarations'):
            parse('comptime view A { in i32 a; }')
        with self.assertRaisesRegex(ParseError, 'extern can only mark type, variable, or function declarations'):
            parse('extern view A { in i32 a; }')

    def test_semantic_validation_rejects_duplicate_view_fields(self):
        with self.assertRaisesRegex(SemanticError, 'duplicate field "a"'):
            validate_runtime_ast(parse('view A { in i32 a; out i32 a; }'))

    def test_interpreter_ignores_view_declarations_at_runtime(self):
        Interpreter().eval_source_ast(parse('view A { in i32 a; } i32 value = 1;'))

    def test_jack_emit_round_trips_view_declaration(self):
        source = 'view A { in i32 a; out u8[] bytes; }'

        self.assertEqual(
            'view A {\n    in i32 a;\n    out u8[] bytes;\n}\n',
            emit_jack(parse(source)),
        )

    def test_c_emit_ignores_unused_view_declarations(self):
        c_source = emit_c(parse('view A { in i32 a; } i32 value = 1;'))

        self.assertIn('int32_t value;', c_source)
        self.assertNotIn('view A', c_source)

    def test_semantic_validation_allows_view_borrow_field_access(self):
        ast = validate_runtime_ast(parse('''
            struct Packet {
                i32 header;
                i32 checksum;
            }

            view PacketView {
                in i32 header;
                out i32 checksum;
            }

            void update(&inout PacketView packet) {
                i32 header = packet.header;
                packet.checksum = header;
            }
        '''))

        self.assertTrue(ast)

    def test_semantic_validation_allows_local_view_borrow_field_access(self):
        ast = validate_runtime_ast(parse('''
            struct Packet {
                i32 header;
                i32 checksum;
            }

            view PacketView {
                in i32 header;
                out i32 checksum;
            }

            Packet packet;
            &inout PacketView packet_view = &inout packet;
            i32 header = packet_view.header;
            packet_view.checksum = header;
        '''))

        self.assertTrue(ast)

    def test_semantic_validation_rejects_reading_out_view_field(self):
        with self.assertRaisesRegex(SemanticError, 'through &out view field'):
            validate_runtime_ast(parse('''
                struct Packet {
                    i32 checksum;
                }

                view PacketView {
                    out i32 checksum;
                }

                void update(&inout PacketView packet) {
                    i32 checksum = packet.checksum;
                }
            '''))

    def test_semantic_validation_rejects_writing_in_view_field(self):
        with self.assertRaisesRegex(SemanticError, 'through &in view field'):
            validate_runtime_ast(parse('''
                struct Packet {
                    i32 header;
                }

                view PacketView {
                    in i32 header;
                }

                void update(&inout PacketView packet) {
                    packet.header = 1;
                }
            '''))

    def test_interpreter_uses_view_borrow_fields(self):
        interpreter = Interpreter()
        interpreter.eval_source_ast(parse('''
            struct Packet {
                i32 header;
                i32 checksum;
            }

            view PacketView {
                in i32 header;
                out i32 checksum;
            }

            void update(&inout PacketView packet) {
                i32 header = packet.header;
                packet.checksum = header + 1;
            }

            Packet packet;
            packet.header = 41;
            update(&inout packet);
        '''))

        self.assertEqual(42, interpreter.global_scope.get('packet.checksum'))

    def test_c_emit_uses_view_descriptor_values(self):
        c_source = emit_c(parse('''
            struct Packet {
                i32 header;
                i32 checksum;
            }

            view PacketView {
                in i32 header;
                out i32 checksum;
            }

            void update(&inout PacketView packet) {
                i32 header = packet.header;
                packet.checksum = header;
            }

            Packet packet;
            update(&inout packet);
        '''))

        self.assertIn('typedef struct PacketView {', c_source)
        self.assertIn('const int32_t *header;', c_source)
        self.assertIn('int32_t *checksum;', c_source)
        self.assertIn('void update(PacketView packet)', c_source)
        self.assertIn('int32_t header = (*packet.header);', c_source)
        self.assertIn('(*packet.checksum) = header;', c_source)
        self.assertIn('update((PacketView){.header = &packet.header, .checksum = &packet.checksum});', c_source)

    def test_lsp_reports_view_symbols_hover_and_definition(self):
        source = 'view A { in i32 a; out u8[] bytes; }'

        symbols = document_symbols_for_source(source)
        hover = hover_for_source(source, {'line': 0, 'character': 5})
        locations = definition_for_source(source, 'file:///view.jack', {'line': 0, 'character': 16})

        self.assertEqual(['A'], [symbol['name'] for symbol in symbols])
        self.assertEqual(11, symbols[0]['kind'])
        self.assertEqual(['a', 'bytes'], [symbol['name'] for symbol in symbols[0]['children']])
        self.assertEqual('in i32', symbols[0]['children'][0]['detail'])
        self.assertIn('view A', hover['contents']['value'])
        self.assertEqual({'line': 0, 'character': 16}, locations[0]['range']['start'])


if __name__ == '__main__':
    unittest.main()
