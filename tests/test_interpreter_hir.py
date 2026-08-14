import unittest

from jack.interpreter import Interpreter
from jack.hir_lowering_pass import compile_to_hir
from jack.parser import parse


class HIRInterpreterTests(unittest.TestCase):
    def test_interpreter_executes_hir_program_without_ast_fallback(self):
        interpreter = Interpreter()
        interpreter.eval_hir_program(compile_to_hir(parse('''
            i32 value = 1;
            value = value + 2;
        '''), print_handler=None))

        self.assertEqual(3, interpreter.global_scope.get('value'))
        self.assertFalse(hasattr(interpreter, 'hir_expressions_by_source_id'))
        self.assertFalse(hasattr(interpreter, 'hir_statements_by_source_id'))

    def test_interpreter_evaluates_runtime_expressions_through_hir(self):
        class SpyInterpreter(Interpreter):
            def __init__(self):
                super().__init__()
                self.hir_expression_types = []
                self.hir_statement_types = []

            def _execute_hir_statement(self, statement, scope, allow_return):
                self.hir_statement_types.append(type(statement).__name__)
                return super()._execute_hir_statement(statement, scope, allow_return)

            def _eval_hir_expression(self, expression, scope):
                self.hir_expression_types.append(type(expression).__name__)
                return super()._eval_hir_expression(expression, scope)

        interpreter = SpyInterpreter()
        interpreter.eval_source_ast(parse('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 value = add(2, 3);
        '''))

        self.assertEqual(5, interpreter.global_scope.get('value'))
        self.assertIn('HIRCallExpression', interpreter.hir_expression_types)
        self.assertIn('HIRGlobalVariable', interpreter.hir_statement_types)

    def test_declared_function_body_executes_directly_from_hir(self):
        class SpyInterpreter(Interpreter):
            def __init__(self):
                super().__init__()
                self.hir_return_executions = 0

            def _execute_hir_statement(self, statement, scope, allow_return):
                if type(statement).__name__ == 'HIRReturn':
                    self.hir_return_executions += 1
                return super()._execute_hir_statement(statement, scope, allow_return)

        interpreter = SpyInterpreter()
        interpreter.eval_source_ast(parse('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 value = add(3, 4);
        '''))

        self.assertEqual(7, interpreter.global_scope.get('value'))
        self.assertEqual(1, interpreter.hir_return_executions)

    def test_synthetic_deinit_method_body_executes_directly_from_hir(self):
        class SpyInterpreter(Interpreter):
            def __init__(self):
                super().__init__()
                self.hir_assignment_executions = 0

            def _execute_hir_statement(self, statement, scope, allow_return):
                if type(statement).__name__ == 'HIRAssignment':
                    self.hir_assignment_executions += 1
                return super()._execute_hir_statement(statement, scope, allow_return)

        interpreter = SpyInterpreter()
        interpreter.eval_source_ast(parse('''
            i32 cleaned = 0;

            struct Resource {
                void deinit(&inout self) {
                    cleaned = cleaned + 1;
                }
            }

            void run() {
                Resource resource;
            }

            run();
        '''))

        self.assertEqual(1, interpreter.global_scope.get('cleaned'))
        self.assertEqual(1, interpreter.hir_assignment_executions)


if __name__ == '__main__':
    unittest.main()
