import io
import unittest
from contextlib import redirect_stdout

from jack.ast_nodes import MoveExpression
from jack.compile_time_pass import CompileTimeError, apply_compile_time_pass
from jack.interpreter import Interpreter
from jack.parser import parse
from jack.semantic_pass import SemanticError, validate_runtime_ast


def validate_source(source: str):
    return validate_runtime_ast(
        apply_compile_time_pass(parse(source), print_handler=None)
    )


class OwnershipTests(unittest.TestCase):
    def test_parser_records_move_parameter_and_expression(self):
        ast = parse('''
            void consume(move i32 value) { }
            void run() {
                i32 first = 1;
                i32 second = move first;
            }
        ''')

        self.assertEqual('move', ast[0].parameters[0].passing_mode)
        self.assertIsInstance(ast[1].body[1].expr, MoveExpression)

    def test_move_contract_consumes_even_copyable_values(self):
        with self.assertRaisesRegex(SemanticError, 'because it is moved'):
            validate_source('''
                void consume(move i32 value) { }
                void run() {
                    i32 value = 1;
                    consume(value);
                    print(value);
                }
            ''')

    def test_assignment_reinitializes_a_moved_local(self):
        validate_source('''
            void consume(move i32 value) { }
            void run() {
                i32 value = 1;
                consume(value);
                value = 2;
                print(value);
            }
        ''')

    def test_noncopyable_plain_parameter_is_rejected_after_specialization(self):
        with self.assertRaisesRegex(SemanticError, 'copyable|non-copyable'):
            validate_source('''
                struct Resource {
                    deinit(&inout self) { }
                }

                void inspect(comptime type T, T value) { }
                Resource resource;
                inspect(Resource, resource);
            ''')

    def test_move_cannot_be_combined_with_other_parameter_contracts(self):
        cases = (
            'void bad(comptime move i32 value) { }',
            'void bad(move &in i32 value) { }',
            'extern "c" void bad(move i32 value);',
        )
        for source in cases:
            with self.subTest(source=source), self.assertRaises(
                (SemanticError, CompileTimeError)
            ):
                validate_source(source)

    def test_rejects_partial_and_global_moves(self):
        cases = (
            'i32 global = 1; void run() { i32 value = move global; }',
            '''
                struct Pair { i32 left; i32 right; }
                void run() { Pair pair; i32 value = move pair.left; }
            ''',
            'void run() { i32[2] values; i32 value = move values[0]; }',
        )
        for source in cases:
            with self.subTest(source=source), self.assertRaisesRegex(
                SemanticError, 'whole local|owned locals'
            ):
                validate_source(source)

    def test_branch_and_loop_dataflow_reject_maybe_moved_values(self):
        with self.assertRaisesRegex(SemanticError, 'possibly moved'):
            validate_source('''
                void consume(move i32 value) { }
                void run(bool take) {
                    i32 value = 1;
                    if (take) { consume(value); }
                    print(value);
                }
            ''')

        with self.assertRaisesRegex(SemanticError, 'loop-carried value'):
            validate_source('''
                void consume(move i32 value) { }
                void run(bool repeat) {
                    i32 value = 1;
                    while (repeat) { consume(value); }
                }
            ''')

    def test_interpreter_transfers_returns_and_destroys_once(self):
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(parse('''
                struct Resource {
                    i32 id;
                    init(&inout self, i32 id) { self.id = id; }
                    deinit(&inout self) { print(self.id); }
                }

                Resource make(i32 id) {
                    Resource resource(id);
                    return resource;
                }

                void consume(move Resource resource) { print(resource.id); }

                void run() {
                    Resource resource = make(1);
                    consume(resource);
                    resource = make(2);
                }
                run();
            '''))

        self.assertEqual(
            'resource.id = 1\nself.id = 1\nself.id = 2\n',
            output.getvalue(),
        )


if __name__ == '__main__':
    unittest.main()
