import sys
import os
import unittest

# Ensure the source-layout package is importable when running this file directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.comptime_pass import ComptimePass
from jack.parser import parse
from jack.ast_nodes import (
    Declaration,
    Assignment,
    Print,
    Variable,
    Literal,
    CompositeExpression,
)


class ComptimePassTest(unittest.TestCase):
    def test_comptime_assignment_and_print(self):
        ast = [
            Declaration(False, Variable("a"), "i32"),
            Assignment(
                True,
                Variable("a"),
                CompositeExpression(
                    "+", Literal("i32", "2"), Literal("i32", "3")
                ),
            ),
            Print(True, Variable("a")),
        ]

        cp = ComptimePass()
        new_ast = cp.run(ast)

        self.assertEqual(len(new_ast), 3)
        self.assertIsInstance(new_ast[0], Declaration)

        self.assertIsInstance(new_ast[1], Assignment)
        self.assertIsInstance(new_ast[1].value, Literal)
        self.assertEqual(new_ast[1].value.type, "i32")
        self.assertEqual(new_ast[1].value.value, "5")

        self.assertIsInstance(new_ast[2], Print)
        self.assertIsInstance(new_ast[2].expression, Literal)
        self.assertEqual(new_ast[2].expression.value, "5")

    def test_comptime_assignment_can_call_parsed_function(self):
        ast = parse(
            """
            i32 add(comptime i32 left, i32 right) {
                return left + right;
            }
            i32 b;
            comptime b = add(6, 23);
            """
        )

        new_ast = ComptimePass().run(ast)

        self.assertIsInstance(new_ast[-1], Assignment)
        self.assertIsInstance(new_ast[-1].value, Literal)
        self.assertEqual(new_ast[-1].value.value, "29")


if __name__ == "__main__":
    unittest.main()
