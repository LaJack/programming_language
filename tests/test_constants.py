import io
import unittest
from contextlib import redirect_stdout

from jack.c_emit_pass import emit_hir_c
from jack.compile_time_pass import CompileTimeError
from jack.hir_lowering_pass import compile_to_hir
from jack.interpreter import Interpreter
from jack.llvm_emit_pass import emit_hir_llvm
from jack.parser import ParseError, parse
from jack.semantic_pass import SemanticError


class ConstantTests(unittest.TestCase):
    SOURCE = '''
        struct Pair { i32 left; i32 right; }
        union Mode { off; on; }
        union Data { empty; value(i32 number); }

        const i32 answer = comptime i32(42);
        const Pair pair = comptime Pair { left = 3, right = 4 };
        const Mode mode = comptime Mode.on;
        const Data data = comptime Data.value(17);
        print(answer);
        print(pair.right);
    '''

    def program(self, source=None):
        return compile_to_hir(parse(source or self.SOURCE), print_handler=None)

    def test_parser_requires_explicit_type_and_comptime_initializer(self):
        declaration = parse('pub const i32 answer = comptime i32(42);')[0]
        self.assertTrue(declaration.public)
        self.assertTrue(declaration.constant)
        self.assertTrue(declaration.comptime_initializer)
        with self.assertRaises(ParseError):
            parse('const i32 answer = i32(42);')

    def test_interpreter_reads_frozen_values(self):
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_hir_program(self.program())
        self.assertEqual('answer = 42\npair.right = 4\n', output.getvalue())

    def test_constants_reject_mutation_move_and_mutable_borrow(self):
        for operation in ('answer = 2;', 'i32 value = move answer;',
                          '&inout i32 value = &inout answer;'):
            with self.subTest(operation=operation):
                with self.assertRaises((CompileTimeError, SemanticError)):
                    self.program(f'const i32 answer = comptime i32(1); {operation}')

    def test_native_backends_emit_read_only_globals(self):
        program = self.program()
        c_source = emit_hir_c(program)
        llvm_source = emit_hir_llvm(program)
        self.assertIn('const int32_t answer = 42;', c_source)
        self.assertIn('@"answer" = constant i32 42', llvm_source)
        self.assertIn('constant %"Pair"', llvm_source)
        self.assertIn('constant %"Mode"', llvm_source)
        self.assertIn('constant %"Data"', llvm_source)


if __name__ == '__main__':
    unittest.main()
