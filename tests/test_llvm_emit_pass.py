import subprocess
import tempfile
import unittest
from pathlib import Path

from jack.cleanup_lowering_pass import lower_hir_static_cleanups
from jack.comptime_externs import default_comptime_externs
from jack.hir_lowering_pass import compile_to_hir
from jack.llvm_emit_pass import emit_hir_llvm
from jack.parser import parse


class LLVMEmitPassTests(unittest.TestCase):
    def emit(
        self,
        source: str,
        *,
        source_path: Path | None = None,
        debug: bool = False,
        optimization: int = 0,
    ) -> str:
        program = lower_hir_static_cleanups(
            compile_to_hir(
                parse(source, source_path=source_path),
                print_handler=None,
                externs=default_comptime_externs(),
            )
        )
        return emit_hir_llvm(
            program, debug=debug, optimization=optimization
        )

    def compile_and_run(self, source: str) -> subprocess.CompletedProcess[str]:
        llvm_source = self.emit(source)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'main.ll'
            executable = root / 'program'
            module.write_text(llvm_source)
            compiled = subprocess.run(
                [
                    'clang',
                    str(module),
                    str(Path(__file__).parents[1] / 'jack/c_runtime/jack_std_io.c'),
                    '-I',
                    str(Path(__file__).parents[1] / 'jack/c_runtime'),
                    '-o',
                    str(executable),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, compiled.returncode, compiled.stderr + '\n' + llvm_source)
            return subprocess.run(
                [str(executable)],
                cwd=Path(__file__).parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_scalar_functions_compile_and_run(self):
        completed = self.compile_and_run('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 value = add(2, 3);
            print(value);
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('value = 5\n', completed.stdout)

    def test_struct_methods_constructors_and_cleanup_compile_and_run(self):
        completed = self.compile_and_run('''
            struct Counter {
                i32 value;

                init(&inout self, i32 value) {
                    self.value = value;
                }

                void add(&inout self, i32 amount) {
                    self.value = self.value + amount;
                }

                deinit(&inout self) {
                    print(self.value);
                }
            }

            void run() {
                Counter counter(3);
                counter.add(4);
            }

            run();
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('self.value = 7\n', completed.stdout)

    def test_arrays_slices_borrows_and_loops_compile_and_run(self):
        completed = self.compile_and_run('''
            void fill(&out i32[] values) {
                for (i32 i = 0; i < len(values); i = i + 1) {
                    values[i] = i + 1;
                }
            }

            i32[3] values;
            fill(values[..]);
            i32 total = values[0] + values[1] + values[2];
            print(total);
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('total = 6\n', completed.stdout)

    def test_dynamic_slice_bounds_compile_and_run(self):
        completed = self.compile_and_run('''
            i32[5] values;
            i32 start = 1;
            i32 end = 4;
            &inout i32[] middle = &inout values[start..end];
            middle[0] = 12;
            print(middle[0]);
            print(len(middle));
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('middle[0] = 12\nlen(middle) = 3\n', completed.stdout)

    def test_strings_formatting_and_conversions_compile_and_run(self):
        completed = self.compile_and_run('''
            str word = "jack";
            bool equal = word == "jack";
            u8 small = u8(7);
            i64 wide = i64(small);
            print(f"{word} {equal} {wide}");
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('jack true 7\n', completed.stdout)

    def test_raising_functions_and_catches_compile_and_run(self):
        completed = self.compile_and_run('''
            struct AccessError {
                i32 code;
            }

            i32 fail() raises AccessError {
                raise AccessError { code = 42 };
            }

            try {
                i32 value = fail();
                print(value);
            } catch AccessError error {
                print(error.code);
            }
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('error.code = 42\n', completed.stdout)

    def test_scalar_borrows_and_views_compile_and_run(self):
        completed = self.compile_and_run('''
            struct Packet {
                i32 header;
                i32 checksum;
            }

            view PacketView {
                in i32 header;
                out i32 checksum;
            }

            u8 max_value(&in u8 left, &in u8 right) {
                if (left > right) {
                    return left;
                }
                return right;
            }

            void update(&inout PacketView packet) {
                packet.checksum = packet.header + 1;
            }

            u8 left = 8;
            u8 right = 9;
            u8 biggest = max_value(left, right);
            Packet packet;
            packet.header = 41;
            update(packet);
            print(biggest);
            print(packet.checksum);
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('biggest = 9\npacket.checksum = 42\n', completed.stdout)

    def test_global_cleanup_runs_before_process_exit(self):
        completed = self.compile_and_run('''
            struct Tracer {
                i32 value;

                deinit(&inout self) {
                    print(self.value);
                }
            }

            Tracer tracer;
            tracer.value = 17;
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('self.value = 17\n', completed.stdout)

    def test_successful_raising_value_and_multiple_error_types(self):
        completed = self.compile_and_run('''
            struct FirstError { i32 code; }
            struct SecondError { i32 code; }

            i32 value(bool fail) raises FirstError, SecondError {
                if (fail) {
                    raise SecondError { code = 8 };
                }
                return 23;
            }

            try {
                i32 result = value(false);
                print(result);
                i32 ignored = value(true);
                print(ignored);
            } catch FirstError error {
                print(error.code);
            } catch SecondError error {
                print(error.code);
            }
        ''')

        self.assertEqual(0, completed.returncode)
        self.assertEqual('result = 23\nerror.code = 8\n', completed.stdout)

    def test_error_payloads_are_inline_and_deterministically_ordered(self):
        llvm_source = self.emit('''
            struct ZebraError { i32 code; }
            struct AlphaError { i32 code; }

            void fail() raises ZebraError, AlphaError {
                raise ZebraError { code = 4 };
            }

            try {
                fail();
            } catch AlphaError error {
                print(error.code);
            } catch ZebraError error {
                print(error.code);
            }
        ''')

        self.assertIn(
            '%jack.error.payload = type { %"AlphaError", %"ZebraError" }',
            llvm_source,
        )
        self.assertIn('insertvalue %jack.error.payload zeroinitializer', llvm_source)
        self.assertIn('extractvalue %jack.error.payload', llvm_source)
        self.assertNotIn('@malloc', llvm_source)

    def test_all_allocas_are_emitted_in_function_entry_blocks(self):
        llvm_source = self.emit('''
            void repeat() {
                i32 index = 0;
                while (index < 3) {
                    i32 local = index;
                    print(local);
                    index = index + 1;
                }
            }
            repeat();
        ''')

        current_label = None
        for line in llvm_source.splitlines():
            stripped = line.strip()
            if stripped.endswith(':'):
                current_label = stripped[:-1]
            elif ' = alloca ' in stripped:
                self.assertEqual('entry', current_label, stripped)
        self.assertRegex(
            llvm_source,
            r'while\.body\.\d+:\n(?:  .*\n)*?  store i32 zeroinitializer, ptr %local',
        )

    def test_debug_ir_contains_deterministic_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'program.jack'
            source = '''
                i32 add(i32 left, i32 right) {
                    return left + right;
                }
                i32 value = add(2, 3);
                print(value);
            '''
            source_path.write_text(source)
            release_ir = self.emit(source, source_path=source_path)
            debug_ir = self.emit(
                source, source_path=source_path, debug=True
            )
            repeated_ir = self.emit(
                source, source_path=source_path, debug=True
            )

        self.assertNotIn('!llvm.dbg.cu', release_ir)
        self.assertNotIn('!dbg', release_ir)
        self.assertEqual(debug_ir, repeated_ir)
        self.assertIn('!llvm.dbg.cu', debug_ir)
        self.assertIn('!DICompileUnit(language: DW_LANG_C11', debug_ir)
        self.assertIn('!DIFile(filename: "program.jack"', debug_ir)
        self.assertIn('!DISubprogram(name: "add"', debug_ir)
        self.assertIn('!DILocation(', debug_ir)
        self.assertIn('isOptimized: false', debug_ir)

    def test_optimized_debug_ir_is_accepted_by_clang(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_path = root / 'program.jack'
            source_path.write_text('i32 value = 7;\nprint(value);\n')
            llvm_source = self.emit(
                source_path.read_text(),
                source_path=source_path,
                debug=True,
                optimization=2,
            )
            module = root / 'main.ll'
            output = root / 'main.o'
            module.write_text(llvm_source)
            completed = subprocess.run(
                ['clang', '-x', 'ir', '-c', str(module), '-o', str(output)],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(0, completed.returncode, completed.stderr + llvm_source)
        self.assertIn('isOptimized: true', llvm_source)

    def test_debug_ir_describes_parameters_locals_and_aggregate_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'locals.jack'
            source = '''
                struct Point {
                    i32 x;
                    init(&inout self, i32 value) { self.x = value; }
                }

                i32 inspect(i32 input) {
                    Point point(input);
                    u8[2] bytes;
                    &inout u8[] slice = &inout bytes[..];
                    if (input == 1) {
                        i32 nested = point.x;
                        return nested;
                    }
                    return point.x;
                }

                i32 result = inspect(1);
                print(result);
            '''
            source_path.write_text(source)
            llvm_source = self.emit(
                source, source_path=source_path, debug=True
            )

        self.assertIn('declare void @llvm.dbg.declare', llvm_source)
        self.assertIn('!DILocalVariable(name: "input", arg: 1', llvm_source)
        self.assertIn('!DILocalVariable(name: "point"', llvm_source)
        self.assertIn('!DILocalVariable(name: "bytes"', llvm_source)
        self.assertIn('!DILocalVariable(name: "slice"', llvm_source)
        self.assertIn('!DILocalVariable(name: "nested"', llvm_source)
        self.assertIn('!DICompositeType(tag: DW_TAG_structure_type, name: "Point"', llvm_source)
        self.assertIn('!DICompositeType(tag: DW_TAG_array_type', llvm_source)
        self.assertIn('!DIDerivedType(tag: DW_TAG_pointer_type', llvm_source)
        self.assertIn('distinct !DILexicalBlock(', llvm_source)
        self.assertNotIn('jack_cleanup_return_value', llvm_source)

if __name__ == '__main__':
    unittest.main()
