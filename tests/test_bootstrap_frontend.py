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
SELFHOST = ROOT / 'selfhost'

PRELUDE = '''
import bootstrap.frontend;
import bootstrap.source;
import bootstrap.syntax;
import bootstrap.lexer;
import bootstrap.parser;
import bootstrap.diagnostics;
import std.collections.vector;
import std.collections.arena;
import std.memory;
import std.string;

SourceId add_text(&inout FrontendContext context, str text)
    raises CapacityError, LayoutError, AllocationError, SourceMapError {
    SystemAllocator allocator;
    String(SystemAllocator) source(allocator, text);
    return context.add_source("fixture.jack", source);
}
Vector(Token, SystemAllocator) tokenize(&in FrontendContext context, SourceId source)
    raises CapacityError, LayoutError, AllocationError, SourceMapError {
    &in SourceMap sources = context.source_map();
    &in u8[] bytes = sources.bytes(source);
    SystemAllocator allocator;
    return lex(SystemAllocator, source, bytes, allocator);
}
ParsedFileId parse_source(&inout FrontendContext context, SourceId source,
    &inout DiagnosticBag diagnostics)
    raises CapacityError, LayoutError, AllocationError, SourceMapError,
        BoundsError, ArenaHandleError, SyntaxValidationError, ParseFailed {
    Vector(Token, SystemAllocator) tokens = tokenize(context, source);
    return parse_strict(context, source, tokens, diagnostics);
}
bool is_variable(&in FrontendContext context, NodeRef reference)
    raises FrontendReferenceError {
    &in SyntaxNode node = context.node(reference);
    return node.kind() == SyntaxKind.variable_declaration;
}
'''

MAIN = '''
i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch SourceMapError { return 13; }
    catch BoundsError { return 14; }
    catch ArenaHandleError { return 15; }
    catch SyntaxValidationError { return 16; }
    catch ParseFailed { return 17; }
    catch FrontendReferenceError { return 18; }
}
'''

IDENTITIES = '''
bool rejects_invalid_tree(&in FrontendContext context, SourceId source,
    move Vector(Token, SystemAllocator) tokens, usize mode)
    raises CapacityError, LayoutError, AllocationError, BoundsError, SourceMapError, ArenaHandleError {
    SyntaxBuilder builder(source, tokens);
    SourceSpan span = source_span(source, usize(0), usize(1), usize(1), usize(1));
    ArenaHandle(SyntaxNode) root;
    if (mode == usize(0)) {
        root = builder.insert(make_syntax_node(span, SyntaxData.print_statement(NodeLink.absent)));
    } elif (mode == usize(1)) {
        ArenaHandle(SyntaxNode) child = builder.insert(make_syntax_node(span, SyntaxData.module_declaration(span)));
        root = builder.insert(make_syntax_node(span, SyntaxData.print_statement(NodeLink.present(child))));
    } elif (mode == usize(2)) {
        ArenaHandle(SyntaxNode) child = builder.insert(make_syntax_node(span, SyntaxData.name_expression(span)));
        ArenaHandle(SyntaxNode) binary = builder.insert(make_syntax_node(span,
            SyntaxData.binary_expression(span, SyntaxOperator.add,
                NodeLink.present(child), NodeLink.present(child))));
        root = builder.insert(make_syntax_node(span, SyntaxData.print_statement(NodeLink.present(binary))));
    } elif (mode == usize(3)) {
        SourceSpan outside = source_span(source, usize(1), usize(2), usize(1), usize(2));
        ArenaHandle(SyntaxNode) child = builder.insert(make_syntax_node(outside, SyntaxData.name_expression(outside)));
        root = builder.insert(make_syntax_node(span, SyntaxData.print_statement(NodeLink.present(child))));
    } else {
        root = builder.insert(make_syntax_node(span, SyntaxData.invalid_statement));
    }
    builder.append_root(root);
    ParsedFile file(builder);
    &in SourceMap sources = context.source_map();
    try { validate_syntax(sources, file); }
    catch SyntaxValidationError { return true; }
    return false;
}

i32 run() raises CapacityError, LayoutError, AllocationError, SourceMapError,
    BoundsError, ArenaHandleError, SyntaxValidationError, ParseFailed, FrontendReferenceError {
    FrontendContext first = frontend_context();
    FrontendContext second = frontend_context();
    SourceId source = add_text(first, "i32 first = 1;");
    SourceId other = add_text(second, "i32 other = 2;");
    if (source.equals(other)) { return 22; }
    DiagnosticBag diagnostics(usize(1));
    usize invalid_case = usize(0);
    while (invalid_case < usize(5)) {
        Vector(Token, SystemAllocator) invalid_tokens = tokenize(first, source);
        if (rejects_invalid_tree(first, source, invalid_tokens, invalid_case) == false) { return 24; }
        invalid_case = invalid_case + usize(1);
    }
    Vector(Token, SystemAllocator) missing_eof = tokenize(first, source);
    missing_eof.pop();
    try { parse_strict(first, source, missing_eof, diagnostics); return 23; }
    catch SourceMapError { }
    ParsedFileId original = parse_source(first, source, diagnostics);
    NodeRef reference = first.root(original, usize(0));
    ParsedFileId reparse = parse_source(first, source, diagnostics);
    if (original.equals(reparse)) { return 1; }
    ParsedFileId other_file = parse_source(second, other, diagnostics);
    try { second.file(original); return 2; }
    catch FrontendReferenceError { }
    try { second.node(reference); return 3; }
    catch FrontendReferenceError { }
    try { tokenize(first, other); return 4; }
    catch SourceMapError { }
    FrontendContext moved = move first;
    if (is_variable(moved, reference) == false) { return 5; }
    try { moved.root(original, usize(1)); return 6; }
    catch FrontendReferenceError { }
    Diagnostic existing = locationless_diagnostic(DiagnosticSeverity.error, "test.existing", "existing");
    diagnostics.add(existing);
    parse_source(moved, source, diagnostics);
    SourceId invalid = add_text(moved, "i32 broken = ;");
    usize files_before = moved.file_count();
    try { parse_source(moved, invalid, diagnostics); return 7; }
    catch ParseFailed { }
    try { parse_source(moved, invalid, diagnostics); return 8; }
    catch ParseFailed { }
    if (files_before != moved.file_count() || diagnostics.omitted() == usize(0)) { return 9; }
    Vector(Token, SystemAllocator) tokens = tokenize(moved, invalid);
    ParsedFileId recovered = parse_recovering(moved, invalid, tokens, diagnostics);
    if (moved.file_count() != files_before + usize(1)) { return 20; }
    SourceId deep = add_text(moved, "i32 value = (((1)));");
    Vector(Token, SystemAllocator) deep_tokens = tokenize(moved, deep);
    ParserOptions options(usize(2));
    try { parse_strict_with_options(moved, deep, deep_tokens, diagnostics, options); return 21; }
    catch ParseFailed { }
    return 0;
}
'''

