import subprocess
import tempfile
import unittest
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from tests.bootstrap_artifacts import artifacts


ROOT = Path(__file__).resolve().parents[1]


class BootstrapTypingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.executable = artifacts.executable().output_path

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_source(self, source, *options):
        with tempfile.TemporaryDirectory(dir=self.directory) as directory:
            entry = Path(directory) / "entry.jack"
            entry.write_text(source)
            return subprocess.run(
                [str(self.executable), *options, str(entry)],
                capture_output=True, text=True, timeout=30,
            )

    def test_declaration_only_project_is_complete(self):
        result = self.run_source(
            "module app; struct Point { i32 x; i32 y; }",
            "--check-types",
        )
        self.assertEqual((0, "", ""), (result.returncode, result.stdout, result.stderr))

    def test_concrete_body_is_complete(self):
        result = self.run_source(
            "module app; i32 main() { return 1; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)

    def test_invalid_declared_type_takes_precedence(self):
        result = self.run_source(
            "module app; Missing value;",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("resolve.unknown-name", result.stderr)

    def test_concrete_stored_layout_cycles_are_rejected(self):
        result = self.run_source(
            "module app; struct Node { Node child; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.infinite-size", result.stderr)

    def test_pointer_indirection_breaks_stored_layout_recursion(self):
        result = self.run_source(
            "module app; struct Node { *in Node next; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_recursive_union_payload_layout_is_rejected(self):
        result = self.run_source(
            "module app; union Recursive { next(move Recursive value); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.infinite-size", result.stderr)

    def test_concrete_initializer_mismatch_is_reported(self):
        result = self.run_source(
            "module app; bool enabled = 42;",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.incompatible-initializer", result.stderr)

    def test_field_and_constant_initializers_are_checked(self):
        result = self.run_source(
            "module app; struct Flags { bool enabled = 1; } "
            "const bool global_enabled = 1;",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual(2, result.stderr.count("type.incompatible-initializer"))

    def test_integer_literal_narrowing_remains_deferred_to_range_rules(self):
        result = self.run_source(
            "module app; u8 small = 42;",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("type.incompatible-initializer", result.stderr)

    def test_integer_literal_range_boundaries(self):
        accepted = self.run_source(
            "module app; u8 top = 255; i8 low = -128;",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        rejected = self.run_source(
            "module app; u8 too_big = 256; i8 too_low = -129; "
            "u8 negative = -1;",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, rejected.returncode)
        self.assertEqual(3, rejected.stderr.count("type.literal-out-of-range"))

    def test_integer_literal_ranges_apply_to_calls_assignments_and_returns(self):
        result = self.run_source(
            "module app; void accept(u8 value) {} "
            "u8 bad_return() { return 256; } "
            "void bad_assignment() { u8 value = 0; value = 256; } "
            "void bad_call() { accept(256); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual(3, result.stderr.count("type.literal-out-of-range"))

    def test_concrete_call_arguments_and_arity_are_checked(self):
        result = self.run_source(
            "module app; void accept(i32 value) {} "
            "void wrong_type() { accept(true); } "
            "void wrong_arity() { accept(); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.incompatible-argument", result.stderr)
        self.assertIn("type.call-arity", result.stderr)

    def test_known_struct_field_access_is_typed(self):
        result = self.run_source(
            "module app; struct Point { i32 x; } "
            "i32 read(&in Point point) { return point.x; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_missing_member_on_known_struct_is_diagnosed(self):
        result = self.run_source(
            "module app; struct Point { i32 x; } "
            "i32 read(&in Point point) { return point.y; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.missing-member", result.stderr)

    def test_numeric_argument_conversion_is_accepted(self):
        result = self.run_source(
            "module app; void accept(i64 value) {} "
            "void caller() { accept(i32(7)); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("type.incompatible-argument", result.stderr)

    def test_concrete_methods_check_receiver_and_explicit_arguments(self):
        result = self.run_source(
            "module app; struct Point { i32 x; "
            "i32 read(&in self, i32 extra) { return self.x + extra; } } "
            "i32 caller(&in Point point) { return point.read(2); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_concrete_method_argument_errors(self):
        result = self.run_source(
            "module app; struct Point { "
            "void accept(&in self, i32 extra) {} } "
            "void caller(&in Point point) { point.accept(true); point.accept(); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("type.incompatible-argument", result.stderr)
        self.assertIn("type.call-arity", result.stderr)

    def test_immutable_fields_and_constants_reject_assignment(self):
        result = self.run_source(
            "module app; struct Point { i32 x; } const i32 fixed = 1; "
            "void caller(&in Point point) { point.x = 2; fixed = 3; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(2, result.stderr.count("type.invalid-assignment-target"))

    def test_mutable_methods_and_implicit_borrows_require_writable_places(self):
        result = self.run_source(
            "module app; struct Point { i32 x; "
            "void change(&inout self) { self.x = 2; } } "
            "void update(&inout i32 value) {} "
            "void caller(&in Point point) { point.change(); update(point.x); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(2, result.stderr.count("type.mutable-borrow-required"))

    def test_mutable_method_and_borrow_calls_are_complete(self):
        result = self.run_source(
            "module app; struct Point { i32 x; "
            "void change(&inout self) { self.x = 2; } } "
            "void update(&inout i32 value) {} "
            "void caller(&inout Point point) { point.change(); update(point.x); }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_conditions_returns_assignments_and_operators_are_checked(self):
        result = self.run_source(
            "module app; "
            "i32 bad_return() { return true; } "
            "void bad_condition() { if (1) {} } "
            "void bad_assignment() { i32 value = 1; value = true; } "
            "bool bad_operator() { return 1 + true == 0; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.incompatible-return", result.stderr)
        self.assertIn("type.non-boolean-condition", result.stderr)
        self.assertIn("type.incompatible-assignment", result.stderr)
        self.assertIn("type.invalid-operator", result.stderr)

    def test_nonvoid_fallthrough_is_reported(self):
        result = self.run_source(
            "module app; i32 missing_return() { i32 value = 1; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("type.missing-return-path", result.stderr)

    def test_type_dump_is_deterministic(self):
        source = "module app; i32 add(i32 a, i32 b) { return a + b; }"
        directory = self.directory / "deterministic-dump"
        directory.mkdir()
        entry = directory / "entry.jack"
        entry.write_text(source)
        first = subprocess.run(
            [str(self.executable), "--dump", "types", str(entry)],
            capture_output=True, text=True, timeout=30,
        )
        second = subprocess.run(
            [str(self.executable), "--dump", "types", str(entry)],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual((first.returncode, first.stdout, first.stderr),
                         (second.returncode, second.stdout, second.stderr))
        self.assertEqual(0, first.returncode)
        self.assertIn("signature\t", first.stdout)
        self.assertIn("\ttype\t", first.stdout)
        self.assertIn("\tplace\t", first.stdout)
        self.assertNotIn("pending\t", first.stdout)

    def test_intrinsic_expression_checks_are_once_per_node(self):
        result = self.run_source(
            "module app; i32 sum(i32 a, i32 b) { return (a + b) * (a + b); }",
            "--dump", "types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        fields = result.stdout.splitlines()[0].split("\t")
        self.assertEqual(["checking", "expressions", "9", "requests"], fields[:4])
        self.assertGreaterEqual(int(fields[4]), 9)

    def test_contextual_conversions_preserve_intrinsic_literal_types(self):
        result = self.run_source(
            "module app; u8 narrow() { u8 value = 42; return 7; }",
            "--dump", "types", "--diagnostic-format", "stable",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        conversions = [line.split("\t") for line in result.stdout.splitlines()
                       if line.startswith("conversion\t")]
        self.assertEqual({"initializer", "return"}, {item[-1] for item in conversions})
        self.assertTrue(all(item[-3] != item[-2] for item in conversions))

    def test_qualifier_comments_and_whitespace_do_not_change_access(self):
        result = self.run_source(
            "module app; struct Point { i32 x; } "
            "void change(& /* out */ inout Point point) { point.x = 2; } "
            "void read(& /* inout */ in Point point) { point.x = 3; }",
            "--check-types", "--diagnostic-format", "stable",
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(1, result.stderr.count("type.invalid-assignment-target"))

    def test_syntax_qualifiers_have_exact_tokens_and_typed_modes(self):
        from tests.bootstrap_syntax_normalization import read_dump

        source = ("module app; struct Point { void read(& /* trivia */ in self) {} } "
                  "void use(&out i32 a, *in i32 b, ?*inout i32 c) {}")
        result = self.run_source(source, "--dump", "syntax")
        self.assertEqual(0, result.returncode, result.stderr)
        nodes, _ = read_dump(result.stdout)
        qualifiers = [node for node in nodes.values() if "access" in node.values]
        self.assertEqual(4, len(qualifiers))
        for node in qualifiers:
            start, end = node.fields["mode"]
            self.assertEqual(node.values["access"], source[start:end])

    def test_concrete_member_targets_retain_original_declaration_spans(self):
        source = ("module app; struct Point { i32 x; "
                  "i32 read(&in self) { return self.x; } } "
                  "i32 caller(&in Point point) { return point.read(); }")
        result = self.run_source(source, "--dump", "types")
        self.assertEqual(0, result.returncode, result.stderr)
        targets = [line.split("\t") for line in result.stdout.splitlines()
                   if line.startswith("target\t") and not line.endswith("\tbuiltin")]
        declaration_names = {source[int(row[-2]):int(row[-1])] for row in targets}
        self.assertTrue({"read", "x"}.issubset(declaration_names), result.stdout)

    def test_typed_mode_is_exclusive(self):
        result = self.run_source(
            "module app;", "--check-types", "--dump", "types",
        )
        self.assertEqual(2, result.returncode)

    def test_comptime_body_is_not_executed(self):
        result = self.run_source(
            'module app; comptime print("SHOULD_NOT_RUN");',
            "--check-types",
        )
        self.assertEqual(3, result.returncode)
        self.assertNotIn("SHOULD_NOT_RUN", result.stdout + result.stderr)

    def test_pending_checks_have_one_deterministic_record(self):
        result = self.run_source(
            'module app; comptime print("NOT_EXECUTED");',
            "--dump", "types", "--diagnostic-format", "stable",
        )
        self.assertEqual(3, result.returncode)
        pending = [line for line in result.stdout.splitlines()
                   if line.startswith("pending\t")]
        self.assertEqual(1, len(pending), result.stdout)
        self.assertIn("\texpression\t", pending[0])
        self.assertNotIn("NOT_EXECUTED", result.stdout + result.stderr)

    def test_match_return_has_an_explicit_pending_result(self):
        result = self.run_source('''
            module app;
            union Choice { first; second; }
            i32 choose(&in Choice value) {
                return match (value) { .first => 1, .second => 2, };
            }
        ''', "--dump", "types", "--diagnostic-format", "stable")
        self.assertEqual(3, result.returncode, result.stderr)
        self.assertIn("dependent_match", result.stdout)
        self.assertNotIn("error", result.stderr)

    def test_generic_struct_literal_reuses_its_type_expression_result(self):
        result = self.run_source('''
            module app;
            struct Box(comptime type T: Copyable) { T item; }
            void build() { Box(i32) value = Box(i32) { item = 1 }; }
        ''', "--dump", "types", "--diagnostic-format", "stable")
        self.assertIn(result.returncode, (0, 3), result.stderr)
        self.assertNotIn("project.invalid-reference", result.stderr)

    def test_std_memory_generic_value_arguments(self):
        result = subprocess.run(
            [str(self.executable), "--check-types", "--module-root",
             str(ROOT / "jack"), str(ROOT / "jack/std/memory.jack")],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(3, result.returncode, result.stderr)
        self.assertNotIn("error[", result.stderr)


if __name__ == "__main__":
    unittest.main()
