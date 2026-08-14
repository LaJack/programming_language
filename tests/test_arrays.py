import io
import unittest
from contextlib import redirect_stdout

from jack.ast_nodes import BorrowExpression, FunctionCall, IndexExpression, SliceExpression, TypeReference
from jack.c_emit_pass import emit_c
from jack.interpreter import Interpreter
from jack.parser import ParseError, parse
from jack.semantic_pass import SemanticError


class ArrayAndSliceTests(unittest.TestCase):
    def test_parser_reads_array_slice_and_borrow_syntax(self):
        ast = parse('''
            void fill(&inout u8[] dst) {
                dst[0] = 42;
            }

            u8[4] buffer;
            fill(&inout buffer[..]);
        ''')

        function = ast[0]
        parameter_type = function.parameters[0].type
        self.assertEqual(TypeReference('u8', is_slice=True, borrow='inout'), parameter_type)
        self.assertEqual(IndexExpression, type(function.body[0].name))

        declaration = ast[1]
        self.assertEqual('u8', declaration.type.name)
        self.assertEqual(4, declaration.type.array_size.value)

        call = ast[2]
        self.assertEqual(FunctionCall, type(call))
        self.assertEqual(BorrowExpression, type(call.parameters[0]))
        self.assertEqual(SliceExpression, type(call.parameters[0].expr))

    def test_parser_reads_in_out_and_inout_borrow_modes(self):
        ast = parse('''
            void use(&in u8[] src, &out u8[] dst, &inout u8[] scratch) {
            }
        ''')

        parameters = ast[0].parameters
        self.assertEqual(['in', 'out', 'inout'], [parameter.type.borrow for parameter in parameters])

    def test_parser_rejects_old_borrow_keywords(self):
        with self.assertRaisesRegex(ParseError, 'Expected in, out, or inout after &'):
            parse('void fill(&mut u8[] dst) {}')
        with self.assertRaisesRegex(ParseError, 'Expected in, out, or inout after &'):
            parse('void fill(&const u8[] dst) {}')

    def test_interpreter_out_slice_argument_writes_caller_array(self):
        source = '''
            void fill(&out u8[] dst) {
                dst[0] = 11;
            }

            u8[2] buffer;
            fill(&out buffer[..]);
            print(buffer[0]);
        '''

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(parse(source))

        self.assertEqual('buffer[0] = 11\n', output.getvalue())

    def test_interpreter_rejects_read_through_out_slice(self):
        source = '''
            void fill(&out u8[] dst) {
                u8 value = dst[0];
            }
        '''

        with self.assertRaisesRegex(SemanticError, 'write-only borrow or slice'):
            Interpreter().eval_source_ast(parse(source))

    def test_interpreter_inout_slice_argument_mutates_caller_array(self):
        source = '''
            void fill(&inout u8[] dst) {
                dst[0] = 42;
            }

            u8[4] buffer;
            fill(&inout buffer[..]);
            print(buffer[0]);
        '''

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(parse(source))

        self.assertEqual('buffer[0] = 42\n', output.getvalue())

    def test_interpreter_exact_array_borrow_mutates_caller_array(self):
        source = '''
            void set_first(&inout u8[4] dst) {
                dst[0] = 7;
            }

            u8[4] buffer;
            set_first(&inout buffer);
            print(buffer[0]);
        '''

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(parse(source))

        self.assertEqual('buffer[0] = 7\n', output.getvalue())

    def test_interpreter_requires_explicit_borrow_for_borrow_parameters(self):
        source = '''
            void fill(&inout u8[] dst) {
                dst[0] = 42;
            }

            u8[4] buffer;
            fill(buffer[..]);
        '''

        with self.assertRaisesRegex(SemanticError, 'requires an explicit borrow'):
            Interpreter().eval_source_ast(parse(source))

    def test_interpreter_rejects_assignment_through_in_slice(self):
        source = '''
            void nope(&in u8[] src) {
                src[0] = 1;
            }

            u8[4] buffer;
            nope(&in buffer[..]);
        '''

        with self.assertRaisesRegex(SemanticError, 'read-only borrow or slice'):
            Interpreter().eval_source_ast(parse(source))

    def test_c_emit_keeps_inout_slice_code_readable(self):
        source = '''
            void fill(&inout u8[] dst) {
                dst[0] = 42;
            }

            u8[4] buffer;
            fill(&inout buffer[..]);
            print(buffer[0]);
        '''

        c_source = emit_c(parse(source))

        self.assertNotIn('typedef struct jack_slice_u8 {', c_source)
        self.assertIn('uint8_t buffer[4];', c_source)
        self.assertIn('void fill(jack_slice_u8 dst);', c_source)
        self.assertIn('(dst).data[0] = 42;', c_source)
        self.assertIn('fill((jack_slice_u8){ buffer, 4 });', c_source)
        self.assertIn('printf("buffer[0] = %" PRIu8 "\\n", (unsigned int)(buffer[0]));', c_source)

    def test_c_emit_keeps_exact_array_borrow_readable(self):
        source = '''
            void set_first(&inout u8[4] dst) {
                dst[0] = 7;
            }

            u8[4] buffer;
            set_first(&inout buffer);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('void set_first(uint8_t *dst);', c_source)
        self.assertIn('dst[0] = 7;', c_source)
        self.assertIn('set_first(buffer);', c_source)


    def test_interpreter_reads_scalar_in_borrows_in_value_contexts(self):
        source = '''
            u8 pick(&in u8 a, &in u8 b) {
                if (a > b) {
                    return a;
                }
                return b;
            }

            u8 a = 8;
            u8 b = 9;
            u8 c = pick(&in a, &in b);
            print(c);
        '''

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(parse(source))

        self.assertEqual('c = 9\n', output.getvalue())

    def test_c_emit_reads_scalar_in_borrows_in_value_contexts(self):
        source = '''
            &in u8 max(&in u8 a, &in u8 b) {
                if (a > b) {
                    return a;
                }
                return b;
            }

            u8 pick(&in u8 a, &in u8 b) {
                return max(a, b);
            }

            u8 a = 8;
            u8 b = 9;
            u8 c = pick(&in a, &in b);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('if (((*a) > (*b))) {', c_source)
        self.assertIn('return a;', c_source)
        self.assertIn('return (*max(a, b));', c_source)


    def test_c_emit_rejects_by_value_array_parameters(self):
        source = '''
            void take(u8[4] data) {
                print(data[0]);
            }
        '''

        with self.assertRaisesRegex(SemanticError, 'explicit borrow or slice'):
            emit_c(parse(source))

    def test_c_emit_uses_in_slice_type_for_in_borrows(self):
        source = '''
            void show_first(&in u8[] src) {
                print(src[0]);
            }

            u8[4] buffer;
            show_first(&in buffer[..]);
        '''

        c_source = emit_c(parse(source))

        self.assertNotIn('typedef struct jack_in_slice_u8 {', c_source)
        self.assertIn('void show_first(jack_in_slice_u8 src);', c_source)
        self.assertIn('show_first((jack_in_slice_u8){ buffer, 4 });', c_source)

    def test_parser_reads_borrowed_slice_variable_declaration(self):
        ast = parse('''
            u8[4] buffer;
            &inout u8[] window = &inout buffer[..];
        ''')

        declaration = ast[1]
        self.assertEqual(TypeReference('u8', is_slice=True, borrow='inout'), declaration.type)
        self.assertEqual(BorrowExpression, type(declaration.expr))
        self.assertEqual(SliceExpression, type(declaration.expr.expr))

    def test_interpreter_inout_slice_variable_mutates_caller_array(self):
        source = '''
            u8[4] buffer;
            &inout u8[] window = &inout buffer[1..3];
            window[0] = 9;
            print(window[0]);
            print(len(buffer));
            print(len(window));
        '''

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(parse(source))

        self.assertEqual(
            'window[0] = 9\nlen(buffer) = 4\nlen(window) = 2\n',
            output.getvalue(),
        )

    def test_interpreter_rejects_assignment_through_in_slice_variable(self):
        source = '''
            u8[4] buffer;
            &in u8[] window = &in buffer[..];
            window[0] = 9;
        '''

        with self.assertRaisesRegex(SemanticError, 'read-only borrow or slice'):
            Interpreter().eval_source_ast(parse(source))

    def test_c_emit_keeps_borrowed_slice_variables_readable(self):
        source = '''
            u8[4] buffer;
            &inout u8[] window = &inout buffer[1..3];
            window[0] = 9;
            print(len(buffer));
            print(len(window));
        '''

        c_source = emit_c(parse(source))

        self.assertIn('jack_slice_u8 window;', c_source)
        self.assertIn('window = (jack_slice_u8){ &buffer[1], (3 - 1) };', c_source)
        self.assertIn('(window).data[0] = 9;', c_source)
        self.assertIn('printf("len(buffer) = %" PRId32 "\\n", (int32_t)(((int32_t)(4))));', c_source)
        self.assertIn('printf("len(window) = %" PRId32 "\\n", (int32_t)((window).len));', c_source)

    def test_c_emit_can_pass_inout_slice_variable_as_in_slice(self):
        source = '''
            void show(&in u8[] src) {
                print(len(src));
            }

            u8[4] buffer;
            &inout u8[] window = &inout buffer[..];
            show(&in window);
        '''

        c_source = emit_c(parse(source))

        self.assertIn('show((jack_in_slice_u8){ (window).data, (window).len });', c_source)


if __name__ == '__main__':
    unittest.main()
