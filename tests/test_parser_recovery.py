import tempfile
import unittest
from pathlib import Path

from jack.ast_nodes import (
    FunctionDeclaration,
    InvalidExpression,
    InvalidStatement,
    VariableDeclaration,
)
from jack.parser import ParseError, parse, parse_recovering
from jack.hir_lowering_pass import compile_to_hir


class RecoveringParserTests(unittest.TestCase):
    def test_strict_parse_remains_fail_fast(self):
        with self.assertRaisesRegex(ParseError, 'Expected expression'):
            parse('i32 bad = ; i32 later = 2;')

    def test_recovers_multiple_top_level_declarations(self):
        result = parse_recovering(
            'i32 first = ;\ni32 good = 2;\ni32 second = ;\ni32 later = 3;'
        )

        self.assertEqual(2, len(result.diagnostics))
        self.assertEqual(
            ['first', 'good', 'second', 'later'],
            [node.name for node in result.statements],
        )
        self.assertIsInstance(result.statements[0].expr, InvalidExpression)
        self.assertIsInstance(result.statements[2].expr, InvalidExpression)

    def test_recovers_later_statements_inside_function(self):
        result = parse_recovering('''
            i32 calculate() {
                i32 before = 1;
                nonsense;
                i32 after = 2;
                return after;
            }
        ''')

        function = result.statements[0]
        self.assertIsInstance(function, FunctionDeclaration)
        self.assertTrue(any(isinstance(node, InvalidStatement) for node in function.body))
        self.assertIn(
            'after',
            [node.name for node in function.body if isinstance(node, VariableDeclaration)],
        )

    def test_unterminated_string_recovers_at_next_line(self):
        result = parse_recovering('print("unfinished\ni32 later = 2;')

        self.assertIn('Unterminated string', result.diagnostics[0].message)
        self.assertEqual(
            ['later'],
            [node.name for node in result.statements if isinstance(node, VariableDeclaration)],
        )

    def test_unexpected_characters_are_skipped(self):
        result = parse_recovering('i32 first = 1; @ i32 later = 2;')

        self.assertEqual(1, len(result.diagnostics))
        self.assertEqual(['first', 'later'], [node.name for node in result.statements])

    def test_malformed_argument_list_recovers_at_comma(self):
        result = parse_recovering('consume(1, , 3); i32 later = 2;')

        self.assertEqual(1, len(result.diagnostics))
        self.assertEqual(3, len(result.statements[0].parameters))
        self.assertIsInstance(result.statements[0].parameters[1], InvalidExpression)
        self.assertEqual('later', result.statements[1].name)

    def test_valid_slice_is_not_diagnosed_during_speculative_type_parse(self):
        result = parse_recovering(
            'u8[4] source; fill(source[..]); '
            'copy(source[1..], source[..3]);'
        )

        self.assertEqual([], result.diagnostics)

    def test_recovers_later_parameters_and_struct_fields(self):
        result = parse_recovering('''
            struct Record(comptime type T, broken, comptime i32 Size) {
                i32 first;
                &wrong bad;
                i32 later;
            }
        ''')

        declaration = result.statements[0]
        self.assertGreaterEqual(len(result.diagnostics), 2)
        self.assertEqual(['T', 'Size'], [item.name for item in declaration.parameters])
        self.assertEqual(['first', 'later'], [item.name for item in declaration.fields])

    def test_eof_in_nested_block_is_diagnosed_without_looping(self):
        result = parse_recovering('void run() { if (true) { i32 value = 1;')

        self.assertTrue(result.diagnostics)
        self.assertTrue(any('Expected } after block' in item.message for item in result.diagnostics))

    def test_diagnostics_are_capped_and_terminal_notice_is_added(self):
        source = '\n'.join(f'i32 bad{index} = ;' for index in range(30))
        result = parse_recovering(source)

        self.assertEqual(21, len(result.diagnostics))
        self.assertIn('stopped after 20 diagnostics', result.diagnostics[-1].message)

    def test_diagnostics_preserve_canonical_source_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'broken.jack'
            result = parse_recovering('i32 bad = ;', source_path=source_path)

        self.assertEqual(str(source_path.resolve()), result.diagnostics[0].span.source_path)

    def test_valid_input_contains_no_invalid_nodes(self):
        result = parse_recovering('i32 value = 1;')

        self.assertEqual([], result.diagnostics)
        self.assertEqual(parse('i32 value = 1;'), result.statements)
        self.assertFalse(any(isinstance(node, InvalidStatement) for node in result.statements))

    def test_recovered_nodes_are_rejected_by_compiler_lowering(self):
        recovered = parse_recovering('i32 bad = ;')

        with self.assertRaises(Exception):
            compile_to_hir(recovered.statements)


if __name__ == '__main__':
    unittest.main()
