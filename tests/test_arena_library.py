import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs
from jack.semantic_pass import SemanticError


ROOT = Path(__file__).resolve().parents[1]
SELFHOST_ROOT = ROOT / 'selfhost'


ARENA_PROGRAM = '''
import std.collections.arena;
import std.memory;

pub struct Item { i32 value; }

i32 run() raises CapacityError, LayoutError, AllocationError, ArenaHandleError {
    SystemAllocator allocator;
    Arena(Item, SystemAllocator) arena(allocator, usize(0));
    ArenaHandle(Item) first = arena.insert(Item { value = 10 });
    ArenaHandle(Item) second = arena.insert(Item { value = 20 });
    usize index = usize(0);
    while (index < usize(20)) {
        arena.insert(Item { value = i32(index) });
        index = index + usize(1);
    }
    Arena(Item, SystemAllocator) moved = move arena;
    if (true) {
        &inout Item second_item = moved.get_mut(second);
        second_item.value = 21;
    }
    print(moved.len());
    print(moved.is_empty());
    &in Item first_result = moved.get(first);
    &in Item second_result = moved.get(second);
    print(first_result.value);
    print(second_result.value);
    print(first.equals(first));
    print(first.equals(second));
    return 0;
}

i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch ArenaHandleError { return 13; }
}
'''

EXPECTED = (
    'moved.len() = 22\n'
    'moved.is_empty() = false\n'
    'first_result.value = 10\n'
    'second_result.value = 21\n'
    'first.equals(first) = true\n'
    'first.equals(second) = false\n'
)


