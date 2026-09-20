import io
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs


ROOT = Path(__file__).resolve().parents[1]


class BootstrapAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.executable = cls.directory / "jack-bootstrap"
        cls.driver = CompilerDriver(print_handler=None)
        cls.options = CompilationOptions(module_roots=(ROOT / "selfhost",))
        cls.driver.compile_executable(
            ROOT / "selfhost/bootstrap/main.jack",
            CompilationOptions(
                module_roots=(ROOT / "selfhost",),
                backend="llvm",
                output=cls.executable,
            ),
        )
        cls.program = cls.driver.compile_hir(ROOT / "selfhost/bootstrap/main.jack", cls.options)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_project(self, files, *arguments):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            base = Path(directory)
            for name, source in files.items():
                path = base / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            return subprocess.run(
                [str(self.executable), "--diagnostic-format", "stable",
                 *arguments, str(base / "entry.jack")],
                capture_output=True, text=True, timeout=30,
            )

    def test_lexical_scopes_and_stable_dump(self):
        files = {"entry.jack": """
module app;
i32 twice(i32 value) {
    i32 result = value + value;
    { i32 inner = result; }
    return result;
}
i32 answer = twice(21);
"""}
        first = self.run_project(files, "--dump", "symbols")
        second = self.run_project(files, "--dump", "symbols")
        self.assertEqual((0, ""), (first.returncode, first.stderr))
        normalize = lambda output: re.sub(r"/[^\t\n]*/entry\.jack", "ENTRY", output)
        self.assertEqual(normalize(first.stdout), normalize(second.stdout))
        self.assertIn("\tinner\tlocal\t", first.stdout)
        self.assertIn("\ttwice\tfunction\t", first.stdout)
        self.assertIn("\tcall\tresolved\t", first.stdout)

    def test_independent_unknown_names_and_block_boundary(self):
        result = self.run_project({"entry.jack": """
module app;
i32 value() {
    i32 first = missing;
    { i32 inside = first; }
    return inside + another;
}
"""}, "--check-names")
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual(3, result.stderr.count("resolve.unknown-name"))

    def test_selective_and_alias_imports(self):
        library = "module api; pub i32 add(i32 a, i32 b) { return a + b; } i32 hidden = 1;"
        for source in (
            "module app; import api.{add}; i32 result = add(1, 2);",
            "module app; import api as a; i32 result = a.add(1, 2);",
        ):
            with self.subTest(source=source):
                result = self.run_project({"entry.jack": source, "api.jack": library},
                                          "--check-names")
                self.assertEqual((0, "", ""),
                                 (result.returncode, result.stdout, result.stderr))

    def test_private_import_and_conflicting_declarations(self):
        private = self.run_project({
            "entry.jack": "module app; import api.{hidden};",
            "api.jack": "module api; i32 hidden = 1;",
        }, "--check-names")
        self.assertEqual(1, private.returncode)
        self.assertIn("module.private-symbol", private.stderr)
        duplicate = self.run_project({
            "entry.jack": "module app; i32 value = 1; i32 value = 2;",
        }, "--check-names")
        self.assertEqual(1, duplicate.returncode)
        self.assertIn("resolve.duplicate-declaration", duplicate.stderr)
        self.assertIn("label", duplicate.stderr)

    def test_analysis_never_executes_comptime(self):
        result = self.run_project({
            "entry.jack": 'module app; comptime print("MUST_NOT_RUN"); i32 value = 1;',
        }, "--check-names")
        self.assertEqual((0, "", ""),
                         (result.returncode, result.stdout, result.stderr))

    def test_invalid_project_options(self):
        files = {"entry.jack": "module app;"}
        for arguments in (
            ("--module-root", "other"),
            ("--check-names", "--check-names"),
            ("--dump", "symbols", "--check-names"),
            ("--check-names", "--stub", "broken"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_project(files, *arguments)
                self.assertEqual(2, result.returncode)

    def test_concrete_implementation_use(self):
        result = self.run_project({"entry.jack": """
module app;
interface Readable { i32 read(&in self); }
struct Item { i32 read(&in self) { return 1; } }
Item implements Readable { use read; }
"""}, "--dump", "symbols")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        self.assertIn("\tmember\tresolved\t", result.stdout)

    def test_comptime_parameters_in_return_type_but_not_earlier_parameters(self):
        valid = self.run_project({
            "entry.jack": "module app; T identity(comptime type T, T value) { return value; }",
        }, "--check-names")
        self.assertEqual((0, ""), (valid.returncode, valid.stderr))
        invalid = self.run_project({
            "entry.jack": "module app; void bad(T value, comptime type T) { }",
        }, "--check-names")
        self.assertEqual(1, invalid.returncode)
        self.assertIn("resolve.unknown-name", invalid.stderr)

    def test_struct_literal_field_binding(self):
        valid = self.run_project({
            "entry.jack": "module app; struct Item { i32 value; } Item item = Item { value = 1 };",
        }, "--dump", "symbols")
        self.assertEqual((0, ""), (valid.returncode, valid.stderr))
        self.assertIn("\tmember\tresolved\t", valid.stdout)
        invalid = self.run_project({
            "entry.jack": "module app; struct Item { i32 value; } Item item = Item { other = 1 };",
        }, "--check-names")
        self.assertEqual(1, invalid.returncode)
        self.assertIn("resolve.missing-member", invalid.stderr)

    def test_interpreter_matches_native_name_check(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            entry = Path(directory) / "entry.jack"
            entry.write_text("module app; i32 twice(i32 x) { return x + x; }")
            arguments = ["jack-bootstrap", "--check-names", str(entry)]
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = Interpreter(externs=default_runtime_externs()).eval_hir_program(
                    self.program, arguments,
                )
            native = subprocess.run(
                [str(self.executable), *arguments[1:]],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual((native.returncode, native.stdout, native.stderr),
                             (status, stdout.getvalue(), stderr.getvalue()))


if __name__ == "__main__":
    unittest.main()
