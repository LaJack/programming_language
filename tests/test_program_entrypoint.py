import subprocess
import tempfile
import unittest
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.hir_lowering_pass import HIRLoweringError
from jack.interpreter import Interpreter


class ProgramEntrypointTests(unittest.TestCase):
    SOURCE = '''
i32 main(&in str[] arguments) {
    print(len(arguments));
    return len(arguments);
}
'''

    def test_interpreter_invokes_typed_main(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text(self.SOURCE)
            program = CompilerDriver(print_handler=None).compile_hir(
                entry, CompilationOptions()
            )
            status = Interpreter().eval_hir_program(program, ['jack-app', 'one'])
        self.assertEqual(2, status)

    def test_native_backends_forward_arguments_and_status(self):
        for backend in ('c', 'llvm'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                entry = root / 'main.jack'
                output = root / 'app'
                entry.write_text(self.SOURCE)
                CompilerDriver(print_handler=None).compile_executable(
                    entry, CompilationOptions(backend=backend, output=output)
                )
                result = subprocess.run(
                    [str(output), 'one'], capture_output=True, text=True, check=False
                )
                self.assertEqual(2, result.returncode)
                self.assertEqual('len(arguments) = 2\n', result.stdout)

    def test_invalid_typed_main_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text('void main() { }')
            with self.assertRaisesRegex(HIRLoweringError, 'Program entry point'):
                CompilerDriver(print_handler=None).compile_hir(
                    entry, CompilationOptions()
                )


if __name__ == '__main__':
    unittest.main()
