import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.interpreter import interpret
from jack.parser import parse


class InterpreterTest(unittest.TestCase):
    def test_function_can_read_global_variable(self):
        ast = parse(
            """
            i32 global 4;
            i32 add_global(i32 value) {
                return value + global;
            }
            print(add_global(6));
            """
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            interpret(ast)

        self.assertEqual(output.getvalue().strip(), "10")

    def test_function_call_expression_statement_runs_side_effects(self):
        ast = parse(
            """
            i32 emit() {
                print(1);
                return 0;
            }
            emit();
            """
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            interpret(ast)

        self.assertEqual(output.getvalue().strip(), "1")


if __name__ == "__main__":
    unittest.main()
