import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.ast_nodes import Block
from jack.compile_time_pass import CompileTimeError, CompileTimePass, ComptimeEffects
from jack.module_loader import load_source_file
from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.hir_lowering_pass import HIRLoweringError, compile_to_hir
from jack.hir_nodes import HIRBlock
from jack.interpreter import Interpreter
from jack.jack_emit_pass import JackEmitPass
from jack.parser import ParseError, parse, parse_recovering
from jack.llvm_emit_pass import emit_hir_llvm
from jack.semantic_pass import SemanticError


class LexicalBlockTests(unittest.TestCase):
    def test_parse_spans_roundtrip_and_recovery(self):
        source = '{ { } i32 value = 1; } comptime { i32 local = 2; }'
        ast = parse(source, source_path='/tmp/blocks.jack')
        self.assertTrue(all(isinstance(node, Block) for node in ast))
        self.assertEqual('{ { } i32 value = 1; }', source[ast[0].span.start_offset:ast[0].span.end_offset])
        self.assertEqual('/tmp/blocks.jack', ast[0].span.source_path)
        self.assertTrue(ast[1].comptime)
        self.assertEqual(ast, parse(JackEmitPass().emit(ast)))
        recovered = parse_recovering('{ i32 bad = ; i32 good = 2; } i32 later = 3;')
        self.assertEqual(1, len(recovered.diagnostics))
        self.assertEqual('good', recovered.statements[0].body[1].name)
        self.assertEqual('later', recovered.statements[1].name)
        with self.assertRaises(ParseError):
            parse('{')

    def test_comptime_scope_mutation_shadowing_and_return(self):
        source = '''
            comptime i32 total = 1;
            comptime { i32 local = 2; { total = total + local; } }
            { i32 total = 7; print(total); }
            print(total);
        '''
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_hir_program(compile_to_hir(parse(source)))
        self.assertEqual('total = 7\ntotal = 3\n', output.getvalue())
        for source in (
            'comptime { return; }',
            'i32 runtime = 2; comptime { print(runtime); }',
            'comptime { i32 local = 2; } comptime print(local);',
        ):
            with self.subTest(source=source), self.assertRaises(CompileTimeError):
                CompileTimePass().apply(parse(source))

    def test_blocks_reject_module_declarations_and_leaking_locals(self):
        for source in (
            '{ i32 local = 2; } print(local);',
            '{ void nested() { } }',
            '{ struct Nested { } }',
            '{ pub i32 exported = 1; }',
            '{ import other; }',
        ):
            with self.subTest(source=source), self.assertRaises((SemanticError, CompileTimeError)):
                compile_to_hir(parse(source))
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'entry.jack'
            entry.write_text('{} i32 main(&in str[] arguments) { return 0; }')
            with self.assertRaises(HIRLoweringError):
                CompilerDriver().compile_hir(entry, CompilationOptions())

    def test_comptime_cleanup_moves_overwrites_and_propagation(self):
        source = '''
            struct Failure { }
            struct Resource { i32 tag; deinit(move self) { print(self.tag); } }
            void consume(move Resource value) { }
            void fail() raises Failure { { Resource r = Resource { tag = 3 }; raise Failure { }; } }
            Resource make() { { Resource r = Resource { tag = 4 }; return r; } }
            comptime {
                Resource first = Resource { tag = 1 };
                first = Resource { tag = 2 };
                try { fail(); } catch Failure { }
                Resource returned = make();
                consume(returned);
                Resource second = move first;
                first = Resource { tag = 5 };
            }
        '''
        lines = []
        CompileTimePass(print_handler=lines.append).apply(parse(source))
        self.assertEqual(['self.tag = 1', 'self.tag = 3', 'self.tag = 4',
                          'self.tag = 2', 'self.tag = 5'], lines)

    def test_comptime_rejects_use_after_move_and_local_borrow_return(self):
        for source in (
            'comptime { i32 a = 1; i32 b = move a; print(a); }',
            '&in i32 bad() { { i32 local = 1; return &in local; } } comptime { &in i32 value = bad(); }',
            'struct R { deinit(move self) {} } comptime { R first; R second = first; }',
        ):
            with self.subTest(source=source), self.assertRaises(CompileTimeError):
                CompileTimePass().apply(parse(source))

    def test_comptime_failed_push_destroys_consumed_value(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'capacity.jack'
            entry.write_text('''
                import std.memory;
                import std.collections.vector;
                struct Resource { i32 tag; deinit(move self) { print(self.tag); } }
                comptime {
                    StaticAllocator(Resource, 0) allocator;
                    Vector(Resource, StaticAllocator(Resource, 0)) values(allocator, 0);
                    Resource value = Resource { tag = 9 };
                    try { values.push(value); } catch CapacityError { }
                }
            ''')
            lines = []
            CompileTimePass(print_handler=lines.append).apply(load_source_file(entry))
            self.assertEqual(['self.tag = 9'], lines)

    def test_comptime_io_and_owned_string_views(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'input.txt'
            data.write_text('hello')
            entry = root / 'read.jack'
            prefix = 'import std.io; import std.memory; import std.string;\n'
            read = f'''
                File input = open_read("{data}");
                SystemAllocator allocator;
                String(SystemAllocator) source = read_to_string(SystemAllocator, input, allocator);
            '''
            entry.write_text(prefix + 'comptime { ' + read + 'str text = source.as_str(); print(text); }')
            effects = ComptimeEffects()
            lines = []
            CompileTimePass(print_handler=lines.append, effects=effects).apply(load_source_file(entry))
            self.assertEqual(['text = hello'], lines)
            self.assertIn(data.resolve(), effects.dependencies)
            for suffix in (
                'str text = source.as_str(); String(SystemAllocator) other = move source;',
                'str text = source.as_str(); source.clear();',
                'leaked = source.as_str();',
            ):
                entry.write_text(prefix + 'comptime str leaked = ""; comptime { ' + read + suffix + ' }')
                with self.subTest(suffix=suffix), self.assertRaisesRegex(CompileTimeError, 'borrow'):
                    CompileTimePass(print_handler=None).apply(load_source_file(entry))

    def test_borrow_release_and_definite_ownership(self):
        compile_to_hir(parse('''
            void run() {
                i32 owner = 1;
                { &in i32 borrow = &in owner; print(borrow); }
                owner = 2;
                { i32 moved = move owner; }
                owner = 3;
                print(owner);
            }
        '''))
        with self.assertRaises(SemanticError):
            compile_to_hir(parse('void run() { i32 x = 1; { i32 y = move x; } print(x); }'))
        with self.assertRaises(SemanticError):
            compile_to_hir(parse('&in i32 bad() { { i32 local = 1; return &in local; } }'))
        compile_to_hir(parse('unsafe void update(*inout i32 pointer) { { *pointer = 2; } }'))
        with self.assertRaises(SemanticError):
            compile_to_hir(parse('void update(*inout i32 pointer) { { *pointer = 2; } }'))

    def test_blocks_emit_lexical_debug_scopes_without_conditionals(self):
        program = compile_to_hir(parse('{ i32 local = 1; print(local); }', source_path='/tmp/blocks.jack'))
        ir = emit_hir_llvm(program, debug=True)
        self.assertNotIn('br i1', ir)
        self.assertIn('DILexicalBlock', ir)
        self.assertIn('name: "local"', ir)

    def test_runtime_cleanup_matches_native_backends(self):
        source = '''
            struct Failure { }
            struct Resource { i32 tag; deinit(move self) { print(self.tag); } }
            void fail() raises { { Resource r = Resource { tag = 3 }; raise Failure { }; } }
            i32 value() { { Resource r = Resource { tag = 4 }; return 9; } }
            void relay() raises {
                try { { Resource r = Resource { tag = 6 }; fail(); } }
                catch Failure { { Resource r = Resource { tag = 7 }; rethrow; } }
            }
            { Resource outer = Resource { tag = 1 };
              { Resource inner = Resource { tag = 2 }; }
              try { fail(); } catch Failure { print(value()); }
              try { relay(); } catch Failure { }
              for (i32 i = 0; i < 2; i = i + 1) { { Resource r = Resource { tag = 8 }; } }
            }
        '''
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'blocks.jack'
            entry.write_text(source)
            driver = CompilerDriver(print_handler=None)
            program = driver.compile_hir(entry, CompilationOptions())
            self.assertTrue(any(isinstance(node, HIRBlock) for node in program.body))
            output = io.StringIO()
            with redirect_stdout(output):
                Interpreter().eval_hir_program(program)
            expected = output.getvalue()
            self.assertEqual(['2', '3', '4', '3', '6', '7', '8', '8', '1'], [line.rsplit(' = ', 1)[1]
                for line in expected.splitlines() if line.startswith('self.tag')])
            for backend in ('c', 'llvm'):
                for optimization in (0, 2):
                    executable = Path(directory) / f'{backend}-{optimization}'
                    driver.compile_executable(entry, CompilationOptions(
                        backend=backend, optimization=optimization, output=executable))
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                    self.assertEqual((0, expected, ''), (result.returncode, result.stdout, result.stderr))
