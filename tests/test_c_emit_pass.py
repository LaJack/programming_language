import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.c_emit_pass import CEmitError, CEmitPass, emit_c, emit_hir_c
from jack.cleanup_lowering_pass import lower_hir_static_cleanups
from jack.hir_lowering_pass import compile_to_hir
from jack.parser import parse
from jack.semantic_pass import SemanticError


class CEmitPassTests(unittest.TestCase):
    def test_debug_emission_adds_source_line_directives_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = Path(tmpdir) / 'program.jack'
            source = 'i32 value = 7;\nprint(value);\n'
            source_path.write_text(source)
            program = lower_hir_static_cleanups(
                compile_to_hir(
                    parse(source, source_path=source_path), print_handler=None
                )
            )

            release_source = emit_hir_c(program)
            debug_source = emit_hir_c(program, debug=True)

        directive = f'#line 1 "{source_path.resolve()}"'
        self.assertNotIn('#line', release_source)
        self.assertIn(directive, debug_source)
        self.assertIn(f'#line 2 "{source_path.resolve()}"', debug_source)

    def test_emits_lowered_hir_without_ast_side_tables(self):
        program = lower_hir_static_cleanups(compile_to_hir(parse('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 value = add(2, 3);
        '''), print_handler=None))

        c_source = emit_hir_c(program)

        self.assertIn('int32_t add(int32_t left, int32_t right)', c_source)
        self.assertIn('value = add(2, 3);', c_source)

    def test_emits_c_after_compile_time_pass(self):
        source = '''
            comptime i32 offset;
            comptime offset = 2;
            comptime offset = offset + 5;
            i32 y = offset;
            print(y);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('#include "jack_runtime.h"', c_source)
        self.assertIn('int32_t y;', c_source)
        self.assertIn('int main(void) {', c_source)
        self.assertIn('    y = 7;', c_source)
        self.assertIn('    printf("y = %" PRId32 "\\n", (int32_t)(y));', c_source)
        self.assertNotIn('comptime', c_source)

    def test_emits_c_after_comptime_struct_field_substitution(self):
        source = '''
            struct Point {
                i32 x;
                i32 y;
            }

            comptime Point point;
            comptime point.x = 3;
            comptime point.y = point.x + 4;

            i32 y = point.y;
            print(point.x);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('typedef struct Point {', c_source)
        self.assertIn('int32_t y;', c_source)
        self.assertIn('y = 7;', c_source)
        self.assertIn('printf("point.x = %" PRId32 "\\n", (int32_t)(3));', c_source)
        self.assertNotIn('comptime', c_source)

    def test_emit_c_suppresses_comptime_prints_by_default(self):
        source = '''
            comptime i32 offset = 7;
            comptime print(offset);
            i32 y = offset;
        '''

        output = io.StringIO()
        with redirect_stdout(output):
            c_source = emit_c(parse(source))

        self.assertEqual('', output.getvalue())
        self.assertNotIn('offset = 7', c_source)
        self.assertNotIn('printf("offset =', c_source)

    def test_emit_c_can_report_comptime_prints_to_a_handler(self):
        source = '''
            comptime i32 offset = 7;
            comptime print(offset);
            i32 y = offset;
        '''

        messages: list[str] = []
        c_source = emit_c(parse(source), print_handler=messages.append)

        self.assertEqual(['offset = 7'], messages)
        self.assertIn('y = 7;', c_source)

    def test_mangles_generated_function_variant_names(self):
        source = '''
            i32 add_offset(comptime i32 offset, i32 value) {
                return offset + value;
            }

            i32 y = add_offset(3, 4);
        '''

        c_source = emit_c(parse(source))

        self.assertIn(
            'int32_t add_offset_comptime_offset_3(int32_t value)',
            c_source,
        )
        self.assertIn(
            'y = add_offset_comptime_offset_3(4);',
            c_source,
        )
        self.assertNotIn('$', c_source)

    def test_c_emitter_prepares_hir_call_targets(self):
        source = '''
            struct Counter {
                i32 value;

                void bump(&inout self, i32 amount) {
                    self.value = self.value + amount;
                }
            }

            Counter counter;
            counter.bump(3);
        '''

        program = lower_hir_static_cleanups(
            compile_to_hir(parse(source), print_handler=None)
        )
        emitter = CEmitPass()
        c_source = emitter.emit_hir(program)
        call = program.top_level[-1].expr

        self.assertIn('Counter_bump(&counter, 3);', c_source)
        self.assertEqual('method', call.target.kind)
        self.assertEqual('Counter.bump', call.target.name)
        self.assertEqual('Counter', call.target.owner_type_name)
        self.assertEqual('counter', call.target.receiver_name)


    def test_c_emitter_emits_function_and_method_bodies_directly_from_hir(self):
        class SpyCEmitPass(CEmitPass):
            def __init__(self):
                super().__init__()
                self.hir_return_emits = 0

            def _emit_hir_statement(self, statement, env):
                if type(statement).__name__ == 'HIRReturn':
                    self.hir_return_emits += 1
                return super()._emit_hir_statement(statement, env)

        source = '''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            struct Box {
                i32 value;

                i32 get(&inout self) {
                    return self.value;
                }
            }
        '''

        program = lower_hir_static_cleanups(
            compile_to_hir(parse(source), print_handler=None)
        )
        emitter = SpyCEmitPass()
        c_source = emitter.emit_hir(program)

        self.assertIn('return (left + right);', c_source)
        self.assertIn('return self->value;', c_source)
        self.assertEqual(2, emitter.hir_return_emits)

    def test_comptime_receiver_method_call_uses_normal_method_for_plain_receiver_type(self):
        source = '''
            struct ExampleDriver(comptime type Accessor, comptime Accessor accessor) {
                void do_stuff(&inout self) {
                    accessor.write(0, 18);
                    accessor.write(4, 25);
                }
            }

            struct CanDriver {
                void write(&inout self, i32 address, i32 value) {
                    print(address);
                    print(value);
                }
            }

            comptime CanDriver can1;
            ExampleDriver(CanDriver, can1) exampleDriver;
            exampleDriver.do_stuff();
        '''

        c_source = emit_c(parse(source))

        self.assertIn('uint8_t jack_empty;', c_source)
        self.assertIn('void CanDriver_write(CanDriver *self, int32_t address, int32_t value)', c_source)
        self.assertNotIn('CanDriver_write_comptime_self', c_source)
        self.assertRegex(c_source, r'CanDriver_write\(&accessor_runtime_\d+, 0, 18\);')
        self.assertRegex(c_source, r'CanDriver_write\(&accessor_runtime_\d+, 4, 25\);')
        self.assertNotIn('accessor.write', c_source)

    def test_comptime_receiver_method_call_uses_specialized_type_method_for_generic_receiver_type(self):
        source = '''
            struct ExampleDriver(comptime type Accessor, comptime Accessor accessor) {
                void do_stuff(&inout self) {
                    accessor.write(18);
                }
            }

            struct CanDriver(comptime i32 bus) {
                void write(&inout self, i32 value) {
                    print(bus);
                    print(value);
                }
            }

            comptime CanDriver(12) can1;
            ExampleDriver(CanDriver(12), can1) exampleDriver;
            exampleDriver.do_stuff();
        '''

        c_source = emit_c(parse(source))

        self.assertIn('void CanDriver_comptime_bus_12_write(', c_source)
        self.assertNotIn('CanDriver_comptime_bus_12_write_comptime_self', c_source)
        self.assertRegex(c_source, r'CanDriver_comptime_bus_12_write\(&accessor_runtime_\d+, 18\);')
        self.assertIn('printf("bus = %" PRId32 "\\n", (int32_t)(12));', c_source)

    def test_emits_specialized_generic_struct(self):
        source = '''
            struct Box(comptime type T, comptime i32 N) {
                T value;
            }

            Box(i32, 4) small;
            small.value = 11;
            print(small.value);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('typedef struct Box_comptime_T_i32_N_4 {', c_source)
        self.assertIn('    int32_t value;', c_source)
        self.assertIn('small.value = 11;', c_source)
        self.assertIn('printf("small.value = %" PRId32 "\\n", (int32_t)(small.value));', c_source)
        self.assertNotIn('$', c_source)

    def test_generated_names_do_not_collide_with_source_names(self):
        source = '''
            i32 add_offset(comptime i32 offset, i32 value) {
                return offset + value;
            }

            i32 add_offset_comptime_offset_3(i32 value) {
                return value;
            }

            i32 y = add_offset(3, 4);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('int32_t add_offset_comptime_offset_3(int32_t value);', c_source)
        self.assertIn('int32_t add_offset_comptime_offset_3_2(int32_t value);', c_source)
        self.assertIn('y = add_offset_comptime_offset_3_2(4);', c_source)

    def test_emits_constructor_and_destructor_calls(self):
        source = '''
            struct CanDriver {
                i32 slave_address;

                init(&inout self, i32 slave_address) {
                    self.slave_address = slave_address;
                }

                deinit(&inout self) {
                    print(self.slave_address);
                }
            }

            CanDriver can(5);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('void CanDriver_init(CanDriver *self, int32_t slave_address);', c_source)
        self.assertIn('void CanDriver_deinit(CanDriver *self);', c_source)
        self.assertIn('CanDriver can;', c_source)
        self.assertIn('CanDriver_init(&can, 5);', c_source)
        self.assertIn('CanDriver_deinit(&can);', c_source)
        self.assertNotIn('can =', c_source)

    def test_emits_local_destructor_before_return(self):
        source = '''
            struct Tracer {
                i32 value;

                init(&inout self, i32 value) {
                    self.value = value;
                }

                deinit(&inout self) {
                    print(self.value);
                }
            }

            void run() {
                Tracer tracer(3);
                return;
            }
        '''

        c_source = emit_c(parse(source))

        self.assertIn('Tracer tracer = {0};', c_source)
        self.assertIn('Tracer_init(&tracer, 3);', c_source)
        self.assertIn('Tracer_deinit(&tracer);\n    return;', c_source)

    def test_emits_type_methods_as_c_functions(self):
        source = '''
            struct Line {
                i32 p1;
                i32 p2;

                i32 sum(&inout self) {
                    return self.p1 + self.p2;
                }
            }

            Line line;
            line.p1 = 3;
            line.p2 = 4;
            i32 total = line.sum();
        '''

        c_source = emit_c(parse(source))

        self.assertIn('int32_t Line_sum(Line *self);', c_source)
        self.assertIn('int32_t Line_sum(Line *self) {', c_source)
        self.assertIn('return (self->p1 + self->p2);', c_source)
        self.assertIn('total = Line_sum(&line);', c_source)

    def test_emits_specialized_generic_type_methods(self):
        source = '''
            struct Box(comptime type T) {
                T value;

                T get(&inout self) {
                    return self.value;
                }
            }

            Box(i32) box;
            box.value = 42;
            i32 y = box.get();
        '''

        c_source = emit_c(parse(source))

        self.assertIn('int32_t Box_comptime_T_i32_get(Box_comptime_T_i32 *self);', c_source)
        self.assertIn('return self->value;', c_source)
        self.assertIn('y = Box_comptime_T_i32_get(&box);', c_source)


    def test_emits_void_functions(self):
        source = '''
            i32 target;

            void set_target(i32 value) {
                target = value;
                return;
            }

            set_target(12);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('void set_target(int32_t value);', c_source)
        self.assertIn('void set_target(int32_t value) {', c_source)
        self.assertIn('target = value;', c_source)
        self.assertIn('return;', c_source)
        self.assertIn('set_target(12);', c_source)

    def test_emits_void_methods(self):
        source = '''
            struct Counter {
                i32 value;

                void add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                    return;
                }
            }

            Counter counter;
            counter.add(5);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('void Counter_add(Counter *self, int32_t delta);', c_source)
        self.assertIn('void Counter_add(Counter *self, int32_t delta) {', c_source)
        self.assertIn('self->value = (self->value + delta);', c_source)
        self.assertIn('Counter_add(&counter, 5);', c_source)

    def test_emits_runtime_control_flow(self):
        source = '''
            i32 classify(i32 value) {
                if (value == 0) {
                    return 10;
                } elif (value == 1) {
                    return 20;
                } else {
                    return 30;
                }
            }

            i32 sum;
            while (sum < 3) {
                sum = sum + 1;
            }

            for (i32 i = 0; i < 3; i = i + 1) {
                sum = sum + i;
            }
        '''

        c_source = emit_c(parse(source))

        self.assertIn('if ((value == 0)) {', c_source)
        self.assertIn('else if ((value == 1)) {', c_source)
        self.assertIn('else {', c_source)
        self.assertIn('while ((sum < 3)) {', c_source)
        self.assertIn('for (int32_t i = 0; (i < 3); i = (i + 1)) {', c_source)

    def test_emits_str_variables_and_prints(self):
        source = '''
            str message = "hello";
            print(message);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('#include "jack_runtime.h"', c_source)
        self.assertNotIn('typedef struct jack_str {', c_source)
        self.assertIn('jack_str message;', c_source)
        self.assertIn('message = (jack_str){"hello", 5};', c_source)
        self.assertIn(
            'printf("message = %.*s\\n", (int)((message).len), (message).data);',
            c_source,
        )

    def test_emits_str_equality_with_runtime_helper(self):
        source = '''
            str left = "can";
            str right = "can";
            bool same = left == right;
            bool different = left != "eth";
        '''

        c_source = emit_c(parse(source))

        self.assertIn('same = jack_str_equal(left, right);', c_source)
        self.assertIn('(jack_str){"eth", 3}', c_source)
        self.assertIn('different = (!', c_source)

    def test_emits_comptime_str_generic_type(self):
        source = '''
            struct Device(comptime str name) {
                void show(&inout self) {
                    print(name);
                }
            }

            Device("can0") device;
            device.show();
        '''

        c_source = emit_c(parse(source))

        self.assertIn('typedef struct Device_comptime_name_can0 {', c_source)
        self.assertIn('void Device_comptime_name_can0_show(Device_comptime_name_can0 *self)', c_source)
        self.assertIn(
            'printf("name = %.*s\\n", (int)(((jack_str){"can0", 4}).len), ((jack_str){"can0", 4}).data);',
            c_source,
        )


    def test_emits_runtime_formatted_print(self):
        source = '''
            str name = "can0";
            i32 value = 18;
            print(f"{name}: {value}");
        '''

        c_source = emit_c(parse(source))

        self.assertIn(
            'printf("%.*s: %" PRId32 "\\n", (int)((name).len), (name).data, (int32_t)(value));',
            c_source,
        )

    def test_emits_runtime_formatted_print_with_escaped_percent(self):
        source = '''
            str name = "can0";
            print(f"{name} 100%");
        '''

        c_source = emit_c(parse(source))

        self.assertIn(
            'printf("%.*s 100%%\\n", (int)((name).len), (name).data);',
            c_source,
        )

    def test_emits_comptime_folded_formatted_print(self):
        source = '''
            comptime str name = "can0";
            print(f"driver {name}");
        '''

        c_source = emit_c(parse(source))

        self.assertIn('(jack_str){"driver can0", 11}', c_source)
        self.assertIn(
            'printf("%.*s\\n", (int)(((jack_str){"driver can0", 11}).len), ((jack_str){"driver can0", 11}).data);',
            c_source,
        )


    def test_emits_bool_type_literals_and_prints(self):
        source = '''
            bool enabled = true;
            bool disabled = false;
            bool same = enabled != disabled;
            print(enabled);
            print(disabled);
            print(f"same={same}");
        '''

        c_source = emit_c(parse(source))

        self.assertIn('#include "jack_runtime.h"', c_source)
        self.assertIn('bool enabled;', c_source)
        self.assertIn('enabled = true;', c_source)
        self.assertIn('disabled = false;', c_source)
        self.assertIn('same = (enabled != disabled);', c_source)
        self.assertIn('printf("enabled = %s\\n", (enabled) ? "true" : "false");', c_source)
        self.assertIn('printf("same=%s\\n", (same) ? "true" : "false");', c_source)


    def test_emits_builtin_type_conversions(self):
        source = '''
            u8 byte = u8(255);
            i64 wide = i64(byte);
            f32 ratio = f32(3);
            b16 raw = b16(48879);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('byte = ((uint8_t)(255));', c_source)
        self.assertIn('wide = ((int64_t)(byte));', c_source)
        self.assertIn('ratio = ((float)(3));', c_source)
        self.assertIn('raw = ((uint16_t)(48879));', c_source)

    def test_emits_raw_memory_builtin_type_conversions(self):
        source = '''
            i16 half = i16(258);
            i32 native = 45;
            u64 wide = u64(45);
            be_i32 big = be_i32(45);
            le_i32 little = le_i32(45);
            b16 half_raw = b16(half);
            b32 native_raw = b32(native);
            b64 wide_raw = b64(wide);
            b32 big_raw = b32(big);
            b32 little_raw = b32(little);
            b32 literal_raw = b32(45);
        '''

        c_source = emit_c(parse(source))

        self.assertNotIn('#define JACK_BSWAP16(value)', c_source)
        self.assertNotIn('#define JACK_BSWAP64(value)', c_source)
        self.assertIn('half_raw = JACK_B16_FROM_NATIVE16(half);', c_source)
        self.assertIn('native_raw = JACK_B32_FROM_NATIVE32(native);', c_source)
        self.assertIn('wide_raw = JACK_B64_FROM_NATIVE64(wide);', c_source)
        self.assertIn('big_raw = JACK_B32_FROM_BE32(big);', c_source)
        self.assertIn('little_raw = JACK_B32_FROM_LE32(little);', c_source)
        self.assertIn('literal_raw = JACK_B32_FROM_NATIVE32(45);', c_source)

    def test_c_emit_rejects_invalid_literal_builtin_type_conversions(self):
        for source in [
            'u8 value = u8(256);',
            'i32 value = i32(1.5);',
            'i32 value = i32(true);',
            'bool value = bool(1);',
        ]:
            with self.subTest(source=source):
                with self.assertRaises(SemanticError):
                    emit_c(parse(source))


    def test_emits_sized_builtin_c_types(self):
        source = '''
            i64 signed_value = 1;
            i16 short_value = 2;
            i8 tiny_value = 3;
            be_i32 big_signed = 4;
            le_i32 little_signed = 5;
            u64 wide = 6;
            u32 word = 5;
            u16 half = 6;
            u8 byte = 7;
            b64 raw_wide = 8;
            b32 raw_word = 9;
            b16 raw_half = 10;
            b8 raw_byte = 11;
            f64 ratio = 1.25;
            f32 small_ratio = 1.5;
        '''

        c_source = emit_c(parse(source))

        self.assertIn('int64_t signed_value;', c_source)
        self.assertIn('int16_t short_value;', c_source)
        self.assertIn('int8_t tiny_value;', c_source)
        self.assertIn('int32_t big_signed;', c_source)
        self.assertIn('int32_t little_signed;', c_source)
        self.assertIn('uint64_t wide;', c_source)
        self.assertIn('uint32_t word;', c_source)
        self.assertIn('uint16_t half;', c_source)
        self.assertIn('uint8_t byte;', c_source)
        self.assertIn('uint64_t raw_wide;', c_source)
        self.assertIn('uint32_t raw_word;', c_source)
        self.assertIn('uint16_t raw_half;', c_source)
        self.assertIn('uint8_t raw_byte;', c_source)
        self.assertIn('double ratio;', c_source)
        self.assertIn('float small_ratio;', c_source)

    def test_emits_sized_builtin_print_formats(self):
        source = '''
            i64 signed_value = 12;
            u8 byte = 255;
            b16 raw = 48879;
            f64 ratio = 1.25;
            f32 small_ratio = 1.5;
            print(signed_value);
            print(byte);
            print(raw);
            print(ratio);
            print(small_ratio);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('printf("signed_value = %" PRId64 "\\n", (int64_t)(signed_value));', c_source)
        self.assertIn('printf("byte = %" PRIu8 "\\n", (unsigned int)(byte));', c_source)
        self.assertIn('printf("raw = 0x%04" PRIx16 "\\n", (unsigned int)(raw));', c_source)
        self.assertIn('printf("ratio = %.17g\\n", (double)(ratio));', c_source)
        self.assertIn('printf("small_ratio = %.9g\\n", (double)(small_ratio));', c_source)

    def test_emits_formatted_print_for_sized_builtins(self):
        source = '''
            b8 raw = 15;
            f32 ratio = 1.5;
            print(f"raw={raw}, ratio={ratio}");
        '''

        c_source = emit_c(parse(source))

        self.assertIn(
            'printf("raw=0x%02" PRIx8 ", ratio=%.9g\\n", (unsigned int)(raw), (double)(ratio));',
            c_source,
        )



if __name__ == '__main__':
    unittest.main()
