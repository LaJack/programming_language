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
    import std.memory;

    void run() raises CapacityError, LayoutError, AllocationError, BoundsError {
        StaticAllocator(i32, 4) storage;
        Vector(i32, StaticAllocator(i32, 4)) values(storage, 3);
        values.push(10);
        values.push(20);
        values.push(30);
        values.push(40);

        usize length = values.len();
        &in i32 first = values.get(0);
        &in i32 second = values.get(1);
        &in i32 third = values.get(2);

        print(length);
        print(first);
        print(second);
        print(third);
    }

    try {
        run();
    } catch CapacityError { print("capacity error");
    } catch LayoutError { print("layout error");
    } catch AllocationError { print("allocation error");
    } catch BoundsError { print("bounds error"); }
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
            'length = 4\n'
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

        self.assertIn('typedef struct std_memory_StaticAllocator_comptime_T_i32_N_4 {', c_source)
        vector_name = 'std_collections_vector_Vector_comptime_T_i32_A_std_memory_StaticAllocator_comptime_T_i32_N_4'
        self.assertIn(f'typedef struct {vector_name} {{', c_source)
        self.assertIn(f'{vector_name}_push', c_source)
        self.assertIn(f'{vector_name}_push(&values, 40);', c_source)

    def test_static_growth_failure_preserves_elements_and_pop_clear_work(self):
        source_text = '''
import std.collections.vector;
import std.memory;

void run() raises CapacityError, LayoutError, AllocationError, BoundsError {
    StaticAllocator(i32, 2) storage;
    Vector(i32, StaticAllocator(i32, 2)) values(storage, 1);
    values.push(10);
    values.push(20);
    try {
        values.push(30);
    } catch CapacityError {
        print(values.len());
    }
    i32 last = values.pop();
    print(last);
    values.clear();
    print(values.len());
    print(values.capacity());
}

try { run();
} catch CapacityError { print("unexpected capacity error");
} catch LayoutError { print("layout error");
} catch AllocationError { print("allocation error");
} catch BoundsError { print("bounds error"); }
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / 'main.jack'
            source.write_text(source_text)
            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(source)])

        self.assertEqual(0, status)
        self.assertEqual(
            'values.len() = 2\nlast = 20\nvalues.len() = 0\nvalues.capacity() = 2\n',
            output.getvalue(),
        )


if __name__ == '__main__':
    unittest.main()
