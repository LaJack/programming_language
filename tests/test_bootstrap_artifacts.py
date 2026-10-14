from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock

from jack.compiler_driver import CompilationOptions, CompilationResult
from tests.bootstrap_artifacts import BootstrapArtifacts


class BootstrapArtifactTests(unittest.TestCase):
    def setUp(self):
        self.driver = Mock()
        self.driver.compile_executable.side_effect = lambda entry, options: (
            CompilationResult(options.output, options.backend)
        )
        self.driver.compile_hir.return_value = {"nodes": []}
        self.registry = BootstrapArtifacts(Mock(return_value=self.driver))
        self.addCleanup(self.registry.close)

    def test_lazy_reuse_and_configuration_separation(self):
        self.driver.compile_executable.assert_not_called()
        options = CompilationOptions(module_roots=(Path("selfhost"),))
        first = self.registry.executable(options=options)
        again = self.registry.executable(options=replace(options, output=Path("unused")))
        self.assertIs(first, again)
        for changed in (
            replace(options, backend="c"), replace(options, optimization=2),
            replace(options, debug=True), replace(options, clang="clang-18"),
            replace(options, module_roots=(Path("jack"),)),
            replace(options, import_overrides={"api": "stub"}),
        ):
            self.assertIsNot(first, self.registry.executable(options=changed))
        self.assertEqual(7, self.driver.compile_executable.call_count)
        self.driver.compile_hir.assert_not_called()

    def test_hir_snapshots_are_isolated_and_link_options_do_not_rebuild(self):
        first = self.registry.hir()
        first["nodes"].append("changed")
        self.assertEqual({"nodes": []}, self.registry.hir(
            options=CompilationOptions(module_roots=(Path("selfhost"), Path("jack")),
                                       backend="c", optimization=2, debug=True)))
        self.assertEqual(1, self.driver.compile_hir.call_count)

    def test_failed_build_is_not_cached(self):
        self.driver.compile_executable.side_effect = RuntimeError("build failed")
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                self.registry.executable()
        self.assertEqual(2, self.driver.compile_executable.call_count)

    def test_extern_identity_and_cleanup(self):
        binding = lambda: None
        first = self.registry.executable(externs={"host": binding})
        self.assertIs(first, self.registry.executable(externs={"host": binding}))
        self.assertIsNot(first, self.registry.executable(externs={"host": lambda: None}))
        directory = first.output_path.parent
        self.registry.close()
        self.assertFalse(directory.exists())

    def test_saved_artifact_builds_require_private_ownership(self):
        with self.assertRaises(ValueError):
            self.registry.executable(options=CompilationOptions(save_temps=Path("artifacts")))
