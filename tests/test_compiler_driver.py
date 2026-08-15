import tempfile
import unittest
from pathlib import Path

from jack.compiler_driver import (
    BackendArtifactError,
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


class ArtifactBackend:
    name = 'artifact'

    def __init__(self, artifacts):
        self.artifacts = artifacts

    def emit(self, _program):
        return self.artifacts


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
            self.assertIn('-O0', commands[0])

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
        self.assertTrue(raised.exception.artifact_paths)
        self.assertIn('Generated inputs:', str(raised.exception))

    def test_driver_passes_requested_optimization_level(self):
        commands = []

        def run(command):
            commands.append(tuple(command))
            Path(command[command.index('-o') + 1]).touch()
            return ToolchainResult(0)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jack'
            source.write_text('i32 value = 7;')
            CompilerDriver(
                [FakeBackend()], toolchain_runner=run, print_handler=None
            ).compile_executable(
                source,
                CompilationOptions(
                    backend='fake', output=root / 'program', optimization=2
                ),
            )

        self.assertIn('-O2', commands[0])

    def test_driver_rejects_invalid_optimization_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jack'
            source.write_text('i32 value = 7;')
            with self.assertRaisesRegex(Exception, 'Optimization level'):
                CompilerDriver([FakeBackend()], print_handler=None).compile_executable(
                    source, CompilationOptions(backend='fake', optimization=4)
                )

    def test_driver_validates_backend_artifacts_before_writing(self):
        invalid = (
            BackendArtifacts(files={'../escape': 'x'}, link_inputs=('../escape',)),
            BackendArtifacts(files={'module': 'x'}, link_inputs=()),
            BackendArtifacts(files={'module': 'x'}, link_inputs=('missing',)),
            BackendArtifacts(files={'module': 'x'}, link_inputs=('module', 'module')),
            BackendArtifacts(files={'module': ''}, link_inputs=('module',)),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jack'
            source.write_text('i32 value = 7;')
            for artifacts in invalid:
                with self.subTest(artifacts=artifacts):
                    with self.assertRaises(BackendArtifactError):
                        CompilerDriver(
                            [ArtifactBackend(artifacts)], print_handler=None
                        ).compile_executable(
                            source,
                            CompilationOptions(
                                backend='artifact', output=root / 'program'
                            ),
                        )

    def test_failed_link_preserves_existing_output(self):
        def fail(command):
            Path(command[command.index('-o') + 1]).write_text('partial')
            return ToolchainResult(1, stderr='link failed')

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jack'
            output = root / 'program'
            source.write_text('i32 value = 7;')
            output.write_text('old executable')
            with self.assertRaises(ToolchainError):
                CompilerDriver(
                    [FakeBackend()], toolchain_runner=fail, print_handler=None
                ).compile_executable(
                    source, CompilationOptions(backend='fake', output=output)
                )

            self.assertEqual('old executable', output.read_text())


if __name__ == '__main__':
    unittest.main()
