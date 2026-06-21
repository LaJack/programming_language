import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.compiler import compile_sources_to_executable, compile_sources_to_llvm_ir
from jack.ir import IRAssignment, IRCall, IRGlobal
from jack.lowering import lower
from jack.parser import parse


class CompilerTest(unittest.TestCase):
    def test_lowering_produces_typed_ir(self):
        module = lower(
            parse(
                """
                i32 value 4;
                i32 add(i32 left, i32 right) {
                    return left + right;
                }
                value = add(value, 6);
                """
            )
        )

        self.assertEqual(module.globals, [IRGlobal("value", "i32", module.globals[0].initializer)])
        self.assertEqual(module.functions[0].name, "add")
        self.assertEqual(module.functions[-1].name, "main")
        self.assertIsInstance(module.functions[-1].body[0], IRAssignment)
        self.assertIsInstance(module.functions[-1].body[1].value, IRCall)

    def test_compile_sources_to_llvm_ir_emits_functions_globals_and_main(self):
        source = os.path.join(os.path.dirname(__file__), "main.lang")

        llvm_ir = compile_sources_to_llvm_ir([source])

        self.assertIn("declare i32 @printf(i8*, ...)", llvm_ir)
        self.assertIn("@a = global i32 0", llvm_ir)
        # comptime specialization creates specialized versions of `add`
        self.assertIn("define i32 @add__i32_6(i32 %right)", llvm_ir)
        self.assertIn("define i32 @add__i32_7(i32 %right)", llvm_ir)
        self.assertIn("define i32 @main()", llvm_ir)
        self.assertIn("call i32 @add__i32_6(i32", llvm_ir)
        self.assertIn("call i32 @add__i32_7(i32", llvm_ir)
        self.assertIn("@printf", llvm_ir)

    @unittest.skipUnless(shutil.which("clang"), "clang is required")
    def test_compile_sources_to_executable(self):
        source = os.path.join(os.path.dirname(__file__), "main.lang")

        with tempfile.TemporaryDirectory() as temp_dir:
            executable = os.path.join(temp_dir, "main")
            compile_sources_to_executable([source], executable)

            result = subprocess.run(
                [executable],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0)
        # main.lang prints two values after specialization
        self.assertEqual(result.stdout.strip(), "29\n30")


if __name__ == "__main__":
    unittest.main()