class ArenaLibraryTests(unittest.TestCase):
    def _program(self, source: str, *, module_roots=()):
        temporary = tempfile.TemporaryDirectory()
        entry = Path(temporary.name) / 'main.jack'
        entry.write_text(source)
        program = CompilerDriver(print_handler=None).compile_hir(
            entry, CompilationOptions(module_roots=tuple(module_roots))
        )
        return temporary, entry, program

    def test_system_arena_matches_all_runtimes_and_survives_growth(self):
        temporary, entry, program = self._program(ARENA_PROGRAM)
        with temporary:
            interpreted = io.StringIO()
            with redirect_stdout(interpreted):
                status = Interpreter(
                    externs=default_runtime_externs()
                ).eval_hir_program(program, ['arena-test'])
            self.assertEqual(0, status)
            self.assertEqual(EXPECTED, interpreted.getvalue())

            for backend, optimization in (('c', 0), ('llvm', 0), ('llvm', 2)):
                with self.subTest(backend=backend, optimization=optimization):
                    output = Path(temporary.name) / f'{backend}-{optimization}'
                    CompilerDriver(print_handler=None).compile_executable(
                        entry,
                        CompilationOptions(
                            backend=backend,
                            output=output,
                            optimization=optimization,
                        ),
                    )
                    result = subprocess.run(
                        [str(output)], capture_output=True, text=True, check=False
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual(EXPECTED, result.stdout)

    def test_static_capacity_failure_preserves_handles_and_values(self):
        source = '''
import std.collections.arena;
import std.memory;

void run() raises CapacityError, LayoutError, AllocationError, ArenaHandleError {
    StaticAllocator(i32, 2) allocator;
    Arena(i32, StaticAllocator(i32, 2)) arena(allocator, usize(0));
    ArenaHandle(i32) first = arena.insert(10);
    arena.insert(20);
    try { arena.insert(30); }
    catch CapacityError {
        print(arena.len());
        print(arena.get(first));
    }
}

try { run(); }
catch CapacityError { print("unexpected capacity"); }
catch LayoutError { print("layout"); }
catch AllocationError { print("allocation"); }
catch ArenaHandleError { print("handle"); }
'''
        temporary, _entry, program = self._program(source)
        with temporary:
            output = io.StringIO()
            with redirect_stdout(output):
                Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
        self.assertEqual('arena.len() = 2\narena.get(first) = 10\n', output.getvalue())

    def test_bootstrap_fixture_stores_source_spans(self):
        source = '''
import bootstrap.arena_fixture;
import bootstrap.source;
import std.collections.arena;
import std.memory;
import std.string;

void run() raises CapacityError, LayoutError, AllocationError, SourceMapError {
    SystemAllocator source_allocator;
    String(SystemAllocator) text(source_allocator, "abc");
    SourceMap sources = source_map();
    SourceId source = sources.add("fixture.jack", text);
    SourceSpan[2] spans;
    spans[0] = source_span(source, usize(0), usize(1), usize(1), usize(1));
    spans[1] = source_span(source, usize(2), usize(3), usize(1), usize(3));
    SystemAllocator allocator;
    Arena(SourceSpan, SystemAllocator) arena =
        store_source_spans(SystemAllocator, spans[..], allocator);
    print(arena.len());
}
try { run(); }
catch CapacityError { }
catch LayoutError { }
catch AllocationError { }
catch SourceMapError { }
'''
        temporary, _entry, program = self._program(
            source, module_roots=(SELFHOST_ROOT,)
        )
        with temporary:
            output = io.StringIO()
            with redirect_stdout(output):
                Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
        self.assertEqual('arena.len() = 2\n', output.getvalue())

    def test_handle_storage_is_private(self):
        source = '''
import std.collections.arena;
import std.memory;
void run() raises CapacityError, LayoutError, AllocationError {
    SystemAllocator allocator;
    Arena(i32, SystemAllocator) arena(allocator, usize(0));
    ArenaHandle(i32) handle = arena.insert(1);
    print(handle.index);
}
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text(source)
            with self.assertRaisesRegex(SemanticError, 'Field "index" is private'):
                CompilerDriver(print_handler=None).compile_hir(
                    entry, CompilationOptions()
                )

    def test_incompatible_handle_type_is_rejected(self):
        source = '''
import std.collections.arena;
import std.memory;
void run() raises CapacityError, LayoutError, AllocationError, ArenaHandleError {
    SystemAllocator allocator;
    Arena(i32, SystemAllocator) arena(allocator, usize(0));
    ArenaHandle(i64) handle;
    arena.get(handle);
}
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text(source)
            with self.assertRaisesRegex(SemanticError, 'ArenaHandle'):
                CompilerDriver(print_handler=None).compile_hir(
                    entry, CompilationOptions()
                )

    def test_live_element_borrow_blocks_insertion(self):
        source = '''
import std.collections.arena;
import std.memory;
void run() raises CapacityError, LayoutError, AllocationError, ArenaHandleError {
    SystemAllocator allocator;
    Arena(i32, SystemAllocator) arena(allocator, usize(1));
    ArenaHandle(i32) handle = arena.insert(1);
    &in i32 value = arena.get(handle);
    arena.insert(2);
    print(value);
}
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text(source)
            with self.assertRaisesRegex(SemanticError, 'borrow'):
                CompilerDriver(print_handler=None).compile_hir(
                    entry, CompilationOptions()
                )

    def test_shared_arena_borrow_cannot_insert(self):
        source = '''
import std.collections.arena;
import std.memory;
void append(&in Arena(i32, SystemAllocator) arena)
    raises CapacityError, LayoutError, AllocationError
{
    arena.insert(1);
}
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text(source)
            with self.assertRaisesRegex(SemanticError, 'read-only|&in'):
                CompilerDriver(print_handler=None).compile_hir(
                    entry, CompilationOptions()
                )

    def test_out_of_range_cross_arena_handle_is_reported(self):
        source = '''
import std.collections.arena;
import std.memory;
void run() raises CapacityError, LayoutError, AllocationError, ArenaHandleError {
    SystemAllocator first_allocator;
    Arena(i32, SystemAllocator) first(first_allocator, usize(2));
    first.insert(1);
    ArenaHandle(i32) handle = first.insert(2);
    SystemAllocator second_allocator;
    Arena(i32, SystemAllocator) second(second_allocator, usize(0));
    second.get(handle);
}
try { run(); }
catch CapacityError { print("capacity"); }
catch LayoutError { print("layout"); }
catch AllocationError { print("allocation"); }
catch ArenaHandleError { print("invalid handle"); }
'''
        temporary, _entry, program = self._program(source)
        with temporary:
            output = io.StringIO()
            with redirect_stdout(output):
                Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
        self.assertEqual('"invalid handle" = invalid handle\n', output.getvalue())

    def test_failed_insert_and_teardown_destroy_values_once_in_reverse_order(self):
        source = '''
import std.collections.arena;
import std.memory;

pub struct Resource {
    i32 identifier;
    deinit(move self) { print(self.identifier); }
}

void run() raises CapacityError, LayoutError, AllocationError {
    StaticAllocator(Resource, 2) allocator;
    Arena(Resource, StaticAllocator(Resource, 2)) arena(allocator, usize(0));
    arena.insert(Resource { identifier = 1 });
    arena.insert(Resource { identifier = 2 });
    try { arena.insert(Resource { identifier = 3 }); }
    catch CapacityError { print("failed"); }
}

try { run(); }
catch CapacityError { print("unexpected capacity"); }
catch LayoutError { print("layout"); }
catch AllocationError { print("allocation"); }
'''
        expected = (
            'self.identifier = 3\n'
            '"failed" = failed\n'
            'self.identifier = 2\n'
            'self.identifier = 1\n'
        )
        temporary, entry, program = self._program(source)
        with temporary:
            interpreted = io.StringIO()
            with redirect_stdout(interpreted):
                Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
            self.assertEqual(expected, interpreted.getvalue())
            for backend in ('c', 'llvm'):
                output = Path(temporary.name) / f'destructors-{backend}'
                CompilerDriver(print_handler=None).compile_executable(
                    entry,
                    CompilationOptions(backend=backend, output=output),
                )
                result = subprocess.run(
                    [str(output)], capture_output=True, text=True, check=False
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, result.stdout)


if __name__ == '__main__':
    unittest.main()
