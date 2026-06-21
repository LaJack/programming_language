import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.cli import main


class CliTest(unittest.TestCase):
    def test_parse_error_is_reported_without_traceback(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jack") as source:
            source.write("return 1;")
            source.flush()

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main(["-i", source.name])

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("jack:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_emit_llvm_writes_ir_to_stdout(self):
        source = os.path.join(os.path.dirname(__file__), "main.lang")
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            result = main(["--emit-llvm", source])

        self.assertEqual(result, 0)
        self.assertIn("define i32 @main()", stdout.getvalue())

    @unittest.skipUnless(shutil.which("clang"), "clang is required")
    def test_compile_writes_executable(self):
        source = os.path.join(os.path.dirname(__file__), "main.lang")

        with tempfile.TemporaryDirectory() as temp_dir:
            executable = os.path.join(temp_dir, "main")
            result = main(["-o", executable, source])
            run_result = subprocess.run(
                [executable],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result, 0)
        # main.lang prints two values after comptime specialization
        self.assertEqual(run_result.stdout.strip(), "29\n30")


if __name__ == "__main__":
    unittest.main()
