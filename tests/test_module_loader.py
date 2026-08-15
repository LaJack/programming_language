import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jack.ast_nodes import FunctionDeclaration, ImportDeclaration, ModuleDeclaration
from jack.c_emit_pass import emit_c_files, emit_hir_c_files
from jack.cli import main
from jack.cleanup_lowering_pass import lower_hir_static_cleanups
from jack.compile_time_pass import apply_compile_time_pass
from jack.hir_lowering_pass import lower_to_hir
from jack.interpreter import Interpreter
from jack.module_loader import ModuleLoadError, load_source_file, load_source_graph
from jack.parser import parse
from jack.semantic_pass import SemanticError


class ModuleLoaderTests(unittest.TestCase):
    def test_load_source_graph_tracks_dependencies_and_overlays(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library = root / 'math.jack'
            library.write_text('module math;\npub i32 disk_value = 1;\n')
            entry = root / 'main.jack'
            entry.write_text('module app;\nimport math;\ni32 result = overlay_value;\n')

            graph = load_source_graph(
                entry,
                source_overlays={
                    library: 'module math;\npub i32 overlay_value = 2;\n'
                },
            )

        self.assertEqual(('math',), graph.dependencies['app'])
        self.assertEqual(('app',), graph.reverse_dependencies['math'])
        self.assertEqual(library.resolve(), graph.modules['math'].path)
        self.assertIn(
            'overlay_value',
            {getattr(node, 'source_name', getattr(node, 'name', None)) for node in graph.ast},
        )

    def test_hir_preserves_module_metadata_for_split_emission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                module app.main;
                import math.ops;

                i32 value = add(2, 3);
            ''')

            runtime_ast = apply_compile_time_pass(
                load_source_file(entry),
                print_handler=None,
            )
            program = lower_hir_static_cleanups(lower_to_hir(runtime_ast))
            files = emit_hir_c_files(program)

        self.assertEqual('app.main', program.entry_module)
        self.assertEqual(['math.ops'], program.module_dependencies['app.main'])
        self.assertIn('#include "math_ops.h"', files['main.c'])
        self.assertIn('int32_t math_ops_add(int32_t left, int32_t right) {', files['math_ops.c'])

    def test_parser_reads_module_import_and_pub_declarations(self):
        ast = parse("""
            module app.main;
            import math.ops;
            import drivers.can as can;
            import protocol.frame.{Frame, Id};

            pub i32 add(i32 left, i32 right) {
                return left + right;
            }
        """)

        self.assertEqual(ModuleDeclaration, type(ast[0]))
        self.assertEqual('app.main', ast[0].name)
        self.assertEqual(ImportDeclaration, type(ast[1]))
        self.assertEqual('math.ops', ast[1].module_name)
        self.assertEqual(ImportDeclaration, type(ast[2]))
        self.assertEqual('drivers.can', ast[2].module_name)
        self.assertEqual('can', ast[2].alias)
        self.assertEqual(ImportDeclaration, type(ast[3]))
        self.assertEqual('protocol.frame', ast[3].module_name)
        self.assertEqual(['Frame', 'Id'], ast[3].symbols)
        self.assertEqual(FunctionDeclaration, type(ast[4]))
        self.assertTrue(ast[4].public)

    def test_load_source_file_flattens_imports_before_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text("""
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            """)
            entry = root / 'main.jack'
            entry.write_text("""
                module app.main;
                import math.ops;

                i32 y = add(2, 3);
            """)

            ast = load_source_file(entry)

        self.assertFalse(any(type(node) in {ModuleDeclaration, ImportDeclaration} for node in ast))
        self.assertEqual(['math$ops$add'], [node.name for node in ast if type(node) is FunctionDeclaration])

        interpreter = Interpreter()
        interpreter.eval_source_ast(ast)
        self.assertEqual(5, interpreter.global_scope.get('y'))

    def test_cli_interpreter_resolves_imported_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text("""
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            """)
            entry = root / 'main.jack'
            entry.write_text("""
                import math.ops;

                i32 y = add(4, 5);
                print(y);
            """)

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-i', str(entry)])

        self.assertEqual(0, status)
        self.assertEqual('y = 9\n', output.getvalue())

    def test_cli_c_mode_resolves_imported_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text("""
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            """)
            entry = root / 'main.jack'
            entry.write_text("""
                import math.ops;

                i32 y = add(4, 5);
            """)

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['-c', str(entry)])

        self.assertEqual(0, status)
        self.assertIn('int32_t math_ops_add(int32_t left, int32_t right)', output.getvalue())
        self.assertIn('y = math_ops_add(4, 5);', output.getvalue())

    def test_emit_c_files_splits_imported_module_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text("""
                module math.ops;

                i32 helper(i32 value) {
                    return value + 1;
                }

                pub i32 add(i32 left, i32 right) {
                    return helper(left + right);
                }
            """)
            entry = root / 'main.jack'
            entry.write_text("""
                import math.ops;

                i32 y = add(4, 5);
            """)

            files = emit_c_files(load_source_file(entry), print_handler=None)

        self.assertEqual({'main.c', 'math_ops.h', 'math_ops.c'}, set(files))
        self.assertIn('#include "math_ops.h"', files['main.c'])
        self.assertIn('y = math_ops_add(4, 5);', files['main.c'])
        self.assertNotIn('int32_t math_ops_add(int32_t left, int32_t right) {', files['main.c'])
        self.assertIn('int32_t math_ops_add(int32_t left, int32_t right);', files['math_ops.h'])
        self.assertNotIn('math_ops_helper', files['math_ops.h'])
        self.assertIn('static int32_t math_ops_helper(int32_t value);', files['math_ops.c'])
        self.assertIn('static int32_t math_ops_helper(int32_t value) {', files['math_ops.c'])
        self.assertIn('int32_t math_ops_add(int32_t left, int32_t right) {', files['math_ops.c'])

    def test_emit_c_files_initializes_imported_module_globals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'config.jack'
            module.write_text("""
                module config;

                i32 secret = 8;
                pub i32 exposed = secret + 5;
            """)
            entry = root / 'main.jack'
            entry.write_text("""
                import config;

                i32 y = exposed;
            """)

            files = emit_c_files(load_source_file(entry), print_handler=None)

        self.assertIn('extern int32_t config_exposed;', files['config.h'])
        self.assertNotIn('config_secret', files['config.h'])
        self.assertIn('static int32_t config_secret;', files['config.c'])
        self.assertIn('int32_t config_exposed;', files['config.c'])
        self.assertIn('void config_init(void);', files['config.h'])
        self.assertIn('void config_init(void) {', files['config.c'])
        self.assertIn('config_secret = 8;', files['config.c'])
        self.assertIn('config_exposed = (config_secret + 5);', files['config.c'])
        self.assertIn('config_init();', files['main.c'])
        self.assertIn('y = config_exposed;', files['main.c'])

    def test_cli_stub_override_replaces_imported_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prod = root / 'hw' / 'spi.jack'
            prod.parent.mkdir()
            prod.write_text("""
                module hw.spi;

                pub i32 read() {
                    return 1;
                }
            """)
            stub = root / 'tests' / 'stubs' / 'spi.jack'
            stub.parent.mkdir(parents=True)
            stub.write_text("""
                module tests.stubs.spi;

                pub i32 read() {
                    return 9;
                }
            """)
            entry = root / 'main.jack'
            entry.write_text("""
                import hw.spi;

                i32 value = read();
                print(value);
            """)

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['--stub', 'hw.spi=tests.stubs.spi', '-i', str(entry)])

        self.assertEqual(0, status)
        self.assertEqual('value = 9\n', output.getvalue())

    def test_module_import_cycles_are_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            left = root / 'left.jack'
            right = root / 'right.jack'
            left.write_text('module left; import right;')
            right.write_text('module right; import left;')
            entry = root / 'main.jack'
            entry.write_text('import left;')

            with self.assertRaisesRegex(ModuleLoadError, 'cycle'):
                load_source_file(entry)

    def test_alias_import_exposes_public_function_through_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import math.ops as ops;

                i32 y = ops.add(2, 3);
            ''')

            interpreter = Interpreter()
            interpreter.eval_source_ast(load_source_file(entry))

        self.assertEqual(5, interpreter.global_scope.get('y'))

    def test_alias_import_does_not_expose_public_function_as_bare_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import math.ops as ops;

                i32 y = add(2, 3);
            ''')

            with self.assertRaisesRegex(SemanticError, 'Unknown function "add"'):
                Interpreter().eval_source_ast(load_source_file(entry))

    def test_alias_import_exposes_public_type_through_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'geometry.jack'
            module.write_text('''
                module geometry;

                pub struct Point {
                    i32 x;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import geometry as geo;

                geo.Point point;
            ''')

            Interpreter().eval_source_ast(load_source_file(entry))

    def test_selective_import_exposes_only_selected_public_symbols(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }

                pub i32 sub(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import math.ops.{add};

                i32 y = sub(5, 2);
            ''')

            with self.assertRaisesRegex(SemanticError, 'Unknown function "sub"'):
                Interpreter().eval_source_ast(load_source_file(entry))

    def test_selective_import_allows_selected_public_symbol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import math.ops.{add};

                i32 y = add(6, 7);
            ''')

            interpreter = Interpreter()
            interpreter.eval_source_ast(load_source_file(entry))

        self.assertEqual(13, interpreter.global_scope.get('y'))

    def test_transitive_imports_are_not_visible_to_importer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inner = root / 'inner.jack'
            inner.write_text('''
                module inner;

                pub i32 source() {
                    return 11;
                }
            ''')
            outer = root / 'outer.jack'
            outer.write_text('''
                module outer;
                import inner;

                pub i32 exposed() {
                    return source();
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import outer;

                i32 y = source();
            ''')

            with self.assertRaisesRegex(SemanticError, 'Unknown function "source"'):
                Interpreter().eval_source_ast(load_source_file(entry))

    def test_imported_public_function_can_use_public_transitive_dependency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inner = root / 'inner.jack'
            inner.write_text('''
                module inner;

                pub i32 source() {
                    return 11;
                }
            ''')
            outer = root / 'outer.jack'
            outer.write_text('''
                module outer;
                import inner;

                pub i32 exposed() {
                    return source();
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import outer;

                i32 y = exposed();
            ''')

            interpreter = Interpreter()
            interpreter.eval_source_ast(load_source_file(entry))

        self.assertEqual(11, interpreter.global_scope.get('y'))

    def test_private_helpers_with_same_name_do_not_collide_across_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            left = root / 'left.jack'
            left.write_text('''
                module left;

                i32 helper() {
                    return 3;
                }

                pub i32 left_value() {
                    return helper();
                }
            ''')
            right = root / 'right.jack'
            right.write_text('''
                module right;

                i32 helper() {
                    return 5;
                }

                pub i32 right_value() {
                    return helper();
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import left;
                import right;

                i32 y = left_value() + right_value();
            ''')

            interpreter = Interpreter()
            interpreter.eval_source_ast(load_source_file(entry))

            c_output = io.StringIO()
            with redirect_stdout(c_output):
                status = main(['-c', str(entry)])

        self.assertEqual(8, interpreter.global_scope.get('y'))
        self.assertEqual(0, status)
        self.assertIn('int32_t left_helper(void)', c_output.getvalue())
        self.assertIn('int32_t right_helper(void)', c_output.getvalue())

    def test_public_import_name_collision_is_reported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            left = root / 'left.jack'
            left.write_text('''
                module left;

                pub i32 value() {
                    return 3;
                }
            ''')
            right = root / 'right.jack'
            right.write_text('''
                module right;

                pub i32 value() {
                    return 5;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import left;
                import right;
            ''')

            with self.assertRaisesRegex(ModuleLoadError, 'Ambiguous imported symbol "value"'):
                load_source_file(entry)

    def test_alias_imports_resolve_public_name_collision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            left = root / 'left.jack'
            left.write_text('''
                module left;

                pub i32 value() {
                    return 3;
                }
            ''')
            right = root / 'right.jack'
            right.write_text('''
                module right;

                pub i32 value() {
                    return 5;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import left as l;
                import right as r;

                i32 y = l.value() + r.value();
            ''')

            interpreter = Interpreter()
            interpreter.eval_source_ast(load_source_file(entry))

        self.assertEqual(8, interpreter.global_scope.get('y'))

    def test_module_declaration_must_appear_before_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text('import left; module main;')

            with self.assertRaisesRegex(ModuleLoadError, 'before imports'):
                load_source_file(entry)

    def test_module_root_adds_an_import_search_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            modules = root / 'modules'
            module = modules / 'math' / 'ops.jack'
            module.parent.mkdir(parents=True)
            module.write_text('''
                module math.ops;

                pub i32 add(i32 left, i32 right) {
                    return left + right;
                }
            ''')
            entry = root / 'src' / 'main.jack'
            entry.parent.mkdir()
            entry.write_text('''
                import math.ops;

                i32 y = add(6, 7);
                print(y);
            ''')

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(['--module-root', str(modules), '-i', str(entry)])

        self.assertEqual(0, status)
        self.assertEqual('y = 13\n', output.getvalue())

    def test_imported_public_function_can_use_private_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                i32 secret() {
                    return 8;
                }

                pub i32 exposed() {
                    return secret();
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import math.ops;

                i32 y = exposed();
            ''')

            interpreter = Interpreter()
            interpreter.eval_source_ast(load_source_file(entry))

        self.assertEqual(8, interpreter.global_scope.get('y'))

    def test_imported_private_function_is_not_visible_to_importer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'math' / 'ops.jack'
            module.parent.mkdir()
            module.write_text('''
                module math.ops;

                i32 secret() {
                    return 8;
                }

                pub i32 exposed() {
                    return secret();
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import math.ops;

                i32 y = secret();
            ''')

            with self.assertRaisesRegex(SemanticError, 'Function "secret" is private'):
                Interpreter().eval_source_ast(load_source_file(entry))

    def test_imported_private_variable_is_not_visible_to_importer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'config.jack'
            module.write_text('''
                module config;

                i32 secret = 8;
                pub i32 exposed = secret + 5;
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import config;

                i32 y = secret;
            ''')

            with self.assertRaisesRegex(SemanticError, 'Name "secret" is private'):
                Interpreter().eval_source_ast(load_source_file(entry))

    def test_imported_private_type_is_not_visible_to_importer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'handles.jack'
            module.write_text('''
                module handles;

                extern "c" type Hidden;
                pub struct Wrapper {
                    &inout Hidden handle;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import handles;

                &inout Hidden handle;
            ''')

            with self.assertRaisesRegex(SemanticError, 'Type "Hidden" is private'):
                Interpreter().eval_source_ast(load_source_file(entry))

    def test_cli_reports_invalid_stub_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entry = Path(tmpdir) / 'main.jack'
            entry.write_text('i32 y = 1;')

            errors = io.StringIO()
            with redirect_stderr(errors):
                status = main(['--stub', 'not-an-override', '-i', str(entry)])

        self.assertEqual(1, status)
        self.assertIn('MODULE=REPLACEMENT', errors.getvalue())


if __name__ == '__main__':
    unittest.main()
