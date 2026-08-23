import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.parser import Lexer
from jack.runtime_externs import default_runtime_externs


ROOT = Path(__file__).resolve().parents[1]
SELFHOST_ROOT = ROOT / 'selfhost'
ENTRY = SELFHOST_ROOT / 'bootstrap' / 'main.jack'


def expected_dump(source: str) -> str:
    kinds = {
        'EOF': 0,
        'IDENT': 1,
        'INT': 2,
        'FLOAT': 3,
        'STRING': 4,
        'FSTRING': 7,
    }
    records = []
    for token in Lexer(source).tokenize():
        span = token.span
        records.append(
            f'{kinds.get(token.kind, 5)}\t{span.start_offset}\t{span.end_offset}'
            f'\t{span.start_line}\t{span.start_column}\n'
        )
    return ''.join(records)


class BootstrapLexerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.root = Path(cls.build.name)
        driver = CompilerDriver(print_handler=None)
        cls.program = driver.compile_hir(
            ENTRY, CompilationOptions(module_roots=(SELFHOST_ROOT,))
        )
        cls.executables = {}
        for backend in ('c', 'llvm'):
            output = cls.root / f'bootstrap-{backend}'
            driver.compile_executable(
                ENTRY,
                CompilationOptions(
                    backend=backend,
                    output=output,
                    module_roots=(SELFHOST_ROOT,),
                ),
            )
            cls.executables[backend] = output
        optimized = cls.root / 'bootstrap-llvm-o2'
        driver.compile_executable(
            ENTRY,
            CompilationOptions(
                backend='llvm',
                output=optimized,
                optimization=2,
                module_roots=(SELFHOST_ROOT,),
            ),
        )
        cls.executables['llvm-o2'] = optimized

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_interpreter(self, path: Path, *arguments: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = Interpreter(
                externs=default_runtime_externs()
            ).eval_hir_program(
                self.program, ['jack-bootstrap', str(path), *arguments]
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def run_native(self, backend: str, *arguments: str):
        result = subprocess.run(
            [str(self.executables[backend]), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    def assert_runtime_parity(self, path: Path, expected_stdout: str, status: int = 0,
                              expected_stderr: str = ''):
        results = [self.run_interpreter(path)]
        results.extend(self.run_native(backend, str(path)) for backend in ('c', 'llvm'))
        for runtime, result in zip(('interpreter', 'c', 'llvm'), results):
            with self.subTest(runtime=runtime):
                self.assertEqual(status, result[0])
                self.assertEqual(expected_stdout, result[1])
                self.assertEqual(expected_stderr, result[2])

    def test_checked_in_examples_match_python_lexer(self):
        for path in sorted((ROOT / 'examples').glob('*.jack')):
            with self.subTest(path=path.name):
                source = path.read_text()
                self.assert_runtime_parity(path, expected_dump(source))

    def test_empty_and_large_sources_are_dynamic(self):
        cases = ['', ' '.join(f'name{index}' for index in range(300))]
        for index, source in enumerate(cases):
            path = self.root / f'dynamic-{index}.jack'
            path.write_text(source)
            self.assert_runtime_parity(path, expected_dump(source))

    def test_invalid_token_is_dumped_before_failure(self):
        path = self.root / 'invalid.jack'
        path.write_text('@ valid')
        expected = (
            '6\t0\t1\t1\t1\n'
            '1\t2\t7\t1\t3\n'
            '0\t7\t7\t1\t8\n'
        )
        self.assert_runtime_parity(
            path, expected, status=1, expected_stderr='error\tlex\t1\t1\n'
        )

    def test_unterminated_string_and_comment_report_invalid_tokens(self):
        for name, source in (
            ('string', '"bad'),
            ('comment', '/* bad'),
            ('escape', '"bad\\q" next'),
        ):
            path = self.root / f'{name}.jack'
            path.write_text(source)
            results = [self.run_interpreter(path)]
            results.extend(self.run_native(backend, str(path)) for backend in ('c', 'llvm'))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0], results[2])
            self.assertEqual(1, results[0][0])
            self.assertIn('6\t0\t', results[0][1])

    def test_usage_and_missing_file_contracts(self):
        for backend in ('c', 'llvm'):
            status, stdout, stderr = self.run_native(backend)
            self.assertEqual((2, '', 'usage: jack-bootstrap <source>\n'),
                             (status, stdout, stderr))
            status, stdout, stderr = self.run_native(
                backend, str(self.root / 'missing.jack')
            )
            self.assertEqual(1, status)
            self.assertEqual('', stdout)
            self.assertRegex(stderr, r'^error\tio\t[0-9]+\n$')

    def test_invalid_utf8_source_reports_byte_offset(self):
        path = self.root / 'invalid-utf8.jack'
        path.write_bytes(b'ok \xff')
        for backend in ('c', 'llvm'):
            status, stdout, stderr = self.run_native(backend, str(path))
            self.assertEqual((1, '', 'error\tutf8\t3\n'), (status, stdout, stderr))

    def test_valid_utf8_uses_byte_offsets_and_columns(self):
        path = self.root / 'utf8.jack'
        path.write_text('\u00e9')
        expected = (
            '6\t0\t1\t1\t1\n'
            '6\t1\t2\t1\t2\n'
            '0\t2\t2\t1\t3\n'
        )
        self.assert_runtime_parity(
            path, expected, status=1, expected_stderr='error\tlex\t1\t1\n'
        )

    def test_optimized_llvm_matches_bootstrap_output(self):
        path = ROOT / 'examples' / 'vector.jack'
        expected = expected_dump(path.read_text())
        self.assertEqual((0, expected, ''), self.run_native('llvm-o2', str(path)))


if __name__ == '__main__':
    unittest.main()
