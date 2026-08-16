from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ConformanceCase:
    name: str
    source: str
    expected_stdout: str
    expected_exit_status: int = 0
    files: Mapping[str, str] = field(default_factory=dict)


CONFORMANCE_CASES = (
    ConformanceCase(
        name='primitives_control_comptime_and_raw_bytes',
        source='''
            i32 add(comptime i32 offset, i32 value) {
                return offset + value;
            }

            comptime i32 folded = add(6, 1);
            comptime print(folded);
            i32 runtime = add(7, 5);
            b32 left = b32(45);
            b32 right = b32(45);
            bool equal = left == right;
            be_i32 big = be_i32(42);
            i32 converted = i32(big);
            i32 total = 0;
            for (i32 index = 0; index < 3; index = index + 1) {
                total = total + index;
            }
            print(runtime);
            print(equal);
            print(converted);
            print(total);
        ''',
        expected_stdout=(
            'folded = 7\nruntime = 12\nequal = true\n'
            'converted = 42\ntotal = 3\n'
        ),
    ),
    ConformanceCase(
        name='generic_type_specialization',
        source='''
            struct Box(comptime type T) {
                T value;

                init(&inout self, T value) {
                    self.value = value;
                }
            }

            Box(i32) box(11);
            print(box.value);
        ''',
        expected_stdout='box.value = 11\n',
    ),
    ConformanceCase(
        name='structs_arrays_slices_borrows_views_and_cleanup',
        source='''
            struct Packet {
                i32 value;

                init(&inout self, i32 value) {
                    self.value = value;
                }

                deinit(&inout self) {
                    print(self.value);
                }
            }

            view PacketView {
                inout i32 value;
            }

            void bump(&inout PacketView packet) {
                packet.value = packet.value + 1;
            }

            void fill(&out u8[] values) {
                values[0] = 8;
                values[1] = 9;
            }

            Packet packet(40);
            bump(packet);
            u8[4] values;
            fill(values[1..3]);
            print(packet.value);
            print(values[1]);
            print(values[2]);
        ''',
        expected_stdout='packet.value = 41\nvalues[1] = 8\nvalues[2] = 9\nself.value = 41\n',
    ),
    ConformanceCase(
        name='errors_multiple_catches_rethrow_and_cleanup',
        source='''
            struct FirstError { i32 code; }
            struct SecondError { i32 code; }

            struct Tracer {
                i32 value;
                deinit(&inout self) { print(self.value); }
            }

            i32 fail(bool should_fail) raises FirstError, SecondError {
                if (should_fail) {
                    raise FirstError { code = 7 };
                }
                return 23;
            }

            void forward() raises FirstError, SecondError {
                Tracer tracer;
                tracer.value = 9;
                try {
                    i32 ignored = fail(true);
                } catch FirstError error {
                    print(error.code);
                    rethrow;
                }
            }

            try {
                i32 result = fail(false);
                print(result);
                forward();
            } catch SecondError error {
                print(error.code);
            } catch FirstError error {
                print(error.code);
            }
        ''',
        expected_stdout=(
            'result = 23\nerror.code = 7\nself.value = 9\nerror.code = 7\n'
        ),
    ),
    ConformanceCase(
        name='ownership_moves_reinitialization_and_cleanup',
        source='''
            struct Resource {
                i32 id;
                init(&inout self, i32 id) { self.id = id; }
                deinit(&inout self) { print(self.id); }
            }

            Resource make(i32 id) {
                Resource resource(id);
                return resource;
            }

            void consume(move Resource resource) {
                print(resource.id);
            }

            void run() {
                u8[2] values;
                values[0] = 7;
                u8[2] copied = values;
                values[0] = 9;
                print(copied[0]);

                Resource resource(1);
                consume(resource);
                resource = make(2);
                resource = make(3);
            }
            run();
        ''',
        expected_stdout=(
            'copied[0] = 7\nresource.id = 1\nself.id = 1\n'
            'self.id = 2\nself.id = 3\n'
        ),
    ),
    ConformanceCase(
        name='imported_module',
        source='''
            module app.main;
            import math.ops;

            i32 result = add(20, 22);
            print(result);
        ''',
        expected_stdout='result = 42\n',
        files={
            'math/ops.jack': '''
                module math.ops;
                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''',
        },
    ),
    ConformanceCase(
        name='extern_c_call',
        source='''
            extern "c" type FILE;
            extern "c" &inout FILE stdout;
            extern "c" usize fwrite(
                &in c_void data, usize size, usize count, &inout FILE stream
            );

            u8[3] message;
            message[0] = 111;
            message[1] = 107;
            message[2] = 10;
            usize written = fwrite(message[0], 1, len(message), stdout);
            print(written);
        ''',
        expected_stdout='ok\nwritten = 3\n',
    ),
)


__all__ = ['CONFORMANCE_CASES', 'ConformanceCase']
