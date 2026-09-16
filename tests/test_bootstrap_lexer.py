import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.parser import Lexer, ParseError, parse
from tests.bootstrap_syntax_normalization import (
    bootstrap_nodes, first_difference, python_node, read_dump, root_source_ranges,
)
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
        cls.compilation_results = {}
        for backend in ('c', 'llvm'):
            output = cls.root / f'bootstrap-{backend}'
            cls.compilation_results[backend] = driver.compile_executable(
                ENTRY,
                CompilationOptions(
                    backend=backend,
                    output=output,
                    module_roots=(SELFHOST_ROOT,),
                ),
            )
            cls.executables[backend] = output
        for backend in ('llvm', 'c'):
            optimized = cls.root / f'bootstrap-{backend}-o2'
            driver.compile_executable(
                ENTRY,
                CompilationOptions(
                    backend=backend, output=optimized, optimization=2,
                    module_roots=(SELFHOST_ROOT,),
                ),
            )
            cls.executables[f'{backend}-o2'] = optimized

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_interpreter(self, path: Path | None, *arguments: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = Interpreter(
                externs=default_runtime_externs()
            ).eval_hir_program(
                self.program,
                [
                    'jack-bootstrap',
                    *arguments,
                    *([] if path is None else [str(path)]),
                ],
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def run_native(self, backend: str, *arguments: str):
        result = subprocess.run(
            [str(self.executables[backend]), *arguments],
            capture_output=True,
            text=True,
            timeout=20,
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
            path,
            expected,
            status=1,
            expected_stderr=(
                'error[lex.invalid-token]: invalid token\n'
                f' --> {path}:1:1\n'
                '  |\n'
                '1 | @ valid\n'
                '  | ^\n'
            ),
        )
        stable = (
            'diagnostic\t0\terror\tlex.invalid-token\t'
            f'{path}\t0\t1\t1\t1\tinvalid token\n'
        )
        results = [
            self.run_interpreter(path, '--diagnostic-format', 'stable')
        ]
        results.extend(
            self.run_native(
                backend, '--diagnostic-format', 'stable', str(path)
            )
            for backend in ('c', 'llvm')
        )
        for result in results:
            self.assertEqual((1, expected, stable), result)

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
            self.assertEqual((2, '', 'usage: jack-bootstrap [--diagnostic-format human|stable] [--dump tokens|syntax] <source>\n'),
                             (status, stdout, stderr))
            status, stdout, stderr = self.run_native(
                backend, str(self.root / 'missing.jack')
            )
            self.assertEqual(1, status)
            self.assertEqual('', stdout)
            self.assertRegex(
                stderr,
                r'^error\[io\.operation\]: I/O operation failed with code [0-9]+\n$',
            )

    def test_invalid_utf8_source_reports_byte_offset(self):
        path = self.root / 'invalid-utf8.jack'
        path.write_bytes(b'ok \xff')
        for backend in ('c', 'llvm'):
            status, stdout, stderr = self.run_native(backend, str(path))
            self.assertEqual(
                (
                    1,
                    '',
                    'error[source.invalid-utf8]: invalid UTF-8 at byte offset 3\n',
                ),
                (status, stdout, stderr),
            )

    def test_valid_utf8_uses_byte_offsets_and_columns(self):
        path = self.root / 'utf8.jack'
        path.write_text('\u00e9')
        expected = (
            '6\t0\t1\t1\t1\n'
            '6\t1\t2\t1\t2\n'
            '0\t2\t2\t1\t3\n'
        )
        self.assert_runtime_parity(
            path,
            expected,
            status=1,
            expected_stderr=(
                'error[lex.invalid-token]: invalid token\n'
                f' --> {path}:1:1\n'
                '  |\n'
                '1 | é\n'
                '  | ^\n'
            ),
        )

    def test_optimized_llvm_matches_bootstrap_output(self):
        path = ROOT / 'examples' / 'vector.jack'
        expected = expected_dump(path.read_text())
        self.assertEqual((0, expected, ''), self.run_native('llvm-o2', str(path)))

    def test_token_specification_is_a_recorded_comptime_dependency(self):
        expected = (SELFHOST_ROOT / 'bootstrap' / 'jack.tokens').resolve()
        for backend, result in self.compilation_results.items():
            with self.subTest(backend=backend):
                self.assertIn(expected, result.comptime_dependencies)

    def test_syntax_dump_preserves_precedence_across_runtimes(self):
        path = self.root / 'syntax.jack'
        path.write_text('module demo;\ni32 value = 1 + 2 * 3;\n')
        results = [self.run_interpreter(path, '--dump', 'syntax')]
        results.extend(
            self.run_native(backend, '--dump', 'syntax', str(path))
            for backend in ('c', 'llvm', 'llvm-o2', 'c-o2')
        )
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual(results[0], results[3])
        self.assertEqual(results[0], results[4])
        status, output, errors = results[0]
        self.assertEqual((0, ''), (status, errors))
        self.assertIn('node\t0\tmodule_declaration\t0\t12\n', output)
        self.assertIn('node\t3\tbinary_expression\t25\t34\n', output)
        self.assertIn('field\t3\toperator\t27\t28\n', output)
        self.assertIn('field\t5\toperator\t31\t32\n', output)
        self.assertEqual(python_node(parse(path.read_text())), bootstrap_nodes(path.read_text(), output))

    def test_syntax_recovery_retains_later_declarations(self):
        path = self.root / 'recover.jack'
        path.write_text('i32 first = ;\ni32 second = 2;\n')
        status, output, errors = self.run_native(
            'llvm', '--diagnostic-format', 'stable', '--dump', 'syntax', str(path)
        )
        self.assertEqual(1, status)
        self.assertEqual(2, output.count('root\t'))
        self.assertIn('invalid_expression', output)
        self.assertIn('field\t3\tname\t18\t24\n', output)
        self.assertIn('parse.expected-expression', errors)

    def test_syntax_mode_parses_checked_in_jack_sources(self):
        paths = [
            *sorted((ROOT / 'examples').glob('*.jack')),
            *sorted((ROOT / 'jack' / 'std').glob('*.jack')),
            *sorted((ROOT / 'jack' / 'std' / 'collections').glob('*.jack')),
            *sorted((SELFHOST_ROOT / 'bootstrap').glob('*.jack')),
        ]
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                status, output, errors = self.run_native(
                    'llvm', '--dump', 'syntax', str(path)
                )
                self.assertEqual((0, ''), (status, errors))
                source = path.read_text()
                expected = python_node(parse(source))
                actual = bootstrap_nodes(source, output)
                self.assertIsNone(first_difference(expected, actual))
                expected_ranges, actual_ranges = root_source_ranges(source, parse(source), output)
                self.assertEqual(expected_ranges, actual_ranges)

    def test_structured_grammar_fixtures_match_python(self):
        sources = [
            '{ { } i32 local = 1; } comptime { i32 count = 0; { count = count + 1; } }',
            'i32 value = project.file(id).nodes()[index..end][0].span().start; values.get(index).field = replacement();',
            'import protocol.frame as frame; import std.memory.{Allocation, Layout};',
            'void f() { for (;;) { return; } for (i32 i = 0; i < 3; i = i + 1) { print(i); } }',
            'void f() { try { return; } catch Option(i32) error { rethrow; } }',
            'void f() { i32 x = match (move value) { .some(item) => move item, .none => 0, }; }',
            'void f() { match (&inout value) { .some(item, _) { item = 1; } _ { } } }',
            'i32 a = 1 | 2 ^ 3 & 4 == 5 << 6 + 7 * 8; i32 b = ~a[0];',
            '&in u8[] whole = &in values[..]; &in u8[] prefix = &in values[..2];',
            'Box(N + 1, u8[4], &in T) box; interface Copier { init(&out self, &in Self other); }',
            'comptime print(1); comptime for (i32 i = 0; i < 3; i = i + 1) { print(i); }',
            'for (comptime i32 i = 0; i < 3; comptime i = i + 1) { print(i); }',
            'struct Generic(comptime type T: Copyable + Printable) { T value; }',
            'pub const i32[2] values = comptime make_values();',
            'pub unsafe extern "c" ?*inout u8 allocate(usize size);',
            'unsafe void update(*inout i32 pointer) { *pointer = 42; }',
            'print(f"value {len("hello")} {{braces}} {f"nested {1}"}");',
        ]
        for index, source in enumerate(sources):
            with self.subTest(source=source):
                path = self.root / f'grammar-{index}.jack'
                path.write_text(source)
                expected = python_node(parse(source))
                status, output, errors = self.run_native('llvm', '--dump', 'syntax', str(path))
                self.assertEqual((0, ''), (status, errors))
                self.assertIsNone(first_difference(expected, bootstrap_nodes(source, output)))

    def test_recovered_trees_remain_valid_across_runtimes(self):
        source = 'void f(i32 a,,i32 b) { i32 x = (1 + ); print(2); } i32 later = 3;'
        path = self.root / 'damaged.jack'
        path.write_text(source)
        results = [self.run_interpreter(path, '--dump', 'syntax', '--diagnostic-format', 'stable')]
        results.extend(self.run_native(backend, '--dump', 'syntax', '--diagnostic-format', 'stable', str(path))
                       for backend in ('llvm', 'c', 'llvm-o2', 'c-o2'))
        for result in results:
            self.assertEqual(results[0], result)
            self.assertEqual(1, result[0])
            self.assertIn('invalid_expression', result[1])
            self.assertNotIn('syntax.invalid-tree', result[2])
            self.assertEqual(2, result[1].count('root\t'))

    def test_malformed_grammar_and_deterministic_mutations(self):
        malformed = [
            'void f() { try { return; } }', 'pub pub i32 value;', 'const i32 value;',
            'i32 if = 1;', 'import a.{B,};', '*out i32 pointer;',
            'void f() { match (&in value) { .some(x,) { } } }',
            'void f() { match (&in value) { .some(x) => x, .none { } } }',
            'void f(i32 a,,i32 b) { return; }', 'void f() { print(1,); print(2); }',
            'print(f"bad {1 + }");', 'print(f"bad } text");',
            'comptime comptime print(1);', 'pub import a;', 'unsafe struct Bad { }',
            'print 1;', 'void f() { raise; }', 'i32 value = 1; value;', '*i32 pointer;',
        ]
        valid = 'void f(i32 value) { if (value > 0) { print(value); } } i32 tail = 2;'
        for token in Lexer(valid).tokenize():
            if token.kind in {'(', ')', '{', '}', ';'}:
                mutated = valid[:token.offset] + valid[token.end_offset:]
                try:
                    parse(mutated)
                except ParseError:
                    malformed.append(mutated)
        for index, source in enumerate(malformed):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source)
                path = self.root / f'malformed-{index}.jack'
                path.write_text(source)
                status, output, errors = self.run_native(
                    'llvm', '--dump', 'syntax', '--diagnostic-format', 'stable', str(path))
                self.assertEqual(1, status)
                self.assertIn('diagnostic\t', errors)
                self.assertNotIn('syntax.invalid-tree', errors)
                self.assertTrue(output)

    def test_nested_formatted_strings_have_absolute_utf8_spans(self):
        source = '// heading\r\n\tprint(f"cafe\u00e9 {len("hi")} {{ok}}");\n'
        path = self.root / 'formatted-utf8.jack'
        path.write_bytes(source.encode())
        result = self.run_native('llvm', '--dump', 'syntax', str(path))
        self.assertEqual((0, ''), (result[0], result[2]))
        self.assertEqual(python_node(parse(source)), bootstrap_nodes(source, result[1]))
        nodes, _ = read_dump(result[1])
        calls = [node for node in nodes.values() if node.kind == 'call_expression']
        start = source.encode().index(b'len(')
        self.assertEqual([(start, start + len(b'len("hi")'))], [(node.start, node.end) for node in calls])

    def test_default_nesting_limit_and_diagnostic_cap(self):
        path = self.root / 'nesting.jack'
        path.write_text('i32 value = ' + '(' * 150 + '1' + ')' * 150 + ';')
        status, output, errors = self.run_native(
            'llvm', '--dump', 'syntax', '--diagnostic-format', 'stable', str(path))
        self.assertEqual(1, status)
        self.assertIn('parse.nesting-limit', errors)
        self.assertTrue(output)
        path.write_text('\n'.join(f'i32 value{i} = ;' for i in range(30)))
        status, output, errors = self.run_native(
            'llvm', '--dump', 'syntax', '--diagnostic-format', 'stable', str(path))
        self.assertEqual(1, status)
        self.assertEqual(20, errors.count('diagnostic\t'))
        self.assertIn('omitted\t', errors)
        self.assertEqual(30, output.count('root\t'))

    def test_dump_options_are_order_independent_and_reject_duplicates(self):
        path = self.root / 'options.jack'
        path.write_text('i32 value = 1;')
        first = self.run_native(
            'llvm', '--dump', 'syntax', '--diagnostic-format', 'stable', str(path)
        )
        second = self.run_native(
            'llvm', str(path), '--diagnostic-format', 'stable', '--dump', 'syntax'
        )
        self.assertEqual(first, second)
        duplicate = self.run_native(
            'llvm', '--dump', 'tokens', '--dump', 'syntax', str(path)
        )
        self.assertEqual(2, duplicate[0])
        for options in [('--unknown',), ('--dump', 'wrong', str(path)),
                        ('--diagnostic-format', 'stable', '--diagnostic-format', 'human', str(path))]:
            self.assertEqual(2, self.run_native('llvm', *options)[0])

    def test_generated_lexer_supports_all_bitwise_symbols(self):
        path = self.root / 'bitwise.jack'
        source = 'a && b || c << 1 >> 1 | d ^ ~e;'
        path.write_text(source)
        self.assert_runtime_parity(path, expected_dump(source))


if __name__ == '__main__':
    unittest.main()
