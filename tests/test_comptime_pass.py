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
    FunctionDefinition,
    FunctionCall,
    If,
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

    def test_function_specialization_creates_specialized_definition(self):
        ast = parse(
            """
            i32 add(comptime i32 left, i32 right) {
                return left + right;
            }
            i32 x;
            x = add(6, 7);
            """
        )

        new_ast = ComptimePass().run(ast)

        specialized_name = "add#i32_6"

        specialized_def = None
        for stmt in new_ast:
            if isinstance(stmt, FunctionDefinition) and stmt.name == specialized_name:
                specialized_def = stmt
                break

        self.assertIsNotNone(specialized_def)
        self.assertEqual(len(specialized_def.parameters), 1)
        self.assertEqual(specialized_def.parameters[0].name, "right")

        # Ensure the assignment uses the specialized function
        last_assignment = None
        for stmt in reversed(new_ast):
            if isinstance(stmt, Assignment) and stmt.variable.name == "x":
                last_assignment = stmt
                break

        self.assertIsNotNone(last_assignment)
        self.assertIsInstance(last_assignment.value, FunctionCall)
        self.assertEqual(last_assignment.value.name, specialized_name)

    def test_comptime_if_resolved_at_compile_time(self):
        ast = [
            Declaration(False, Variable("a"), "i32"),
            If(
                True,
                Literal("i32", "1"),
                [
                    Assignment(True, Variable("a"), Literal("i32", "42")),
                ],
                [],
                [
                    Assignment(False, Variable("a"), Literal("i32", "0")),
                ],
            ),
        ]

        new_ast = ComptimePass().run(ast)

        # Expect declaration + transformed assignment with literal 42
        self.assertEqual(len(new_ast), 2)
        self.assertIsInstance(new_ast[0], Declaration)
        self.assertIsInstance(new_ast[1], Assignment)
        self.assertIsInstance(new_ast[1].value, Literal)
        self.assertEqual(new_ast[1].value.value, "42")

if __name__ == "__main__":
    unittest.main()
