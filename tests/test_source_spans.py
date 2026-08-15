import unittest
import tempfile
from pathlib import Path

from jack.ast_nodes import SourceSpan, TypeReference
from jack.cleanup_lowering_pass import lower_hir_static_cleanups
from jack.hir_lowering_pass import compile_to_hir
from jack.hir_nodes import HIRFunctionDeclaration
from jack.parser import Lexer, ParseError, parse


class SourceSpanTests(unittest.TestCase):
    def test_lexer_tokens_keep_offsets_and_exclusive_end_columns(self):
        tokens = Lexer('i32 value = 12;').tokenize()

        self.assertEqual('IDENT', tokens[0].kind)
        self.assertEqual(SourceSpan(1, 1, 1, 4, 0, 3), tokens[0].span)
        self.assertEqual('INT', tokens[3].kind)
        self.assertEqual(SourceSpan(1, 13, 1, 15, 12, 14), tokens[3].span)

    def test_parser_attaches_spans_to_declarations_types_and_expressions(self):
        ast = parse('i32 value = 12;')
        declaration = ast[0]

        self.assertEqual(SourceSpan(1, 1, 1, 16, 0, 15), declaration.span)
        self.assertEqual(SourceSpan(1, 1, 1, 4, 0, 3), declaration.type.span)
        self.assertEqual(SourceSpan(1, 13, 1, 15, 12, 14), declaration.expr.span)

    def test_spans_do_not_participate_in_ast_equality(self):
        parsed_type = parse('i32 value;')[0].type

        self.assertEqual(TypeReference('i32'), parsed_type)

    def test_parse_error_carries_a_structured_span(self):
        with self.assertRaises(ParseError) as caught:
            parse('i32 value = ;')

        self.assertEqual(SourceSpan(1, 13, 1, 14, 12, 13), caught.exception.span)

    def test_parser_attaches_canonical_source_path_when_provided(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'module.jack'
            ast = parse('i32 value = 12;', source_path=source_path)

        self.assertEqual(str(source_path.resolve()), ast[0].span.source_path)
        self.assertEqual(str(source_path.resolve()), ast[0].expr.span.source_path)

    def test_source_path_does_not_participate_in_span_equality(self):
        self.assertEqual(
            SourceSpan(1, 1, 1, 2, 0, 1),
            SourceSpan(1, 1, 1, 2, 0, 1, '/tmp/example.jack'),
        )

    def test_specialization_and_cleanup_preserve_source_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'program.jack'
            source = '''
                struct Tracer {
                    deinit(&inout self) {
                    }
                }

                i32 add(comptime i32 offset, i32 value) {
                    return offset + value;
                }

                void run() {
                    Tracer tracer;
                    i32 value = add(6, 2);
                    print(value);
                }
                run();
            '''
            source_path.write_text(source)
            program = compile_to_hir(
                parse(source, source_path=source_path), print_handler=None
            )
            lowered = lower_hir_static_cleanups(program)

        functions = [
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        ]
        specialized = next(
            declaration for declaration in functions
            if declaration.name.startswith('add')
        )
        run = next(declaration for declaration in functions if declaration.name == 'run')
        cleanup = run.body[-1]
        self.assertEqual(str(source_path.resolve()), specialized.span.source_path)
        self.assertEqual(str(source_path.resolve()), specialized.body[0].span.source_path)
        self.assertEqual(str(source_path.resolve()), cleanup.span.source_path)


if __name__ == '__main__':
    unittest.main()
