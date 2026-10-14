import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs, malloc
from jack.semantic_pass import SemanticError


PRELUDE = '''
import std.collections.hash_map;
import std.memory;
import std.option;
pub struct Key { i32 id; }
Key implements HashKey {
    u64 hash(&in self) { return u64(1); }
    bool equals(&in self, &in Self other) { return self.id == other.id; }
}
'''

PROGRAM = PRELUDE + '''
i32 run() raises CapacityError, LayoutError, AllocationError, KeyNotFoundError,
    HashMapCursorError {
    SystemAllocator allocator;
    HashMap(Key, i32, SystemAllocator) map(allocator, usize(0));
    usize index = usize(0);
    while (index < usize(30)) {
        map.insert(Key { id = i32(index) }, i32(index * usize(2)));
        index = index + usize(1);
    }
    if (map.len() != usize(30)) { return 1; }
    Key third = Key { id = 3 };
    Key fourth = Key { id = 4 };
    match (map.remove(third)) {
        .some(value) { if (value != 6) { return 2; } }
        .none { return 3; }
    }
    if (map.contains(third)) { return 4; }
    if (map.get(fourth) != 8) { return 5; }
    Key missing = Key { id = 300 };
    HashMapCursor unchanged = map.cursor();
    map.remove(missing);
    if (map.advance(unchanged) == false) { return 15; }
    bool absent = false;
    try { map.get(missing); } catch KeyNotFoundError { absent = true; }
    if (absent == false) { return 16; }
    map.insert(Key { id = 3 }, 33);
    match (map.insert(Key { id = 3 }, 34)) {
        .some(value) { if (value != 33) { return 6; } }
        .none { return 7; }
    }
    HashMapCursor cursor = map.cursor();
    usize seen = usize(0);
    while (map.advance(cursor)) {
        &inout i32 value = map.value_mut(cursor);
        value = value + 1;
        seen = seen + usize(1);
    }
    if (seen != map.len()) { return 8; }
    if (map.get(third) != 35) { return 9; }
    bool rejected = false;
    try { map.value(cursor); } catch HashMapCursorError { rejected = true; }
    if (rejected == false) { return 10; }
    HashMapCursor old = map.cursor();
    HashMap(Key, i32, SystemAllocator) moved = move map;
    if (moved.advance(old) == false) { return 11; }
    moved.insert(Key { id = 99 }, 99);
    rejected = false;
    try { moved.advance(old); } catch HashMapCursorError { rejected = true; }
    if (rejected == false) { return 12; }
    SystemAllocator other_allocator;
    HashMap(Key, i32, SystemAllocator) other(other_allocator, usize(0));
    HashMapCursor foreign = other.cursor();
    rejected = false;
    try { moved.advance(foreign); } catch HashMapCursorError { rejected = true; }
    if (rejected == false) { return 13; }
    moved.clear();
    if (moved.is_empty() == false) { return 14; }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 40; }
    catch LayoutError { return 41; }
    catch AllocationError { return 42; }
    catch KeyNotFoundError { return 43; }
    catch HashMapCursorError { return 44; }
}
'''


