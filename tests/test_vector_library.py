import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.c_emit_pass import emit_c
from jack.cli import main
from jack.module_loader import load_source_file


VECTOR_PROGRAM = """
    import std.collections.vector;

    Vector(i32, StaticAllocator(i32, 4)) values(3);
    bool pushed0 = values.push(10);
    bool pushed1 = values.push(20);
    bool pushed2 = values.push(30);
    bool pushed3 = values.push(40);

    usize length = values.len();
    i32 first = values.get(0);
    i32 second = values.get(1);
    i32 third = values.get(2);

    print(pushed0);
    print(pushed1);
    print(pushed2);
    print(pushed3);
    print(length);
    print(first);
    print(second);
    print(third);
"""


class VectorLibraryTests(unittest.TestCase):
    def test_cli_interpreter_runs_allocator_backed_vector(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'main.jack'
            source.write_text(VECTOR_PROGRAM)

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual(
            'pushed0 = true\n'
            'pushed1 = true\n'
            'pushed2 = true\n'
            'pushed3 = false\n'
            'length = 3\n'
            'first = 10\n'
            'second = 20\n'
            'third = 30\n',
            output.getvalue(),
        )

    def test_c_emit_specializes_vector_and_static_allocator(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'main.jack'
            source.write_text(VECTOR_PROGRAM)

            c_source = emit_c(load_source_file(source), print_handler=None)

        self.assertIn('typedef struct std_collections_vector_StaticAllocator_comptime_T_i32_N_4 {', c_source)
        vector_name = 'std_collections_vector_Vector_comptime_T_i32_Allocator_std_collections_vector_StaticAllocator_comptime_T_i32_N_4'
        self.assertIn(f'typedef struct {vector_name} {{', c_source)
        self.assertIn(f'{vector_name}_push', c_source)
        self.assertIn(f'pushed3 = {vector_name}_push(&values, 40);', c_source)


if __name__ == '__main__':
    unittest.main()
