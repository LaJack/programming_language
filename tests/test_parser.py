import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.ast_nodes import (
    Assignment,
    CompositeExpression,
    Declaration,
    FunctionCall,
    FunctionDefinition,
    Literal,
    Print,
    Return,
    Variable,
)
from jack.parser import parse, parse_sources


class ParserTest(unittest.TestCase):
    def test_parse_main_language_sample(self):
        ast = parse_sources([os.path.join(os.path.dirname(__file__), "main.lang")])

        self.assertEqual(len(ast), 6)
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
        self.assertIsInstance(ast[5], Print)

    def test_parse_precedence_and_print(self):
        ast = parse("print(1 + 2 * 3);")

        self.assertEqual(len(ast), 1)
        self.assertIsInstance(ast[0], Print)
        self.assertIsInstance(ast[0].expression, CompositeExpression)
        self.assertEqual(ast[0].expression.operator, "+")
        self.assertIsInstance(ast[0].expression.second_operand, CompositeExpression)
        self.assertEqual(ast[0].expression.second_operand.operator, "*")


if __name__ == "__main__":
    unittest.main()
