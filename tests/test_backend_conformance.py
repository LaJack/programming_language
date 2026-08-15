import io
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.cli import main as jack_main
from jack.compiler_driver import CompilationOptions, CompilerDriver

try:
    from tests.conformance_cases import CONFORMANCE_CASES, ConformanceCase
except ImportError:
    from conformance_cases import CONFORMANCE_CASES, ConformanceCase


class BackendConformanceTests(unittest.TestCase):
    def materialize(self, root: Path, case: ConformanceCase) -> Path:
        entry = root / 'main.jack'
        entry.write_text(case.source)
        for name, content in case.files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return entry

    def run_interpreter(self, entry: Path, root: Path):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = jack_main([
                '--module-root', str(root), '-i', str(entry),
            ])
        return status, stdout.getvalue(), stderr.getvalue()

    def run_native(
        self,
        entry: Path,
        root: Path,
        backend: str,
        optimization: int = 0,
        debug: bool = False,
    ):
        compile_output = []
        executable = root / f'program-{backend}-O{optimization}'
        CompilerDriver(print_handler=compile_output.append).compile_executable(
            entry,
            CompilationOptions(
                backend=backend,
                output=executable,
                module_roots=(root,),
                optimization=optimization,
                debug=debug,
            ),
        )
        completed = subprocess.run(
            [str(executable)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = ''.join(f'{line}\n' for line in compile_output) + completed.stdout
        return completed.returncode, stdout, completed.stderr

    def assert_case(
        self,
        case: ConformanceCase,
        backend: str | None,
        optimization=0,
        debug=False,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = self.materialize(root, case)
            if backend is None:
                status, stdout, stderr = self.run_interpreter(entry, root)
                runtime = 'interpreter'
            else:
                status, stdout, stderr = self.run_native(
                    entry, root, backend, optimization, debug
                )
                runtime = f'{backend} -O{optimization}' + (' -g' if debug else '')
            self.assertEqual(
                case.expected_exit_status,
                status,
                f'{case.name} under {runtime}:\n{stderr}',
            )
            self.assertEqual(case.expected_stdout, stdout, f'{case.name} under {runtime}')

    def test_shared_cases_match_interpreter_c_and_llvm(self):
        for case in CONFORMANCE_CASES:
            for backend in (None, 'c', 'llvm'):
                with self.subTest(case=case.name, backend=backend or 'interpreter'):
                    self.assert_case(case, backend)

    def test_representative_native_cases_match_at_O2(self):
        for case in (CONFORMANCE_CASES[0], CONFORMANCE_CASES[3]):
            for backend in ('c', 'llvm'):
                with self.subTest(case=case.name, backend=backend):
                    self.assert_case(case, backend, optimization=2)

    def test_debug_native_cases_match_at_O0(self):
        for case in CONFORMANCE_CASES:
            for backend in ('c', 'llvm'):
                with self.subTest(case=case.name, backend=backend):
                    self.assert_case(case, backend, debug=True)

    def test_representative_debug_native_cases_match_at_O2(self):
        for case in (CONFORMANCE_CASES[0], CONFORMANCE_CASES[3]):
            for backend in ('c', 'llvm'):
                with self.subTest(case=case.name, backend=backend):
                    self.assert_case(
                        case, backend, optimization=2, debug=True
                    )

    @unittest.skipUnless(shutil.which('readelf'), 'readelf is not available')
    def test_debug_executables_contain_jack_line_tables(self):
        case = CONFORMANCE_CASES[0]
        for backend in ('c', 'llvm'):
            with self.subTest(backend=backend):
                with tempfile.TemporaryDirectory() as tmpdir:
                    root = Path(tmpdir)
                    entry = self.materialize(root, case)
                    executable = root / f'program-{backend}'
                    CompilerDriver(print_handler=None).compile_executable(
                        entry,
                        CompilationOptions(
                            backend=backend,
                            output=executable,
                            module_roots=(root,),
                            debug=True,
                        ),
                    )
                    decoded = subprocess.run(
                        [
                            'readelf', '--debug-dump=decodedline',
                            str(executable),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                self.assertEqual(0, decoded.returncode, decoded.stderr)
                self.assertIn('main.jack', decoded.stdout)

    def test_checked_in_examples_match_all_three_runtimes(self):
        repository = Path(__file__).parents[1]
        for entry in sorted((repository / 'examples').glob('*.jack')):
            with self.subTest(example=entry.name, runtime='interpreter'):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    status = jack_main(['-i', str(entry)])
                self.assertEqual(0, status, stderr.getvalue())
                expected = stdout.getvalue()
            for backend in ('c', 'llvm'):
                with self.subTest(example=entry.name, runtime=backend):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        root = Path(tmpdir)
                        compile_output = []
                        executable = root / 'program'
                        CompilerDriver(
                            print_handler=compile_output.append
                        ).compile_executable(
                            entry,
                            CompilationOptions(
                                backend=backend, output=executable
                            ),
                        )
                        completed = subprocess.run(
                            [str(executable)],
                            cwd=repository,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    actual = (
                        ''.join(f'{line}\n' for line in compile_output)
                        + completed.stdout
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertEqual(expected, actual)


if __name__ == '__main__':
    unittest.main()
