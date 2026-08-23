import io
import unittest
from contextlib import redirect_stdout

from jack.ast_nodes import EnumDeclaration, Match
from jack.cleanup_lowering_pass import lower_hir_static_cleanups
from jack.compile_time_pass import apply_compile_time_pass
from jack.hir_lowering_pass import lower_to_hir
from jack.interpreter import Interpreter
from jack.llvm_emit_pass import emit_hir_llvm
from jack.c_emit_pass import emit_hir_c
from jack.parser import parse, parse_recovering
from jack.semantic_pass import SemanticError, validate_runtime_ast


class SumTypeTests(unittest.TestCase):
    def runtime_ast(self, source: str):
        return apply_compile_time_pass(parse(source))

    def hir(self, source: str):
        return lower_hir_static_cleanups(lower_to_hir(self.runtime_ast(source)))

    def test_parses_generic_union_and_both_match_forms(self):
        ast = parse('''
            union Option(comptime type T) { none; some(move T value); }
            void inspect(&in Option(i32) value) {
                match (value) { .none { } .some(item) { print(item); } }
                i32 result = match (value) { .none => 0, .some(_) => 1, };
            }
        ''')

        self.assertIsInstance(ast[0], EnumDeclaration)
        self.assertIsInstance(ast[1].body[0], Match)
        self.assertIsInstance(ast[1].body[1].expr, Match)

    def test_recovery_retains_variants_after_bad_member(self):
        result = parse_recovering('union E { first; broken(,); later; }')

        self.assertTrue(result.diagnostics)
        self.assertEqual(['first', 'broken', 'later'], [v.name for v in result.statements[0].variants])

    def test_requires_explicit_ownership_for_owned_place(self):
        ast = self.runtime_ast('union E { a; } void f() { E value = E.a; match (value) { .a { } } }')
        with self.assertRaisesRegex(SemanticError, 'requires &in, &inout, or move'):
            validate_runtime_ast(ast)

    def test_requires_exhaustive_nonduplicate_arms(self):
        ast = self.runtime_ast('union E { a; b; } void f(&in E value) { match (value) { .a { } } }')
        with self.assertRaisesRegex(SemanticError, 'Non-exhaustive match'):
            validate_runtime_ast(ast)

    def test_rejects_infinitely_sized_enum(self):
        ast = self.runtime_ast('union Recursive { next(move Recursive value); }')
        with self.assertRaisesRegex(SemanticError, 'Infinitely sized aggregate layout'):
            validate_runtime_ast(ast)

    def test_generic_consuming_match_executes(self):
        program = lower_to_hir(self.runtime_ast('''
            union Option(comptime type T) { none; some(move T value); }
            void run() {
                Option(i32) value = Option(i32).some(7);
                i32 result = match (move value) { .none => 0, .some(item) => item, };
                print(result);
            }
            run();
        '''))
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_hir_program(program)
        self.assertEqual('result = 7\n', output.getvalue())

    def test_c_emits_tagged_union_and_switch(self):
        source = emit_hir_c(self.hir('''
            union E { a; b(i32 value); }
            E item = E.b(3);
            match (&in item) { .a { } .b(value) { print(value); } }
        '''))
        self.assertIn('uint32_t jack_tag;', source)
        self.assertIn('union {', source)
        self.assertIn('switch (', source)

    def test_llvm_emits_tagged_storage_and_switch(self):
        source = emit_hir_llvm(self.hir('''
            union E { a; b(i32 value); }
            E item = E.b(3);
            i32 result = match (&in item) { .a => 0, .b(value) => value, };
        '''))
        self.assertIn('switch i32', source)
        self.assertIn('E$variant$b', source)


if __name__ == '__main__':
    unittest.main()
