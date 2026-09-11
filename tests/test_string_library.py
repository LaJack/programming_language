import subprocess
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs, jack_string_view


SOURCE = '''
import std.string;
import std.memory;

void run() raises CapacityError, LayoutError, AllocationError, ParseIntError {
    SystemAllocator allocator;
    String(SystemAllocator) text(allocator, "value=");
    text.append_i64(i64(0) - i64(42));
    print(text.as_str());
    print(parse_u64("18446744073709551615"));
    print(parse_i64("-42"));
}

try { run(); }
catch CapacityError { print("capacity"); }
catch LayoutError { print("layout"); }
catch AllocationError { print("allocation"); }
catch ParseIntError { print("parse"); }
'''

EXPECTED = (
    'text.as_str() = value=-42\n'
    'parse_u64("18446744073709551615") = 18446744073709551615\n'
    'parse_i64("-42") = -42\n'
)


class StringLibraryTests(unittest.TestCase):
    def test_empty_string_view_does_not_read_its_storage(self):
        self.assertEqual('', jack_string_view(object(), 0))

    def test_owned_string_and_integer_helpers_match_all_runtimes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = root / 'main.jack'
            entry.write_text(SOURCE)
            program = CompilerDriver(print_handler=None).compile_hir(
                entry, CompilationOptions()
            )
            interpreted = io.StringIO()
            with redirect_stdout(interpreted):
                Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
            self.assertEqual(EXPECTED, interpreted.getvalue())
            for backend in ('c', 'llvm'):
                output = root / backend
                CompilerDriver(print_handler=None).compile_executable(
                    entry, CompilationOptions(backend=backend, output=output, optimization=2)
                )
                result = subprocess.run(
                    [str(output)], capture_output=True, text=True, check=False
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(EXPECTED, result.stdout)


if __name__ == '__main__':
    unittest.main()
