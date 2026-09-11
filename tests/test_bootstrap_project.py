import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs, malloc


ROOT = Path(__file__).resolve().parents[1]

INTERNER = '''
import bootstrap.names;
import std.memory;
import std.string;

i32 run() raises CapacityError, LayoutError, AllocationError, NameReferenceError {
    NameInterner names = name_interner();
    NameId empty = names.intern("");
    NameId first = names.intern("alpha");
    NameId again = names.intern("alpha");
    if (first.equals(again) == false) { return 1; }
    usize index = usize(0);
    while (index < usize(80)) {
        SystemAllocator allocator;
        String(SystemAllocator) text(allocator, "name");
        text.append_usize(index);
        NameId id = names.intern(text.as_str());
        if (names.text(id) != text.as_str()) { return 2; }
        index = index + usize(1);
    }
    NameInterner moved = move names;
    if (moved.text(first) != "alpha" || moved.text(empty) != "") { return 3; }
    NameId indexed = moved.at(usize(1));
    if (indexed.equals(first) == false) { return 4; }
    if (moved.len() != usize(82)) { return 5; }
    NameInterner other = name_interner();
    NameId foreign = other.intern("alpha");
    NameId collision_left = other.intern("aA");
    NameId collision_right = other.intern("b!");
    if (collision_left.equals(collision_right)) { return 20; }
    if (other.text(collision_left) != "aA" || other.text(collision_right) != "b!") {
        return 21;
    }
    if (moved.contains(foreign)) { return 6; }
    bool rejected = false;
    try { moved.text(foreign); }
    catch NameReferenceError { rejected = true; }
    if (rejected == false) { return 7; }
    rejected = false;
    try { moved.at(moved.len()); }
    catch NameReferenceError { rejected = true; }
    if (rejected == false) { return 8; }
    NameId invalid;
    if (moved.contains(invalid)) { return 9; }
    index = usize(0);
    while (index < usize(80)) {
        SystemAllocator allocator;
        String(SystemAllocator) text(allocator, "name");
        text.append_usize(index);
        NameId id = moved.intern(text.as_str());
        if (id.equals(moved.at(index + usize(2))) == false) { return 19; }
        index = index + usize(1);
    }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch NameReferenceError { return 13; }
}
'''

PATH_PROGRAM = '''
import std.path;
import std.io;
import std.string;
import std.memory;

i32 run(str input, str expected)
    raises IoError, Utf8Error, CapacityError, LayoutError, AllocationError {
    u8[1] tiny;
    tiny[0] = u8(77);
    usize required = canonical_path_into(input, tiny[..]);
    if (required != len(expected) || tiny[0] != u8(77)) { return 1; }
    String(SystemAllocator) resolved = canonical_path(input);
    if (resolved.as_str() != expected) { return 2; }
    bool rejected = false;
    try { canonical_path(""); }
    catch IoError { rejected = true; }
    if (rejected == false) { return 3; }
    rejected = false;
    try { canonical_path("missing-bootstrap-project-file"); }
    catch IoError { rejected = true; }
    if (rejected == false) { return 4; }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(arguments[1], arguments[2]); }
    catch IoError { return 10; }
    catch Utf8Error { return 11; }
    catch CapacityError { return 12; }
    catch LayoutError { return 13; }
    catch AllocationError { return 14; }
}
'''

FAILURE_PROGRAM = '''
import bootstrap.names;
import std.memory;
import std.string;
extern "c" void arm_failure();

i32 run() raises CapacityError, LayoutError, AllocationError, NameReferenceError {
    NameInterner names = name_interner();
    usize index = usize(0);
    while (index < usize(8)) {
        SystemAllocator allocator;
        String(SystemAllocator) text(allocator, "existing");
        text.append_usize(index);
        names.intern(text.as_str());
        index = index + usize(1);
    }
    NameId first = names.at(usize(0));
    arm_failure();
    try { names.intern("a-new-owned-name"); }
    catch AllocationError {
        if (names.len() != usize(8)) { return 1; }
        if (names.text(first) != "existing0") { return 2; }
        names.intern("a-new-owned-name");
    }
    if (names.len() != usize(9)) { return 3; }
    NameId last = names.at(usize(8));
    if (names.text(last) != "a-new-owned-name") { return 4; }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch NameReferenceError { return 13; }
}
'''