class HashMapLibraryTests(unittest.TestCase):
    def check_program(self, source, *, native=True):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "main.jack"
            entry.write_text(source)
            driver = CompilerDriver(print_handler=None)
            program = driver.compile_hir(entry)
            output = io.StringIO()
            with redirect_stdout(output):
                status = Interpreter(externs=default_runtime_externs()).eval_hir_program(
                    program, ["map-test"])
            self.assertEqual(0, status, output.getvalue())
            if native:
                for backend, optimization in (("c", 0), ("llvm", 0), ("c", 2), ("llvm", 2)):
                    with self.subTest(backend=backend, optimization=optimization):
                        executable = Path(directory) / f"{backend}-{optimization}"
                        driver.compile_executable(entry, CompilationOptions(
                            backend=backend, optimization=optimization, output=executable))
                        result = subprocess.run([executable], capture_output=True, text=True, timeout=30)
                        self.assertEqual((0, output.getvalue(), ""),
                                         (result.returncode, result.stdout, result.stderr))

    def test_collisions_growth_removal_movement_and_cursors(self):
        self.check_program(PROGRAM)

    def test_static_failure_tombstone_reuse_and_cursor_preservation(self):
        source = PRELUDE + '''
i32 run() raises CapacityError, LayoutError, AllocationError, KeyNotFoundError,
    HashMapCursorError {
    StaticAllocator(HashMapBucket(Key, i32), 8) allocator;
    HashMap(Key, i32, StaticAllocator(HashMapBucket(Key, i32), 8)) map(allocator, usize(0));
    usize index = usize(0);
    while (index < usize(6)) {
        map.insert(Key { id = i32(index) }, i32(index));
        index = index + usize(1);
    }
    HashMapCursor cursor = map.cursor();
    bool failed = false;
    try { map.insert(Key { id = 6 }, 6); } catch CapacityError { failed = true; }
    if (failed == false || map.len() != usize(6)) { return 1; }
    if (map.advance(cursor) == false) { return 2; }
    Key key = Key { id = 2 };
    if (map.get(key) != 2) { return 3; }
    map.remove(key);
    map.insert(Key { id = 99 }, 99);
    Key replacement = Key { id = 99 };
    if (map.get(replacement) != 99 || map.len() != usize(6)) { return 4; }
    HashMap(Key, i32, StaticAllocator(HashMapBucket(Key, i32), 8)) moved = move map;
    if (moved.get(replacement) != 99) { return 5; }
    moved.clear();
    moved.insert(Key { id = 1 }, 10);
    if (moved.len() != usize(1)) { return 6; }
    return 0;
}
''' + PROGRAM[PROGRAM.index("i32 main("):]
        self.check_program(source)

    def test_noncopyable_entries_destroy_once_on_replace_remove_and_clear(self):
        prelude = PRELUDE.replace(
            "pub struct Key { i32 id; }",
            "i32 key_drops = 0; pub struct Key { i32 id; "
            "deinit(move self) { key_drops = key_drops + self.id; } }",
        )
        source = prelude + '''
i32 destroyed = 0;
pub struct Resource {
    i32 id;
    deinit(move self) { destroyed = destroyed + self.id; }
}
i32 run() raises CapacityError, LayoutError, AllocationError, KeyNotFoundError,
    HashMapCursorError {
    {
        SystemAllocator allocator;
        HashMap(Key, Resource, SystemAllocator) map(allocator, usize(0));
        map.insert(Key { id = 1 }, Resource { id = 1 });
        map.insert(Key { id = 2 }, Resource { id = 2 });
        map.insert(Key { id = 1 }, Resource { id = 4 });
        if (destroyed != 1) { return 1; }
        Key key = Key { id = 2 };
        map.remove(key);
        if (destroyed != 3) { return 2; }
        map.clear();
        if (destroyed != 7) { return 3; }
        map.insert(Key { id = 3 }, Resource { id = 8 });
    }
    print(destroyed);
    print(key_drops);
    if (destroyed != 15 || key_drops != 9) { return 4; }
    return 0;
}
''' + PROGRAM[PROGRAM.index("i32 main("):]
        self.check_program(source)

    def test_live_entry_borrow_blocks_insertion(self):
        source = PRELUDE + '''
void run() raises CapacityError, LayoutError, AllocationError, KeyNotFoundError {
    SystemAllocator allocator;
    HashMap(Key, i32, SystemAllocator) map(allocator, usize(0));
    map.insert(Key { id = 1 }, 1);
    Key key = Key { id = 1 };
    &in i32 value = map.get(key);
    map.insert(Key { id = 2 }, 2);
    print(value);
}
'''
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "main.jack"
            entry.write_text(source)
            with self.assertRaises(SemanticError):
                CompilerDriver(print_handler=None).compile_hir(entry)

    def test_zero_static_capacity_and_layout_overflow(self):
        source = PRELUDE + '''
i32 run() raises CapacityError, LayoutError, AllocationError, KeyNotFoundError,
    HashMapCursorError {
    StaticAllocator(HashMapBucket(Key, i32), 0) allocator;
    HashMap(Key, i32, StaticAllocator(HashMapBucket(Key, i32), 0)) empty(allocator, usize(0));
    bool failed = false;
    try { empty.insert(Key { id = 1 }, 1); } catch CapacityError { failed = true; }
    if (failed == false || empty.is_empty() == false) { return 1; }
    SystemAllocator system;
    HashMap(Key, i32, SystemAllocator) map(system, usize(0));
    HashMapCursor cursor = map.cursor();
    failed = false;
    try { map.reserve(usize(18446744073709551615)); } catch LayoutError { failed = true; }
    if (failed == false || map.len() != usize(0)) { return 2; }
    if (map.advance(cursor)) { return 3; }
    return 0;
}
''' + PROGRAM[PROGRAM.index("i32 main("):]
        self.check_program(source)

    def test_storage_helpers_require_unsafe_context(self):
        source = PRELUDE + '''
void run() raises CapacityError, LayoutError, AllocationError {
    SystemAllocator allocator;
    HashMap(Key, i32, SystemAllocator) map(allocator, usize(0));
    map.rehash(usize(0));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "unsafe.jack"
            entry.write_text(source)
            with self.assertRaisesRegex(SemanticError, "unsafe"):
                CompilerDriver(print_handler=None).compile_hir(entry)

    def test_failed_host_allocation_preserves_entries_cursor_and_destroys_argument(self):
        source = PRELUDE + '''
extern "c" void arm_failure();
i32 destroyed = 0;
pub struct Resource {
    i32 id;
    deinit(move self) { destroyed = destroyed + self.id; }
}
i32 run() raises CapacityError, LayoutError, AllocationError, KeyNotFoundError,
    HashMapCursorError {
    SystemAllocator allocator;
    HashMap(Key, Resource, SystemAllocator) map(allocator, usize(6));
    usize index = usize(0);
    while (index < usize(6)) {
        map.insert(Key { id = i32(index) }, Resource { id = 1 });
        index = index + usize(1);
    }
    HashMapCursor cursor = map.cursor();
    arm_failure();
    bool failed = false;
    try { map.insert(Key { id = 99 }, Resource { id = 8 }); }
    catch AllocationError { failed = true; }
    if (failed == false || map.len() != usize(6) || destroyed != 8) { return 1; }
    if (map.advance(cursor) == false) { return 2; }
    Key first = Key { id = 0 };
    if (map.get(first).id != 1) { return 3; }
    map.clear();
    if (destroyed != 14) { return 4; }
    return 0;
}
''' + PROGRAM[PROGRAM.index("i32 main("):]
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "failure.jack"
            entry.write_text(source)
            program = CompilerDriver(print_handler=None).compile_hir(entry)
            armed = False
            allocations = []

            def arm():
                nonlocal armed
                armed = True

            def allocate(size):
                nonlocal armed
                if armed:
                    armed = False
                    return None
                pointer = malloc(size)
                allocations.append(pointer)
                return pointer

            status = Interpreter(externs={**default_runtime_externs(),
                                         "malloc": allocate, "arm_failure": arm}).eval_hir_program(
                                             program, ["failure-test"])
            self.assertEqual(0, status)
            self.assertTrue(all(not pointer.allocation.live for pointer in allocations))
