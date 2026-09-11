import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter


SOURCE = '''
struct Failure { }
union Choice { left; right; }
struct Resource {
    i32 tag;
    deinit(move self) { print(self.tag); }
}
void check(i32 branch) {
    try {
        Resource resource = Resource { tag = 7 };
        if (branch == 0) {
            raise Failure { };
        } else {
            if (branch == 1) {
                raise Failure { };
            } else {
                Choice choice = Choice.left;
                match (&in choice) {
                    .left { raise Failure { }; }
                    .right { }
                }
            }
        }
    } catch Failure { print("caught"); }
}
check(0);
check(1);
check(2);
'''


class CleanupErrorTraversalTests(unittest.TestCase):
    def test_if_else_and_match_errors_preserve_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'cleanup.jack'
            entry.write_text(SOURCE)
            driver = CompilerDriver(print_handler=None)
            program = driver.compile_hir(entry, CompilationOptions())
            output = io.StringIO()
            with redirect_stdout(output):
                Interpreter().eval_hir_program(program)
            expected = output.getvalue()
            self.assertEqual(3, expected.count('self.tag = 7'))
            self.assertEqual(3, sum(line.endswith('= caught') for line in expected.splitlines()))
            for backend in ('c', 'llvm'):
                for optimization in (0, 2):
                    with self.subTest(backend=backend, optimization=optimization):
                        executable = Path(directory) / f'{backend}-{optimization}'
                        driver.compile_executable(entry, CompilationOptions(
                            backend=backend, optimization=optimization, output=executable,
                        ))
                        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                        self.assertEqual(0, result.returncode, result.stderr)
                        self.assertEqual(expected, result.stdout)