STORAGE_PROGRAM = '''
import bootstrap.project;
import bootstrap.frontend;
import bootstrap.names;
import bootstrap.source;
import bootstrap.syntax;
import bootstrap.parser;
import bootstrap.diagnostics;
import std.collections.vector;
import std.collections.arena;
import std.string;
import std.memory;

NodeRef project_root(&in ProjectBuilder builder, ParsedFileId file, usize index)
    raises FrontendReferenceError {
    &in FrontendProject project = builder.project();
    &in FrontendContext context = project.frontend();
    return context.root(file, index);
}
SourceSpan node_span(&in ProjectBuilder builder, NodeRef reference)
    raises FrontendReferenceError {
    &in FrontendProject project = builder.project();
    &in FrontendContext context = project.frontend();
    &in SyntaxNode node = context.node(reference);
    return node.source_span();
}
i32 run() raises CapacityError, LayoutError, AllocationError, NameReferenceError,
    SourceMapError, ProjectReferenceError, FrontendReferenceError, BoundsError,
    ArenaHandleError, SyntaxValidationError {
    ProjectOptions options = project_options();
    if (options.maximum_diagnostics() != usize(100)) { return 1; }
    options.set_maximum_diagnostics(usize(0));
    if (options.maximum_diagnostics() != usize(1)) { return 2; }
    options.add_module_root("selfhost");
    options.add_module_root("jack");
    options.add_stub("hardware", "test.hardware");
    if (options.module_root(usize(0)) != "selfhost") { return 3; }
    &in ModuleOverride override = options.stub(usize(0));
    if (override.replacement_name() != "test.hardware") { return 4; }

    ProjectBuilder builder = project_builder();
    NameId module_name = builder.intern("fixture");
    NameId path = builder.intern("fixture.jack");
    NameId name = builder.intern("value");
    SystemAllocator allocator;
    String(SystemAllocator) source(allocator, "import hardware as hw; i32 value = 1;");
    DiagnosticBag diagnostics(usize(1));
    ParsedFileId file = builder.parse_source("fixture.jack", source, diagnostics, options.parser_options());
    ModuleId module_id = builder.add_module(module_name, path, file, ModuleState.loaded);
    ScopeId scope_id = builder.add_scope(ScopeLink.none, ModuleLink.present(module_id), SymbolLink.none);
    NodeRef node = project_root(builder, file, usize(1));
    NodeRef import_node = project_root(builder, file, usize(0));
    NameId requested = builder.intern("hardware");
    NameId effective = builder.intern("test.hardware");
    NameId alias = builder.intern("hw");
    usize edge_index = builder.add_import(module_id, import_node,
        NameLink.present(requested), NameLink.present(effective), NameLink.present(alias));
    builder.resolve_import(edge_index, ModuleLink.present(module_id));
    SourceSpan span = node_span(builder, node);
    DeclarationModifiers modifiers;
    SymbolId symbol_id = builder.add_symbol(name, SymbolKind.global, scope_id,
        SymbolLink.none, SymbolOrigin.source(node, span), modifiers);
    ScopeId nested_scope = builder.add_lexical_scope(scope_id, SymbolLink.present(symbol_id), node);
    builder.add_occurrence(node, span, scope_id, OccurrenceRole.declaration,
        ReferenceStatus.resolved(symbol_id));
    usize deferred_index = builder.add_occurrence(node, span, scope_id, OccurrenceRole.member,
        ReferenceStatus.deferred(DeferredReason.receiver_type));
    builder.bind_occurrence(deferred_index, ReferenceStatus.resolved(symbol_id));
    if (true) {
        &in FrontendProject current = builder.project();
        if (current.deferred_count() != usize(0)) { return 24; }
    }
    builder.bind_occurrence(deferred_index, ReferenceStatus.deferred(DeferredReason.generic_type));
    builder.set_module_state(module_id, ModuleState.loading);
    builder.set_module_state(module_id, ModuleState.loaded);

    ProjectBuilder other = project_builder();
    ScopeId foreign_scope = other.add_scope(ScopeLink.none, ModuleLink.none, SymbolLink.none);
    NameId foreign_name = other.intern("foreign");
    SymbolId foreign_symbol = other.add_symbol(foreign_name, SymbolKind.builtin_type,
        foreign_scope, SymbolLink.none, SymbolOrigin.builtin, modifiers);
    bool rejected = false;
    try { builder.add_scope(ScopeLink.present(foreign_scope), ModuleLink.none, SymbolLink.none); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 5; }
    rejected = false;
    try { builder.add_occurrence(node, span, scope_id, OccurrenceRole.read,
        ReferenceStatus.resolved(foreign_symbol)); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 6; }
    rejected = false;
    try { builder.bind_occurrence(deferred_index, ReferenceStatus.resolved(foreign_symbol)); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 25; }
    rejected = false;
    try { builder.bind_occurrence(usize(999), ReferenceStatus.invalid); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 26; }
    other.poison(foreign_symbol);
    if (true) {
        &in FrontendProject failed = other.project();
        &in SymbolRecord poisoned = failed.symbol(foreign_symbol);
        if (failed.has_errors() == false || poisoned.is_poisoned() == false) { return 27; }
    }

    SourceSpan outside = source_span(span.source_id(), usize(0), usize(999), usize(1), usize(1));
    rejected = false;
    try { builder.add_occurrence(node, outside, scope_id, OccurrenceRole.read, ReferenceStatus.invalid); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 28; }
    rejected = false;
    try { builder.add_import(module_id, node, NameLink.none, NameLink.none, NameLink.none); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 29; }

    FrontendProject project = finish_project(builder);
    FrontendProject moved = move project;
    if (moved.module_count() != usize(1) || moved.symbol_count() != usize(1)
        || moved.scope_count() != usize(2) || moved.occurrence_count() != usize(2)
        || moved.deferred_count() != usize(1) || moved.has_errors()) { return 7; }
    if (moved.name(name) != "value") { return 8; }
    &in ScopeRecord nested = moved.scope(nested_scope);
    ScopeLocation location = nested.source_location();
    bool same_node = match (&in location) {
        .builtin => false,
        .module_file(_) => false,
        .source(reference) => reference.equals(node),
    };
    if (same_node == false) { return 33; }
    if (moved.import_count() != usize(1)) { return 30; }
    &in ImportRecord edge = moved.import_edge(edge_index);
    if (edge.load_state() != ImportState.resolved) { return 31; }
    NameLink stored_requested = edge.requested_name();
    bool same_request = match (&in stored_requested) {
        .none => false,
        .present(id) => id.equals(requested),
    };
    if (same_request == false) { return 32; }
    &in SymbolRecord symbol = moved.symbol(symbol_id);
    if (symbol.symbol_kind() != SymbolKind.global) { return 9; }
    &in ModuleRecord module_record = moved.module_record(module_id);
    ParsedFileId recorded_file = module_record.parsed_file();
    if (recorded_file.equals(file) == false) { return 20; }
    rejected = false;
    try { moved.name(foreign_name); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 21; }
    rejected = false;
    try { moved.symbol(foreign_symbol); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 22; }
    rejected = false;
    try { moved.module_at(usize(1)); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 23; }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch NameReferenceError { return 13; }
    catch SourceMapError { return 14; }
    catch ProjectReferenceError { return 15; }
    catch FrontendReferenceError { return 16; }
    catch BoundsError { return 17; }
    catch ArenaHandleError { return 18; }
    catch SyntaxValidationError { return 19; }
}
'''


