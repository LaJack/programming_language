import io
from collections import Counter
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.module_loader import load_source_graph
from jack.runtime_externs import default_runtime_externs
from tests.bootstrap_interpreter_runner import run_hir_isolated


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

    def test_independent_binding_targets_for_imported_function(self):
        from tests.bootstrap_binding_normalization import (
            StageZeroBindings, bootstrap_projection,
        )

        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            root = Path(directory)
            entry = root / "entry.jack"
            library = root / "api.jack"
            entry.write_text("""module app;
import api.{add};
i32 answer = add(2, 3);
""")
            library.write_text("""module api;
pub i32 add(i32 left, i32 right) { return left + right; }
""")
            expected = StageZeroBindings(entry)
            result = subprocess.run(
                [str(self.executable), "--dump", "symbols", str(entry)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual((0, ""), (result.returncode, result.stderr))
            _, actual = bootstrap_projection(result.stdout)
            comparable = {"read", "call", "type_use"}
            expected_refs = Counter(reference for reference in expected.references
                                    if reference.role in comparable)
            actual_refs = Counter(reference for reference in actual
                                  if reference.role in comparable)
            self.assertEqual(expected_refs, actual_refs)

    def test_formatted_expression_targets_use_absolute_utf8_byte_spans(self):
        from tests.bootstrap_binding_normalization import (
            StageZeroBindings, bootstrap_projection,
        )

        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            entry = Path(directory) / "entry.jack"
            entry.write_text('module app; i32 value = 3; print(f"caf\u00e9 {value} {value + 1}");')
            expected = StageZeroBindings(entry)
            result = subprocess.run(
                [str(self.executable), "--dump", "symbols", str(entry)],
                capture_output=True, text=True, timeout=45,
            )
            self.assertEqual((0, ""), (result.returncode, result.stderr))
            _, actual = bootstrap_projection(result.stdout)
            self.assertEqual(
                Counter(expected.references),
                Counter(reference for reference in actual
                        if reference.role != "declaration"),
            )
            positions = [reference.start for reference in actual
                         if reference.role == "read"]
            self.assertEqual(2, len(positions))
            self.assertGreater(min(positions), entry.read_bytes().index(b"caf"))

    def test_complete_bootstrap_bindings_match_stage_zero(self):
        from tests.bootstrap_binding_normalization import (
            StageZeroBindings, bootstrap_projection,
        )
        from tests.bootstrap_identifier_coverage import (
            assert_complete_classification, coverage_for_source,
        )

        assert_complete_classification(ROOT / "selfhost/bootstrap/syntax.jack")
        entry = ROOT / "selfhost/bootstrap/main.jack"
        roots = (ROOT / "selfhost", ROOT / "jack")
        expected = StageZeroBindings(entry, roots)
        result = subprocess.run(
            [str(self.executable), "--dump", "symbols",
             "--module-root", str(roots[0]), "--module-root", str(roots[1]),
             str(entry)], capture_output=True, text=True, timeout=45,
        )
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        symbols, actual = bootstrap_projection(result.stdout)
        expected_references = Counter(expected.references)
        actual_references = Counter(reference for reference in actual
                                    if reference.role != "declaration")
        self.assertEqual(expected_references, actual_references)
        self.assertFalse(any(reference.status == "invalid"
                             for reference in actual_references))
        self.assertEqual(
            {"receiver_type", "computed_type", "pattern_type"},
            {reference.target for reference in actual_references
             if reference.status == "deferred"},
        )
        self.assertGreater(len(symbols), 5000)
        self.assertGreater(sum(actual_references.values()), 29000)
        totals = [0, 0, 0]
        for module, source in expected.sources.items():
            started = time.perf_counter()
            with self.subTest(module=module):
                counts = coverage_for_source(self.executable, source, symbols, actual)
                totals = [previous + count for previous, count in zip(totals, counts)]
            print(f"identifier coverage {module}: {time.perf_counter() - started:.2f}s",
                  file=sys.stderr, flush=True)
        self.assertGreater(totals[0], 50000)
        self.assertGreater(totals[2], 29000)

    def test_complete_module_graph_matches_stage_zero(self):
        entry = ROOT / "selfhost/bootstrap/main.jack"
        roots = (ROOT / "selfhost", ROOT / "jack")
        expected = load_source_graph(entry, search_roots=list(roots))
        result = subprocess.run(
            [str(self.executable), "--dump", "modules",
             "--module-root", str(roots[0]), "--module-root", str(roots[1]),
             str(entry)], capture_output=True, text=True, timeout=30,
        )
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        records = [line.split("\t") for line in result.stdout.splitlines()]
        module_rows = [row for row in records if row[0] == "module"]
        import_rows = [row for row in records if row[0] == "import"]
        actual_modules = {row[2]: Path(row[3]).resolve() for row in module_rows}
        expected_modules = {name: module.path.resolve()
                            for name, module in expected.modules.items()}
        self.assertEqual(expected_modules, actual_modules)
        self.assertTrue(all(row[4] == "loaded" for row in module_rows))
        names_by_id = {row[1]: row[2] for row in module_rows}
        actual_edges = Counter((names_by_id[row[2]], row[3])
                               for row in import_rows)
        expected_edges = Counter((name, imported)
                                 for name, module in expected.modules.items()
                                 for imported in module.imports)
        self.assertEqual(expected_edges, actual_edges)
        self.assertTrue(all(row[4] == row[6] and row[7] == "resolved"
                            for row in import_rows))
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

    def test_recovered_identifier_coverage_keeps_later_bindings(self):
        from tests.bootstrap_binding_normalization import bootstrap_projection
        from tests.bootstrap_identifier_coverage import TokenSource, coverage_for_source

        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            entry = Path(directory) / "entry.jack"
            source = """module app;
i32 broken = ;
i32 later = 2;
i32 answer = later;
"""
            entry.write_text(source)
            result = subprocess.run(
                [str(self.executable), "--dump", "symbols",
                 "--diagnostic-format", "stable", str(entry)],
                capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("parse.expected-expression", result.stderr)
            symbols, references = bootstrap_projection(result.stdout)
            token_source = TokenSource(entry)
            coverage_for_source(self.executable, token_source, symbols, references,
                                recovered=True)
            later_start = source.rindex("later;")
            omitted = [reference for reference in references
                       if reference.start != later_start]
            with self.assertRaises(AssertionError):
                coverage_for_source(self.executable, token_source, symbols, omitted,
                                    recovered=True)

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

    def test_comptime_host_calls_are_never_executed_by_analysis(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            base = Path(directory)
            entry = base / "entry.jack"
            absent = base / "must-not-be-opened.jack"
            created = base / "must-not-be-created.txt"
            entry.write_text(f"""module app;
import std.io;
extern "c" void forbidden_host_call();
comptime File input = open_read("{absent}");
comptime File output = create("{created}");
comptime forbidden_host_call();
""")
            arguments = ["jack-bootstrap", "--check-names",
                         "--module-root", str(ROOT / "jack"), str(entry)]
            native = subprocess.run(
                [str(self.executable), *arguments[1:]],
                capture_output=True, text=True, timeout=45,
            )
            self.assertEqual((0, "", ""),
                             (native.returncode, native.stdout, native.stderr))
            self.assertFalse(created.exists())

            instrumented = base / "instrumented.jack"
            instrumented.write_text(f"""module instrumented;
extern "c" void forbidden_read(str path);
extern "c" void forbidden_write(str path);
extern "c" void forbidden_host_call();
comptime forbidden_read("{absent}");
comptime forbidden_write("{created}");
comptime forbidden_host_call();
""")
            interpreter_arguments = ["jack-bootstrap", "--check-names",
                                     str(instrumented)]
            externs = default_runtime_externs()

            def forbidden(*_arguments):
                raise AssertionError("analyzed comptime host call was executed")

            for name in ("forbidden_read", "forbidden_write",
                         "forbidden_host_call"):
                externs[name] = forbidden
            status, stdout, stderr = run_hir_isolated(
                self.program, interpreter_arguments, externs=externs,
                timeout=240, label="comptime non-execution",
            )
            self.assertEqual((0, "", ""), (status, stdout, stderr))
            self.assertFalse(created.exists())

    def test_comptime_selection_defers_only_selected_bodies(self):
        selected = self.run_project({"entry.jack": """
module app;
comptime if (true) {
    i32 value = generated_name;
}
"""}, "--dump", "symbols")
        self.assertEqual((0, ""), (selected.returncode, selected.stderr))
        self.assertIn("\tcomptime_selection\n", selected.stdout)

        condition = self.run_project({"entry.jack": """
module app;
comptime if (missing_condition) { i32 value = generated_name; }
"""}, "--dump", "symbols")
        self.assertEqual(1, condition.returncode)
        self.assertEqual(1, condition.stderr.count("resolve.unknown-name"))
        self.assertIn("\tcomptime_selection\n", condition.stdout)

        unconditional = self.run_project({"entry.jack": """
module app;
comptime { i32 value = always_missing; }
"""}, "--dump", "symbols")
        self.assertEqual(1, unconditional.returncode)
        self.assertIn("resolve.unknown-name", unconditional.stderr)
        self.assertNotIn("\tcomptime_selection\n", unconditional.stdout)

    def test_nested_comptime_selection_keeps_deferred_context(self):
        result = self.run_project({"entry.jack": """
module app;
comptime if (true) {
    if (nested_condition) { i32 value = nested_value; }
}
"""}, "--dump", "symbols")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        self.assertEqual(2, result.stdout.count("\tcomptime_selection\n"))

    def test_each_deferral_has_its_specific_trigger(self):
        from tests.bootstrap_binding_normalization import bootstrap_projection

        fixtures = {
            "receiver_type": """module app;
struct Item { i32 value; }
void inspect(&in Item item) { print(item.value); }
""",
            "generic_type": """module app;
void inspect(comptime type T) { i32 value = T.member; }
""",
            "computed_type": """module app;
comptime type Generated = Union("one");
i32 value = Generated.one;
""",
            "pattern_type": """module app;
void inspect(i32 value) {
    match (&in value) { .some(item) { print(item); } _ { } }
}
""",
            "comptime_selection": """module app;
comptime if (true) { i32 value = generated_name; }
""",
        }
        for reason, source in fixtures.items():
            with self.subTest(reason=reason):
                result = self.run_project({"entry.jack": source}, "--dump", "symbols")
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                _, occurrences = bootstrap_projection(result.stdout)
                deferred = [reference.target for reference in occurrences
                            if reference.status == "deferred"]
                self.assertEqual([reason], deferred)
                self.assertFalse(any(reference.status == "invalid"
                                     for reference in occurrences))

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

    def test_implementation_use_rejects_fields(self):
        result = self.run_project({"entry.jack": """
module app;
interface Readable { i32 read(&in self); }
struct Item { i32 read; }
Item implements Readable { use read; }
"""}, "--dump", "symbols")
        self.assertEqual(1, result.returncode)
        self.assertIn("resolve.missing-member", result.stderr)

    def test_failed_import_uses_share_checked_issue(self):
        for statement, expression in (
            ("import missing as api;", "api.value + unrelated"),
            ("import missing.{value};", "value + unrelated"),
        ):
            with self.subTest(statement=statement):
                result = self.run_project({"entry.jack":
                    f"module app; {statement} i32 result = {expression};"
                }, "--dump", "symbols")
                self.assertEqual(1, result.returncode)
                self.assertEqual(1, result.stderr.count("resolve.unknown-name"))
                records = [line.split("\t") for line in result.stdout.splitlines()]
                issues = {record[1] for record in records if record[0] == "issue"}
                invalid = [record for record in records
                           if record[0] == "reference" and "invalid" in record]
                self.assertGreaterEqual(len(invalid), 3)
                for record in invalid:
                    self.assertIn(record[record.index("invalid") + 1], issues)

    def test_conflicting_imports_poison_uses(self):
        result = self.run_project({
            "entry.jack": """module app;
import first.{value};
import second.{value};
i32 result = value;
""",
            "first.jack": "module first; pub i32 value = 1;",
            "second.jack": "module second; pub i32 value = 2;",
        }, "--dump", "symbols")
        self.assertEqual(1, result.returncode)
        self.assertIn("module.import-conflict", result.stderr)
        records = [line.split("\t") for line in result.stdout.splitlines()]
        causes = {record[1] for record in records if record[0] == "issue"
                  and record[2] == "module.import-conflict"}
        self.assertEqual(1, len(causes))
        self.assertTrue(any(record[0] == "issue_label" and record[1] in causes
                            for record in records))
        self.assertTrue(any(record[0] == "reference" and record[-2] == "invalid"
                            and record[-1] in causes for record in records))

    def test_local_declaration_precedes_bare_import(self):
        result = self.run_project({
            "entry.jack": "module app; import api; i32 value = 2; i32 other = value;",
            "api.jack": "module api; pub i32 value = 1;",
        }, "--check-names")
        self.assertEqual((0, ""), (result.returncode, result.stderr))

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

    def test_qualified_type_segments_and_private_member(self):
        files = {
            "entry.jack": """module app;
import api as a;
a.Item first;
a.Item.Missing second;
a.secret third;
""",
            "api.jack": "module api; pub struct Item { i32 value; } struct secret { }",
        }
        result = self.run_project(files, "--dump", "symbols")
        self.assertEqual(1, result.returncode)
        self.assertIn("resolve.missing-member", result.stderr)
        self.assertIn("module.private-symbol", result.stderr)
        references = [line.split("\t") for line in result.stdout.splitlines()
                      if line.startswith("reference\t")]
        self.assertTrue(any(record[-3:-1] == ["member", "resolved"] for record in references))
        self.assertTrue(any("invalid" in record for record in references))

    def test_parenthesized_qualifier_and_deferred_type_members(self):
        files = {
            "entry.jack": """module app;
import api as a;
i32 answer = (a).number;
comptime type Generated = Union("first");
i32 generated = Generated.first;
void generic(comptime type T) { i32 unknown = T.field; }
""",
            "api.jack": "module api; pub i32 number = 1;",
        }
        result = self.run_project(files, "--dump", "symbols")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        self.assertIn("\tcomputed_type\n", result.stdout)
        self.assertIn("\tgeneric_type\n", result.stdout)
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
