import unittest
import subprocess
import tempfile
from pathlib import Path

from jack.ast_nodes import CompositeExpression, UnaryExpression, VariableDeclaration
from jack.compile_time_pass import apply_compile_time_pass
from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.hir_lowering_pass import compile_to_hir
from jack.interpreter import Interpreter
from jack.jack_emit_pass import emit_jack
from jack.parser import parse, parse_recovering
from jack.semantic_pass import SemanticError, validate_runtime_ast


class BitwiseOperatorTests(unittest.TestCase):
    def test_parser_precedence_and_source_emission(self):
        declaration = parse('u8 value = ~u8(1) & u8(7) << u8(2) | u8(1);')[0]
        self.assertIsInstance(declaration, VariableDeclaration)
        self.assertIsInstance(declaration.expr, CompositeExpression)
        self.assertEqual('|', declaration.expr.operator)
        self.assertEqual('&', declaration.expr.left.operator)
        self.assertIsInstance(declaration.expr.left.left, UnaryExpression)
        self.assertEqual('~', declaration.expr.left.left.operator)
        self.assertEqual('<<', declaration.expr.left.right.operator)
        self.assertIn('~u8(1) & u8(7) << u8(2) | u8(1)', emit_jack([declaration]))

    def test_comptime_folds_fixed_width_operations(self):
        compiled = apply_compile_time_pass(parse('''
            comptime u8 mask = ~u8(15);
            comptime i8 signed = i8(0 - 8) >> u8(2);
            comptime u8 too_far = u8(7) << u8(8);
            u8 a = mask;
            i8 b = signed;
            u8 c = too_far;
        '''))
        values = {node.name: node.expr.value for node in compiled}
        self.assertEqual({'a': 240, 'b': -2, 'c': 0}, values)

    def test_interpreter_uses_fixed_width_shift_rules(self):
        interpreter = Interpreter()
        interpreter.eval_hir_program(compile_to_hir(parse('''
            u8 anded = u8(243) & u8(63);
            u8 ored = u8(128) | u8(1);
            u8 xored = u8(170) ^ u8(255);
            i8 arithmetic = i8(0 - 4) >> u8(1);
            u8 boundary = u8(1) << u8(8);
        '''), print_handler=None))
        self.assertEqual(0x33, interpreter.global_scope.get('anded'))
        self.assertEqual(0x81, interpreter.global_scope.get('ored'))
        self.assertEqual(0x55, interpreter.global_scope.get('xored'))
        self.assertEqual(-2, interpreter.global_scope.get('arithmetic'))
        self.assertEqual(0, interpreter.global_scope.get('boundary'))

    def test_semantics_require_matching_integer_operands_and_unsigned_shift(self):
        with self.assertRaisesRegex(SemanticError, 'same integer type'):
            validate_runtime_ast(parse('u8 value = u8(1) | u16(2);'))
        with self.assertRaisesRegex(SemanticError, 'unsigned count'):
            validate_runtime_ast(parse('u8 value = u8(1) << i8(2);'))

    def test_recovery_makes_progress_after_malformed_shift(self):
        result = parse_recovering('u8 broken = u8(1) << ;\nu8 valid = u8(2);')
        self.assertTrue(result.diagnostics)
        self.assertTrue(any(
            isinstance(node, VariableDeclaration) and node.name == 'valid'
            for node in result.statements
        ))

    def test_c_and_llvm_observe_the_same_shift_contract(self):
        source = '''
            i8 arithmetic = i8(0 - 8) >> u8(2);
            u8 boundary = u8(7) << usize(8);
            u8 mask = ~u8(15) & u8(255);
            print(arithmetic);
            print(boundary);
            print(mask);
        '''
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entry = root / 'main.jack'
            entry.write_text(source)
            for backend in ('c', 'llvm'):
                with self.subTest(backend=backend):
                    executable = root / backend
                    CompilerDriver(print_handler=None).compile_executable(
                        entry,
                        CompilationOptions(backend=backend, output=executable),
                    )
                    completed = subprocess.run(
                        [executable], capture_output=True, text=True, check=False
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    self.assertEqual(
                        'arithmetic = -2\nboundary = 0\nmask = 240\n',
                        completed.stdout,
                    )


if __name__ == '__main__':
    unittest.main()