ALLOCATION_FAILURE = '''
extern "c" void arm_failure();
i32 run() raises CapacityError, LayoutError, AllocationError, SourceMapError,
    BoundsError, ArenaHandleError, SyntaxValidationError, ParseFailed, FrontendReferenceError {
    FrontendContext context = frontend_context();
    SourceId source = add_text(context, "void f(i32 value) { print(value + 1); }");
    DiagnosticBag diagnostics(usize(4));
    Vector(Token, SystemAllocator) tokens = tokenize(context, source);
    arm_failure();
    try { parse_strict(context, source, tokens, diagnostics); }
    catch AllocationError {
        if (context.file_count() != usize(0)) { return 2; }
        return 12;
    }
    return 0;
}
'''


class BootstrapFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.entry = cls.root / 'identities.jack'
        cls.entry.write_text(PRELUDE + IDENTITIES + MAIN)
        cls.driver = CompilerDriver(print_handler=None)
        cls.options = CompilationOptions(module_roots=(SELFHOST,))
        cls.program = cls.driver.compile_hir(cls.entry, cls.options)
        failure = cls.root / 'failure.jack'
        failure.write_text(PRELUDE + ALLOCATION_FAILURE + MAIN)
        cls.failure_program = cls.driver.compile_hir(failure, cls.options)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_context_identity_movement_reparse_and_strict_diagnostics(self):
        with redirect_stdout(io.StringIO()):
            status = Interpreter(externs=default_runtime_externs()).eval_hir_program(self.program, ['fixture'])
        self.assertEqual(0, status)
        for backend, optimization in [('c', 0), ('llvm', 0), ('c', 2), ('llvm', 2)]:
            with self.subTest(backend=backend, optimization=optimization):
                executable = self.root / f'identity-{backend}-{optimization}'
                self.driver.compile_executable(self.entry, CompilationOptions(
                    backend=backend, optimization=optimization, output=executable,
                    module_roots=(SELFHOST,),
                ))
                result = subprocess.run([executable], capture_output=True, text=True, timeout=20)
                self.assertEqual((0, '', ''), (result.returncode, result.stdout, result.stderr))

    def test_parser_allocation_failures_release_every_allocation(self):
        failures = [None]
        for fail_at in failures:
            with self.subTest(fail_at=fail_at):
                pointers = []
                armed = False
                count = 0

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
                with redirect_stdout(io.StringIO()):
                    status = Interpreter(externs=externs).eval_hir_program(self.failure_program, ['failure'])
                self.assertEqual(0 if fail_at is None else 12, status)
                self.assertTrue(pointers)
                self.assertTrue(all(not pointer.allocation.live for pointer in pointers))
                if fail_at is None:
                    failures.extend(range(1, count + 1))
