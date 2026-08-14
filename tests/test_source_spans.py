import unittest

from jack.ast_nodes import SourceSpan, TypeReference
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


if __name__ == '__main__':
    unittest.main()
