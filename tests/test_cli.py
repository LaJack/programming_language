import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.cli import build_parser, main


class CliTests(unittest.TestCase):
    def test_parser_accepts_compact_optimization_flags(self):
        for level in range(4):
            with self.subTest(level=level):
                args = build_parser().parse_args([f'-O{level}', 'program.jack'])
                self.assertEqual(level, args.optimization)

    def test_parser_accepts_debug_flag(self):
        args = build_parser().parse_args(['-g', 'program.jack'])

        self.assertTrue(args.debug)

    def test_interpreter_mode_runs_source_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('''
                comptime i32 offset;
                comptime offset = 2;
                comptime offset = offset + 5;
                i32 y = offset;
                print(y);
            ''')

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual('y = 7\n', output.getvalue())

    def test_interpreter_mode_runs_comptime_prints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('''
                comptime i32 offset = 7;
                comptime print(offset);
                i32 y = offset;
                print(y);
            ''')

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual('offset = 7\ny = 7\n', output.getvalue())

    def test_c_mode_emits_c_source_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('''\n                comptime i32 offset;\n                comptime offset = 7;\n                i32 y = offset;\n                print(y);\n            ''')

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-c', str(source)])

        self.assertEqual(0, status)
        self.assertIn('#include "jack_runtime.h"', output.getvalue())
        self.assertIn('    y = 7;', output.getvalue())
        self.assertIn('    printf("y = %" PRId32 "\\n", (int32_t)(y));', output.getvalue())

    def test_c_mode_with_output_directory_writes_split_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            source = root / 'program.jk'
            source.write_text('''
                import math.ops;

                i32 y = add(2, 3);
            ''')
            output_dir = root / 'c_out'

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-c', str(source), '-o', str(output_dir)])

            main_c = output_dir / 'main.c'
            module_c = output_dir / 'math_ops.c'
            module_h = output_dir / 'math_ops.h'

            self.assertEqual(0, status)
            self.assertEqual('', output.getvalue())
            self.assertTrue(main_c.is_file())
            self.assertTrue(module_c.is_file())
            self.assertTrue(module_h.is_file())
            self.assertTrue((output_dir / 'jack_runtime.h').is_file())
            self.assertTrue((output_dir / 'jack_std_io.c').is_file())
            self.assertIn('#include "math_ops.h"', main_c.read_text())
            self.assertIn('int32_t math_ops_add(int32_t left, int32_t right) {', module_c.read_text())

    def test_c_mode_sends_comptime_prints_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('''
                comptime i32 offset = 7;
                comptime print(offset);
                i32 y = offset;
            ''')

            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                status = main(['-c', str(source)])

        self.assertEqual(0, status)
        self.assertIn('#include "jack_runtime.h"', output.getvalue())
        self.assertIn('    y = 7;', output.getvalue())
        self.assertNotIn('offset = 7', output.getvalue())
        self.assertEqual('offset = 7\n', errors.getvalue())

    def test_default_mode_builds_an_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jk'
            executable = root / 'program'
            source.write_text('''
                comptime i32 offset = 7;
                i32 y = offset;
                print(y);
            ''')

            status = main([str(source), '-o', str(executable)])
            completed = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )

        self.assertEqual(0, status)
        self.assertEqual(0, completed.returncode)
        self.assertEqual('y = 7\n', completed.stdout)

    def test_default_llvm_build_resolves_imported_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;
                pub i32 add(i32 left, i32 right) { return left + right; }
            ''')
            source = root / 'program.jack'
            source.write_text('''
                import math.ops;
                i32 value = add(5, 6);
                print(value);
            ''')
            executable = root / 'program'
            temps = root / 'temps'

            status = main([
                str(source), '-o', str(executable), '--save-temps', str(temps)
            ])
            completed = subprocess.run(
                [str(executable)], capture_output=True, text=True, check=False
            )
            llvm_exists = (temps / 'main.ll').is_file()

        self.assertEqual(0, status)
        self.assertEqual('value = 11\n', completed.stdout)
        self.assertTrue(llvm_exists)

    def test_native_mode_can_save_c_backend_temps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / 'program.jk'
            executable = root / 'program'
            temps = root / 'temps'
            source.write_text('i32 y = 3;')

            status = main([
                '--backend', 'c',
                '--save-temps', str(temps),
                '-o', str(executable),
                str(source),
            ])
            executable_exists = executable.is_file()
            main_c_exists = (temps / 'main.c').is_file()
            runtime_exists = (temps / 'jack_runtime.h').is_file()

        self.assertEqual(0, status)
        self.assertTrue(executable_exists)
        self.assertTrue(main_c_exists)
        self.assertTrue(runtime_exists)

    def test_interpreter_rejects_native_output_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('i32 y = 3;')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', '-o', str(Path(tmpdir) / 'out'), str(source)])

        self.assertNotEqual(0, status)
        self.assertIn('--output cannot be used with -i/--interpret', errors.getvalue())

    def test_interpreter_rejects_debug_information(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jack'
            source.write_text('i32 value = 7;')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', '-g', str(source)])

        self.assertEqual(1, status)
        self.assertIn('--debug cannot be used with -i/--interpret', errors.getvalue())

    def test_c_mode_debug_output_contains_source_directives(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jack'
            source.write_text('i32 value = 7;')

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-c', '-g', str(source)])

        self.assertEqual(0, status)
        self.assertIn(f'#line 1 "{source.resolve()}"', output.getvalue())

    def test_native_mode_reports_missing_clang(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('i32 y = 3;')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['--cc', 'missing-jack-clang', str(source)])

        self.assertEqual(1, status)
        self.assertIn('Cannot find Clang executable', errors.getvalue())

    def test_interpreter_mode_runs_stdio_extern_bindings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('''
                extern "c" type FILE;
                extern "c" &inout FILE stdout;
                extern "c" usize fwrite(&in c_void data, usize size, usize count, &inout FILE stream);

                u8[3] message;
                message[0] = 104;
                message[1] = 105;
                message[2] = 10;

                usize written = fwrite(&in message[0], 1, len(message), stdout);
                print(written);
            ''')

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual('hi\nwritten = 3\n', output.getvalue())

    def test_interpreter_error_reports_source_span(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('i32 value = 1;\n&in i32 ref = &in value;\nvalue = 2;\n')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', str(source)])

        self.assertEqual(1, status)
        diagnostic = errors.getvalue()
        self.assertIn(f'jack: {source}:3:1:', diagnostic)
        self.assertIn('overlaps live &in borrow', diagnostic)
        self.assertIn('value = 2;', diagnostic)
        self.assertIn('^', diagnostic)

    def test_comptime_error_reports_source_span(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('comptime i32 value = missing;\n')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', str(source)])

        self.assertEqual(1, status)
        diagnostic = errors.getvalue()
        self.assertIn(f'jack: {source}:1:1:', diagnostic)
        self.assertIn('Comptime expression references runtime name "missing"', diagnostic)
        self.assertIn('comptime i32 value = missing;', diagnostic)
        self.assertIn('^', diagnostic)

    def test_parse_error_reports_source_span(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'program.jk'
            source.write_text('i32 value = ;\n')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', str(source)])

        self.assertEqual(1, status)
        diagnostic = errors.getvalue()
        self.assertIn(f'jack: {source}:1:13:', diagnostic)
        self.assertIn('Expected expression.', diagnostic)
        self.assertIn('i32 value = ;', diagnostic)
        self.assertIn('^', diagnostic)

    def test_imported_parse_error_reports_imported_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'broken.jack'
            module.write_text('module broken;\ni32 value = ;\n')
            source = root / 'main.jack'
            source.write_text('import broken;\n')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', str(source)])

        self.assertEqual(1, status)
        diagnostic = errors.getvalue()
        self.assertIn(f'jack: {module.resolve()}:2:13:', diagnostic)
        self.assertIn('i32 value = ;', diagnostic)

    def test_imported_semantic_error_reports_imported_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'broken.jack'
            module.write_text('''
                module broken;
                pub i32 value() {
                    bool flag = true;
                    return flag + flag;
                }
            ''')
            source = root / 'main.jack'
            source.write_text('import broken;\n')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['-i', str(source)])

        self.assertEqual(1, status)
        diagnostic = errors.getvalue()
        self.assertIn(f'jack: {module.resolve()}:5:21:', diagnostic)
        self.assertIn('return flag + flag;', diagnostic)

    def test_stubbed_module_error_reports_stub_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stub = root / 'tests' / 'stubs' / 'device.jack'
            stub.parent.mkdir(parents=True)
            stub.write_text('module tests.stubs.device;\ncomptime i32 value = missing;\n')
            source = root / 'main.jack'
            source.write_text('import hw.device;\n')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main([
                    '--module-root', str(root),
                    '--stub', 'hw.device=tests.stubs.device',
                    '-i', str(source),
                ])

        self.assertEqual(1, status)
        diagnostic = errors.getvalue()
        self.assertIn(f'jack: {stub.resolve()}:2:1:', diagnostic)
        self.assertIn('comptime i32 value = missing;', diagnostic)


if __name__ == '__main__':
    unittest.main()
