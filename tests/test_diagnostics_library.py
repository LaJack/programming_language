import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.interpreter import Interpreter
from jack.runtime_externs import default_runtime_externs


ROOT = Path(__file__).resolve().parents[1]
SELFHOST_ROOT = ROOT / 'selfhost'


PROGRAM = r'''
import bootstrap.diagnostics;
import bootstrap.source;
import std.collections.vector;
import std.io;
import std.memory;
import std.string;

i32 run() raises CapacityError, LayoutError, AllocationError, Utf8Error,
    IoError, SourceMapError, BoundsError
{
    SystemAllocator first_allocator;
    String(SystemAllocator) first_text(
        first_allocator, "alpha\tbeta\r\nsecond\nthird"
    );
    SystemAllocator second_allocator;
    String(SystemAllocator) second_text(second_allocator, "other\n");
    SourceMap sources;
    SourceId first = sources.add("first.jack", first_text);
    SourceId second = sources.add("second.jack", second_text);

    SourceSpan primary = source_span(
        first, usize(6), usize(10), usize(1), usize(7)
    );
    SourceSpan related = source_span(
        second, usize(0), usize(5), usize(1), usize(1)
    );
    Diagnostic diagnostic = source_diagnostic(
        DiagnosticSeverity.error, "parse.expected-token", primary,
        "expected\tidentifier"
    );
    diagnostic.add_secondary(related, "declared\nhere");
    diagnostic.add_note("names use \\ escapes");

    DiagnosticBag diagnostics(usize(1));
    diagnostics.add(diagnostic);
    Diagnostic duplicate = source_diagnostic(
        DiagnosticSeverity.error, "parse.expected-token", primary,
        "duplicate wording is ignored"
    );
    diagnostics.add(duplicate);
    Diagnostic omitted = locationless_diagnostic(
        DiagnosticSeverity.warning, "parse.recovered", "recovered"
    );
    diagnostics.add(omitted);

    String(SystemAllocator) human = render_diagnostics(
        sources, diagnostics, DiagnosticFormat.human
    );
    write_stdout(human.as_str());
    write_stdout("---\n");
    String(SystemAllocator) stable = render_diagnostics(
        sources, diagnostics, DiagnosticFormat.stable
    );
    write_stdout(stable.as_str());
    return 0;
}

i32 main(&in str[] arguments) {
    try { return run(); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch Utf8Error { return 13; }
    catch IoError { return 14; }
    catch SourceMapError { return 15; }
    catch BoundsError { return 16; }
}
'''


EXPECTED = (
    'error[parse.expected-token]: expected\tidentifier\n'
    ' --> first.jack:1:7\n'
    '  |\n'
    '1 | alpha    beta\n'
    '  |          ^^^^\n'
    ' --> second.jack:1:1\n'
    '  |\n'
    '1 | other\n'
    '  | ----- declared\nhere\n'
    ' = note: names use \\ escapes\n'
    ' = note: 1 additional diagnostics omitted\n'
    '---\n'
    'diagnostic\t0\terror\tparse.expected-token\tfirst.jack\t6\t10\t1\t7\t'
    'expected\\tidentifier\n'
    'label\t0\tsecondary\tsecond.jack\t0\t5\t1\t1\tdeclared\\nhere\n'
    'note\t0\tnames use \\\\ escapes\n'
    'omitted\t1\n'
)


class DiagnosticsLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.entry = cls.root / 'main.jack'
        cls.entry.write_text(PROGRAM)
        driver = CompilerDriver(print_handler=None)
        cls.program = driver.compile_hir(
            cls.entry,
            CompilationOptions(module_roots=(SELFHOST_ROOT,)),
        )
        cls.executables = {}
        for backend, optimization in (('c', 0), ('llvm', 0), ('llvm-o2', 2)):
            output = cls.root / backend
            driver.compile_executable(
                cls.entry,
                CompilationOptions(
                    backend='llvm' if backend == 'llvm-o2' else backend,
                    output=output,
                    optimization=optimization,
                    module_roots=(SELFHOST_ROOT,),
                ),
            )
            cls.executables[backend] = output

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_human_and_stable_rendering_match_all_runtimes(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = Interpreter(
                externs=default_runtime_externs()
            ).eval_hir_program(self.program, ['diagnostics-test'])
        self.assertEqual(0, status)
        self.assertEqual(EXPECTED, output.getvalue())

        for backend, executable in self.executables.items():
            with self.subTest(backend=backend):
                completed = subprocess.run(
                    [executable], capture_output=True, text=True, check=False
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual(EXPECTED, completed.stdout)

    def test_invalid_span_is_rejected_deterministically(self):
        source = r'''
import bootstrap.diagnostics;
import bootstrap.source;
import std.collections.vector;
import std.memory;
import std.string;

void run() raises CapacityError, LayoutError, AllocationError, Utf8Error,
    SourceMapError, BoundsError
{
    SystemAllocator allocator;
    String(SystemAllocator) text(allocator, "short");
    SourceMap sources;
    SourceId source = sources.add("short.jack", text);
    Diagnostic diagnostic = source_diagnostic(
        DiagnosticSeverity.error, "test.invalid", source_span(
            source, usize(0), usize(99), usize(1), usize(1)
        ), "bad span"
    );
    DiagnosticBag diagnostics(usize(1));
    diagnostics.add(diagnostic);
    String(SystemAllocator) rendered = render_diagnostics(
        sources, diagnostics, DiagnosticFormat.human
    );
}

try { run(); }
catch CapacityError { print("allocation"); }
catch LayoutError { print("layout"); }
catch AllocationError { print("allocation"); }
catch Utf8Error { print("utf8"); }
catch SourceMapError { print("source map error"); }
catch BoundsError { print("bounds"); }
'''
        entry = self.root / 'invalid-span.jack'
        entry.write_text(source)
        program = CompilerDriver(print_handler=None).compile_hir(
            entry, CompilationOptions(module_roots=(SELFHOST_ROOT,))
        )
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
        self.assertEqual('"source map error" = source map error\n', output.getvalue())

    def test_source_map_ids_lines_and_bytes_survive_map_movement(self):
        source = r'''
import bootstrap.source;
import std.collections.vector;
import std.memory;
import std.string;

void inspect(move SourceMap sources, SourceId empty, SourceId text)
    raises SourceMapError, BoundsError
{
    print(sources.len());
    print(sources.line_count(empty));
    print(sources.line_count(text));
    print(sources.line_start(text, usize(2)));
    print(sources.line_end(text, usize(1)));
    print(sources.line_end(text, usize(2)));
    print(sources.byte_at(text, usize(0)));
    print(sources.line_for_offset(text, usize(4)));
}

void run() raises CapacityError, LayoutError, AllocationError, Utf8Error,
    SourceMapError, BoundsError
{
    SourceMap sources;
    SystemAllocator empty_allocator;
    String(SystemAllocator) empty_text(empty_allocator, "");
    SourceId empty = sources.add("empty.jack", empty_text);
    SystemAllocator text_allocator;
    String(SystemAllocator) text(text_allocator, "é\r\n");
    SourceId nonempty = sources.add("utf8.jack", text);
    inspect(sources, empty, nonempty);
}

try { run(); }
catch CapacityError { }
catch LayoutError { }
catch AllocationError { }
catch Utf8Error { }
catch SourceMapError { }
catch BoundsError { }
'''
        entry = self.root / 'source-map.jack'
        entry.write_text(source)
        program = CompilerDriver(print_handler=None).compile_hir(
            entry, CompilationOptions(module_roots=(SELFHOST_ROOT,))
        )
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter(externs=default_runtime_externs()).eval_hir_program(program)
        self.assertEqual(
            'sources.len() = 2\n'
            'sources.line_count(empty) = 1\n'
            'sources.line_count(text) = 2\n'
            'sources.line_start(text, usize(2)) = 4\n'
            'sources.line_end(text, usize(1)) = 2\n'
            'sources.line_end(text, usize(2)) = 4\n'
            'sources.byte_at(text, usize(0)) = 195\n'
            'sources.line_for_offset(text, usize(4)) = 2\n',
            output.getvalue(),
        )


if __name__ == '__main__':
    unittest.main()
