import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.ast_nodes import (
    Assignment,
    CompositeExpression,
    Declaration,
    ExpressionStatement,
    FunctionCall,
    FunctionDefinition,
    Literal,
    Print,
    Return,
    Variable,
)
from jack.parser import ParseError, parse, parse_sources


class ParserTest(unittest.TestCase):
    def test_parse_main_language_sample(self):
        ast = parse_sources([os.path.join(os.path.dirname(__file__), "main.lang")])

        # main.lang contains declarations for `a`, `b`, and `c`, plus two prints
        self.assertEqual(len(ast), 9)
        self.assertEqual(ast[0], Declaration(False, Variable("a"), "i32"))
        self.assertEqual(ast[1], Assignment(False, Variable("a"), Literal("i32", "23")))

        self.assertIsInstance(ast[2], FunctionDefinition)
        self.assertEqual(ast[2].return_type, "i32")
        self.assertEqual(ast[2].name, "add")
        self.assertTrue(ast[2].parameters[0].comptime)
        self.assertEqual(ast[2].parameters[0].name, "left")
        self.assertEqual(ast[2].parameters[1].name, "right")
        self.assertIsInstance(ast[2].body[0], Return)
        self.assertIsInstance(ast[2].body[0].expression, CompositeExpression)

        self.assertEqual(ast[3], Declaration(False, Variable("b"), "i32"))
        self.assertIsInstance(ast[4], Assignment)
        self.assertIsInstance(ast[4].value, FunctionCall)
        self.assertEqual(ast[4].value.name, "add")

        self.assertEqual(ast[5], Declaration(False, Variable("c"), "i32"))
        self.assertIsInstance(ast[6], Assignment)
        self.assertIsInstance(ast[6].value, FunctionCall)
        self.assertEqual(ast[6].value.name, "add")

        self.assertIsInstance(ast[7], Print)
        self.assertIsInstance(ast[8], Print)

    def test_parse_precedence_and_print(self):
        ast = parse("print(1 + 2 * 3);")

        self.assertEqual(len(ast), 1)
        self.assertIsInstance(ast[0], Print)
        self.assertIsInstance(ast[0].expression, CompositeExpression)
        self.assertEqual(ast[0].expression.operator, "+")
        self.assertIsInstance(ast[0].expression.second_operand, CompositeExpression)
        self.assertEqual(ast[0].expression.second_operand.operator, "*")

    def test_parse_function_call_expression_statement(self):
        ast = parse("foo(1);")

        self.assertEqual(len(ast), 1)
        self.assertIsInstance(ast[0], ExpressionStatement)
        self.assertIsInstance(ast[0].expression, FunctionCall)
        self.assertEqual(ast[0].expression.name, "foo")

    def test_top_level_return_is_rejected(self):
        with self.assertRaises(ParseError):
            parse("return 1;")


if __name__ == "__main__":
    unittest.main()