class BootstrapProjectTests(unittest.TestCase):
    def test_checked_project_storage_and_movement(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'storage.jack'
            entry.write_text(STORAGE_PROGRAM)
            options = CompilationOptions(module_roots=(ROOT / 'selfhost',))
            program = CompilerDriver(print_handler=None).compile_hir(entry, options)
            pointers = []

            def allocate(size):
                pointer = malloc(size)
                pointers.append(pointer)
                return pointer

            status = Interpreter(externs={**default_runtime_externs(), 'malloc': allocate}).eval_hir_program(
                program, ['storage']
            )
            self.assertEqual(0, status)
            self.assertTrue(pointers)
            self.assertTrue(all(not pointer.allocation.live for pointer in pointers))
            for backend, optimization in (('c', 0), ('llvm', 0), ('llvm', 2)):
                with self.subTest(backend=backend, optimization=optimization):
                    output = Path(directory) / f'{backend}-{optimization}'
                    CompilerDriver(print_handler=None).compile_executable(
                        entry, CompilationOptions(module_roots=options.module_roots,
                            backend=backend, optimization=optimization, output=output)
                    )
                    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(0, result.returncode, result.stderr)

    def test_failed_interning_is_retryable_and_releases_allocations(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'failure.jack'
            entry.write_text(FAILURE_PROGRAM)
            program = CompilerDriver(print_handler=None).compile_hir(
                entry, CompilationOptions(module_roots=(ROOT / 'selfhost',))
            )
            failures = [None]
            for fail_at in failures:
                with self.subTest(fail_at=fail_at):
                    count = 0
                    armed = False
                    pointers = []

                    def arm():
                        nonlocal armed
                        armed = True

                    def allocate(size):
                        nonlocal count
                        if armed:
                            count += 1
                            if count == fail_at:
                                return None
                        pointer = malloc(size)
                        pointers.append(pointer)
                        return pointer

                    externs = {**default_runtime_externs(), 'malloc': allocate, 'arm_failure': arm}
                    status = Interpreter(externs=externs).eval_hir_program(program, ['failure'])
                    self.assertEqual(0, status)
                    self.assertTrue(all(not pointer.allocation.live for pointer in pointers))
                    if fail_at is None:
                        self.assertGreater(count, 1)
                        failures.extend(range(1, count + 1))

    def test_canonical_paths_and_buffer_resize_match_all_runtimes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / ('long-name-' * 16)
            target.mkdir()
            source = target / 'source.jack'
            source.write_text('')
            link = root / 'alias.jack'
            link.symlink_to(source)
            entry = root / 'paths.jack'
            entry.write_text(PATH_PROGRAM)
            program = CompilerDriver(print_handler=None).compile_hir(entry, CompilationOptions())
            arguments = ['paths', str(link), str(source.resolve())]
            status = Interpreter(externs=default_runtime_externs()).eval_hir_program(program, arguments)
            self.assertEqual(0, status)
            for backend, optimization in (('c', 0), ('llvm', 0), ('c', 2), ('llvm', 2)):
                with self.subTest(backend=backend, optimization=optimization):
                    output = root / f'{backend}-{optimization}'
                    CompilerDriver(print_handler=None).compile_executable(
                        entry, CompilationOptions(backend=backend, optimization=optimization, output=output)
                    )
                    result = subprocess.run([str(output), *arguments[1:]], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual('', result.stdout)

    def test_interner_growth_movement_identity_and_insertion_order(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'main.jack'
            entry.write_text(INTERNER)
            options = CompilationOptions(module_roots=(ROOT / 'selfhost',))
            program = CompilerDriver(print_handler=None).compile_hir(entry, options)
            with redirect_stdout(io.StringIO()):
                status = Interpreter(externs=default_runtime_externs()).eval_hir_program(
                    program, ['interner-test']
                )
            self.assertEqual(0, status)
            for backend, optimization in (('c', 0), ('llvm', 0), ('c', 2), ('llvm', 2)):
                with self.subTest(backend=backend, optimization=optimization):
                    output = Path(directory) / f'{backend}-{optimization}'
                    CompilerDriver(print_handler=None).compile_executable(
                        entry, CompilationOptions(
                            module_roots=options.module_roots, backend=backend,
                            optimization=optimization, output=output,
                        )
                    )
                    result = subprocess.run([str(output)], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertEqual('', result.stdout)


if __name__ == '__main__':
    unittest.main()
