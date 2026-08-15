import tempfile
import unittest
from pathlib import Path

from jack.compiler_driver import (
    BackendArtifacts,
    BackendNotFoundError,
    CompilationOptions,
    CompilerDriver,
    ToolchainError,
    ToolchainResult,
)
from jack.hir_nodes import HIRProgram


class FakeBackend:
    name = 'fake'

    def __init__(self):
        self.program = None

    def emit(self, program: HIRProgram) -> BackendArtifacts:
        self.program = program
        return BackendArtifacts(
            files={'module.fake': 'backend output'},
            link_inputs=('module.fake',),
        )


class CompilerDriverTests(unittest.TestCase):
    def test_driver_lowers_once_and_materializes_backend_artifacts(self):
        commands = []

        def run(command):
            commands.append(tuple(command))
            Path(command[command.index('-o') + 1]).touch()
            return ToolchainResult(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jack'
            source.write_text('i32 value = 7;')
            output = root / 'program'
            temps = root / 'temps'
            backend = FakeBackend()
            result = CompilerDriver(
                [backend], toolchain_runner=run, print_handler=None
            ).compile_executable(
                source,
                CompilationOptions(
                    backend='fake', output=output, save_temps=temps
                ),
            )

            self.assertIsInstance(backend.program, HIRProgram)
            self.assertEqual(output.resolve(), result.output_path)
            self.assertEqual('fake', result.backend)
            self.assertEqual('backend output', (temps / 'module.fake').read_text())
            self.assertEqual((temps / 'module.fake',), result.saved_artifacts)
            self.assertEqual(str(temps / 'module.fake'), commands[0][1])

    def test_driver_rejects_unknown_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jack'
            source.write_text('i32 value = 7;')
            with self.assertRaisesRegex(BackendNotFoundError, 'Unknown compiler backend'):
                CompilerDriver([], print_handler=None).compile_executable(
                    source, CompilationOptions(backend='missing')
                )

    def test_driver_reports_toolchain_diagnostics(self):
        def fail(_command):
            return ToolchainResult(1, stderr='link failed')

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jack'
            source.write_text('i32 value = 7;')
            with self.assertRaisesRegex(ToolchainError, 'link failed') as raised:
                CompilerDriver(
                    [FakeBackend()], toolchain_runner=fail, print_handler=None
                ).compile_executable(
                    source,
                    CompilationOptions(backend='fake', output=root / 'program'),
                )

        self.assertTrue(raised.exception.command)
        self.assertEqual('link failed', raised.exception.stderr)


if __name__ == '__main__':
    unittest.main()
