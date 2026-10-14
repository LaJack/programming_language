from pathlib import Path
import subprocess
import tempfile
import unittest

from jack.compiler_driver import CompilationOptions
from tests.bootstrap_artifacts import artifacts, ROOTS
from tests.bootstrap_interpreter_runner import run_hir_isolated


TABLE_INVARIANTS = '''
import bootstrap.types;
import bootstrap.project;
import bootstrap.frontend;
import bootstrap.names;
import bootstrap.source;
import bootstrap.syntax;
import bootstrap.parser;
import bootstrap.diagnostics;
import std.collections.arena;
import std.collections.vector;
import std.memory;
import std.string;

i32 run() raises CapacityError, LayoutError, AllocationError, BoundsError,
    ProjectReferenceError, FrontendReferenceError, NameReferenceError,
    SourceMapError, ArenaHandleError, SyntaxValidationError {
    ProjectBuilder builder = project_builder();
    ProjectOptions options = project_options();
    DiagnosticBag diagnostics(usize(1));
    SystemAllocator allocator;
    String(SystemAllocator) text(allocator, "i32 value = 1;");
    ParsedFileId file = builder.parse_source("fixture.jack", text, diagnostics,
        options.parser_options());
    FrontendProject project = finish_project(builder);
    NodeRef node = project.frontend().root(file, usize(0));
    TypeTables tables = type_tables(project.identity_value());
    bool rejected = false;
    try { tables.enter_bodies(project.identity_value()); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 1; }
    tables.prepare(project);
    rejected = false;
    try { tables.prepare(project); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 2; }
    if (tables.has_result(node)) { return 3; }
    rejected = false;
    try { tables.result_for(node); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 4; }
    tables.record(node, TypeResult.deferred(TypeDeferred.computed_type), false, false);
    tables.add_pending(node, TypeDeferred.computed_type);
    if (tables.pending_count() != usize(1)) { return 5; }
    if (tables.has_result(node) == false) { return 6; }
    ProjectBuilder other_builder = project_builder();
    FrontendProject other = finish_project(other_builder);
    TypeTables foreign = type_tables(other.identity_value());
    foreign.prepare(other);
    rejected = false;
    try { foreign.has_result(node); }
    catch BoundsError { rejected = true; }
    if (rejected == false) { return 7; }
    tables.enter_bodies(project.identity_value());
    TypeTables moved = move tables;
    if (moved.has_result(node) == false || moved.pending_count() != usize(1)) {
        return 8;
    }
    moved.finish_bodies(project.identity_value());
    rejected = false;
    try { moved.record(node, TypeResult.deferred(TypeDeferred.computed_type), false, false); }
    catch BoundsError { rejected = true; }
    if (rejected == false) { return 9; }
    rejected = false;
    try { moved.finish_bodies(project.identity_value()); }
    catch ProjectReferenceError { rejected = true; }
    if (rejected == false) { return 10; }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 40; }
    catch LayoutError { return 41; }
    catch AllocationError { return 42; }
    catch BoundsError { return 43; }
    catch ProjectReferenceError { return 44; }
    catch FrontendReferenceError { return 45; }
    catch NameReferenceError { return 46; }
    catch SourceMapError { return 47; }
    catch ArenaHandleError { return 48; }
    catch SyntaxValidationError { return 49; }
}
'''


class BootstrapTypingAcceptanceTests(unittest.TestCase):
    def test_checked_tables_reject_phase_and_identity_misuse(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "tables.jack"
            entry.write_text(TABLE_INVARIANTS)
            options = CompilationOptions(module_roots=ROOTS)
            expected = run_hir_isolated(artifacts.hir(entry, options=options),
                                       ["tables-test"], timeout=180,
                                       label="type-table invariants")
            self.assertEqual((0, "", ""), expected)
            for backend in ("c", "llvm"):
                with self.subTest(backend=backend):
                    executable = artifacts.executable(entry, options=CompilationOptions(
                        backend=backend, module_roots=ROOTS)).output_path
                    result = subprocess.run([executable], capture_output=True,
                                            text=True, timeout=30)
                    self.assertEqual(expected,
                                     (result.returncode, result.stdout, result.stderr))

    def test_contextual_checker_matches_interpreter_c_and_llvm(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "parity.jack"
            for source, status in (
                ("module app; u8 narrow() { u8 value = 42; return 7; }", 0),
                ("module app; bool flag = 42;", 1),
                ("module app; union Choice { first; second; } "
                 "i32 choose(&in Choice value) { "
                 "return match (value) { .first => 1, .second => 2, }; }", 3),
                ("module app; struct Box(comptime type T: Copyable) { T item; } "
                 "void build() { Box(i32) value = Box(i32) { item = 1 }; }", 3),
            ):
                with self.subTest(source=source):
                    entry.write_text(source)
                    arguments = ["jack-bootstrap", "--dump", "types",
                                 "--diagnostic-format", "stable", str(entry)]
                    expected = run_hir_isolated(
                        artifacts.hir(), arguments, timeout=180, label="type-check parity")
                    self.assertEqual(status, expected[0])
                    for backend in ("c", "llvm"):
                        executable = artifacts.executable(options=CompilationOptions(
                            backend=backend, module_roots=ROOTS)).output_path
                        result = subprocess.run([str(executable), *arguments[1:]],
                                                capture_output=True, text=True, timeout=30)
                        self.assertEqual(expected,
                                         (result.returncode, result.stdout, result.stderr))
