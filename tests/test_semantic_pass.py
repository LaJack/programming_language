import unittest

from jack.compile_time_pass import apply_compile_time_pass
from jack.parser import parse
from jack.semantic_pass import SemanticError, validate_runtime_ast


def validate_source(source: str):
    return validate_runtime_ast(apply_compile_time_pass(parse(source), print_handler=None))


class SemanticPassTests(unittest.TestCase):
    def test_accepts_valid_runtime_program(self):
        ast = validate_source('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 y = add(2, 3);
            print(y);
        ''')

        self.assertTrue(ast)

    def test_semantic_error_carries_statement_span(self):
        with self.assertRaises(SemanticError) as caught:
            validate_runtime_ast(parse('i32 value = 1;\n&in i32 ref = &in value;\nvalue = 2;\n'))

        self.assertIn('overlaps live &in borrow', str(caught.exception))
        self.assertEqual(3, caught.exception.span.start_line)
        self.assertEqual(1, caught.exception.span.start_column)

    def test_rejects_runtime_ast_with_raising_deinit(self):
        with self.assertRaisesRegex(SemanticError, 'deinit.*cannot raise'):
            validate_runtime_ast(parse('''
                struct CleanupError {
                    i32 code;
                }

                struct Nope {
                    deinit(&inout self) raises CleanupError {
                        CleanupError err;
                        err.code = 1;
                        raise err;
                    }
                }
            '''))

    def test_rejects_unknown_names(self):
        with self.assertRaisesRegex(SemanticError, 'Unknown name "missing"'):
            validate_source('i32 y = missing;')

    def test_rejects_function_arity_mismatch(self):
        with self.assertRaisesRegex(SemanticError, 'expects 2 argument'):
            validate_source('''
                i32 add(i32 left, i32 right) {
                    return left + right;
                }

                i32 y = add(1);
            ''')

    def test_rejects_non_bool_conditions(self):
        with self.assertRaisesRegex(SemanticError, 'Expected bool for if condition'):
            validate_source('''
                i32 value = 1;
                if (value) {
                    print(value);
                }
            ''')

    def test_rejects_plain_argument_for_borrow_parameter(self):
        with self.assertRaisesRegex(SemanticError, 'requires an explicit borrow'):
            validate_source('''
                void fill(&inout u8[] dst) {
                    dst[0] = 42;
                }

                u8[4] buffer;
                fill(buffer[..]);
            ''')

    def test_rejects_assignment_through_in_slice(self):
        with self.assertRaisesRegex(SemanticError, 'read-only borrow or slice'):
            validate_source('''
                void nope(&in u8[] src) {
                    src[0] = 1;
                }
            ''')

    def test_checks_runtime_array_size_expression(self):
        with self.assertRaisesRegex(SemanticError, 'Unknown name "missing"'):
            validate_source('u8[missing] buffer;')

    def test_allows_local_runtime_sized_arrays(self):
        ast = validate_source('''
            void run(i32 n) {
                u8[n] buffer;
                buffer[0] = 1;
            }
        ''')

        self.assertTrue(ast)

    def test_rejects_indexing_scalar_borrow(self):
        with self.assertRaisesRegex(SemanticError, 'Cannot index value of type "&inout i32"'):
            validate_source('''
                void run(&inout i32 value) {
                    i32 y = value[0];
                }
            ''')

    def test_rejects_slicing_scalar_borrow(self):
        with self.assertRaisesRegex(SemanticError, 'Cannot slice value of type "&in i32"'):
            validate_source('''
                void run(&in i32 value) {
                    &in i32[] slice = &in value[..];
                }
            ''')

    def test_rejects_bare_slice_variable_declaration(self):
        with self.assertRaisesRegex(SemanticError, 'Bare slice type'):
            validate_source('''
                u8[4] buffer;
                u8[] window = buffer[..];
            ''')

    def test_rejects_array_copy_initialization(self):
        with self.assertRaisesRegex(SemanticError, 'Array values cannot be copied'):
            validate_source('''
                u8[2] source;
                u8[2] copy = source;
            ''')


if __name__ == '__main__':
    unittest.main()
