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
PROGRAM = '''
import bootstrap.modules;
import bootstrap.project;
import bootstrap.names;
import bootstrap.frontend;
import bootstrap.source;
import bootstrap.syntax;
import bootstrap.diagnostics;
import std.collections.vector;
import std.collections.arena;
import std.memory;
import std.string;

i32 run(str entry, str root)
    raises CapacityError, LayoutError, AllocationError, BoundsError, SourceMapError,
        ArenaHandleError, SyntaxValidationError, FrontendReferenceError,
        ProjectReferenceError, NameReferenceError, Utf8Error {
    ProjectOptions options = project_options();
    if (root != "-") { options.add_module_root(root); }
    options.add_stub("hardware", "stubs.hardware");
    options.add_stub("stubs.hardware", "must_not_chain");
    options.add_stub("bad_stub", "../escape");
    DiagnosticBag diagnostics(usize(100));
    FrontendProject project = load_module_graph(entry, options, diagnostics);
    print(project.module_count());
    print(project.import_count());
    usize index = usize(0);
    while (index < project.module_count()) {
        ModuleId id = project.module_at(index);
        &in ModuleRecord record = project.module_record(id);
        NameLink name = record.module_name();
        match (&in name) {
            .none { print("<unknown>"); }
            .present(value) { print(project.name(value)); }
        }
        index = index + usize(1);
    }
    index = usize(0);
    while (index < diagnostics.len()) {
        &in Diagnostic diagnostic = diagnostics.get(index);
        print(diagnostic.code_text());
        index = index + usize(1);
    }
    if (project.has_errors()) { return 1; }
    return 0;
}
i32 main(&in str[] arguments) {
    try { return run(arguments[1], arguments[2]); }
    catch CapacityError { return 10; }
    catch LayoutError { return 11; }
    catch AllocationError { return 12; }
    catch BoundsError { return 13; }
    catch SourceMapError { return 14; }
    catch ArenaHandleError { return 15; }
    catch SyntaxValidationError { return 16; }
    catch FrontendReferenceError { return 17; }
    catch ProjectReferenceError { return 18; }
    catch NameReferenceError { return 19; }
    catch Utf8Error { return 20; }
}
'''


class BootstrapModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.entry = cls.root / 'loader.jack'
        cls.entry.write_text(PROGRAM)
        cls.driver = CompilerDriver(print_handler=None)
        cls.options = CompilationOptions(module_roots=(ROOT / 'selfhost',))
        cls.program = cls.driver.compile_hir(cls.entry, cls.options)
        cls.executables = {}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_graph(self, files, *, roots='-', native=False, symlinks=None):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            root = Path(directory)
            for name, source in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            for name, target in (symlinks or {}).items():
                (root / name).symlink_to(target)
            entry = root / 'entry.jack'
            module_root = '-' if roots == '-' else str(root / roots)
            output = io.StringIO()
            pointers = []

            def allocate(size):
                pointer = malloc(size)
                pointers.append(pointer)
                return pointer

            with redirect_stdout(output):
                status = Interpreter(externs={**default_runtime_externs(), 'malloc': allocate}).eval_hir_program(
                    self.program, ['loader', str(entry), module_root]
                )
            self.assertTrue(all(not pointer.allocation.live for pointer in pointers))
            if native:
                for backend, optimization in (('c', 0), ('llvm', 0), ('llvm', 2)):
                    with self.subTest(backend=backend, optimization=optimization):
                        key = (backend, optimization)
                        if key not in self.executables:
                            executable = self.root / f'{backend}-{optimization}'
                            self.driver.compile_executable(self.entry, CompilationOptions(
                                module_roots=self.options.module_roots, output=executable,
                                backend=backend, optimization=optimization,
                            ))
                            self.executables[key] = executable
                        result = subprocess.run(
                            [str(self.executables[key]), str(entry), module_root],
                            capture_output=True, text=True, timeout=30,
                        )
                        self.assertEqual((status, output.getvalue(), ''),
                                         (result.returncode, result.stdout, result.stderr))
            return status, output.getvalue()

    def test_diamond_stubs_comments_and_no_comptime_execution(self):
        status, output = self.run_graph({
            'entry.jack': 'module app; import left; import right; import hardware; comptime print("EXECUTED");',
            'left.jack': 'module left; import common; pub i32 left_value = 1;',
            'right.jack': 'module right; import common; pub i32 right_value = 2;',
            'common.jack': 'pub i32 shared = 3;',
            'stubs/hardware.jk': 'module stubs /* gap */ . hardware; pub i32 pin = 4;',
        }, native=True)
        self.assertEqual(0, status, output)
        self.assertIn('project.module_count() = 5', output)
        self.assertIn('project.import_count() = 5', output)
        self.assertNotIn('EXECUTED', output)
        names = [line.split(' = ', 1)[1] for line in output.splitlines() if line.startswith('project.name')]
        self.assertEqual(['app', 'left', 'common', 'right', 'stubs.hardware'], names)

    def test_cycles_and_missing_imports_do_not_hide_independent_modules(self):
        status, output = self.run_graph({
            'entry.jack': 'module app; import cycle; import missing; import good;',
            'cycle.jack': 'module cycle; import app;',
            'good.jack': 'module good; pub i32 value = 1;',
        })
        self.assertEqual(1, status)
        self.assertIn('module.cycle', output)
        self.assertIn('module.not-found', output)
        self.assertIn('project.name(value) = good', output)

    def test_entry_root_and_suffix_precedence_and_explicit_roots(self):
        status, output = self.run_graph({
            'entry.jack': 'import selected; import external;',
            'selected.jack': 'module selected;',
            'selected.jk': 'module wrong;',
            'roots/selected.jack': 'module wrong;',
            'roots/external.jk': 'module external;',
        }, roots='roots')
        self.assertEqual(0, status, output)
        self.assertIn('project.name(value) = entry', output)
        self.assertIn('project.name(value) = external', output)

    def test_malformed_header_retains_imports_without_guessing_identity(self):
        status, output = self.run_graph({
            'entry.jack': 'module ; import good;',
            'good.jack': 'module good;',
        })
        self.assertEqual(1, status)
        self.assertIn('<unknown>', output)
        self.assertIn('project.name(value) = good', output)

    def test_malformed_import_does_not_probe_or_hide_later_imports(self):
        status, output = self.run_graph({
            'entry.jack': 'module app; import ; import good;',
            'good.jack': 'module good;',
        }, native=True)
        self.assertEqual(1, status, output)
        self.assertIn('project.name(value) = good', output)
        self.assertNotIn('module.not-found', output)
        self.assertNotIn('module.import-order', output)

    def test_invalid_stub_is_rejected_before_path_resolution(self):
        status, output = self.run_graph({
            'entry.jack': 'import bad_stub; import good;',
            'good.jack': 'module good;',
        })
        self.assertEqual(1, status, output)
        self.assertIn('module.invalid-name', output)
        self.assertNotIn('module.not-found', output)
        self.assertIn('project.name(value) = good', output)

    def test_truncated_import_does_not_resolve_its_valid_prefix(self):
        for malformed in ('import foo..bar;', 'import foo import good;'):
            with self.subTest(malformed=malformed):
                source = malformed
                if source.endswith('good;') is False:
                    source += ' import good;'
                status, output = self.run_graph({
                    'entry.jack': source,
                    'foo.jack': 'module foo;',
                    'good.jack': 'module good;',
                })
                self.assertEqual(1, status, output)
                self.assertNotIn('project.name(value) = foo', output)
                self.assertIn('project.name(value) = good', output)
                self.assertNotIn('module.import-order', output)

    def test_symlink_canonical_identity_conflict(self):
        status, output = self.run_graph({
            'entry.jack': 'import original; import alternate; import good;',
            'original.jack': 'module original;',
            'good.jack': 'module good;',
        }, symlinks={'alternate.jack': 'original.jack'})
        self.assertEqual(1, status, output)
        self.assertIn('module.identity-conflict', output)
        self.assertIn('project.module_count() = 3', output)

    def test_mismatched_name_keeps_file_without_registering_false_identity(self):
        status, output = self.run_graph({
            'entry.jack': 'import requested; import good;',
            'requested.jack': 'module different;',
            'good.jack': 'module good;',
        })
        self.assertEqual(1, status, output)
        self.assertIn('module.name-mismatch', output)
        self.assertIn('<unknown>', output)
        self.assertNotIn('project.name(value) = different', output)

    def test_missing_entry_is_a_diagnostic(self):
        status, output = self.run_graph({})
        self.assertEqual(1, status, output)
        self.assertIn('project.module_count() = 0', output)
        self.assertIn('module.entry', output)


if __name__ == '__main__':
    unittest.main()
