import unittest

from jack.ast_nodes import FunctionDeclaration, TypeDeclaration, TypeReference, VariableDeclaration
from jack.c_emit_pass import emit_c
from jack.compile_time_pass import CompileTimeError, apply_compile_time_pass
from jack.interpreter import EvaluationError, Interpreter
from jack.parser import ParseError, parse
from jack.semantic_pass import SemanticError


class ExternTests(unittest.TestCase):
    def test_parser_reads_extern_function_declaration(self):
        ast = parse('extern i32 host_add(i32 left, i32 right);')

        self.assertEqual(1, len(ast))
        declaration = ast[0]
        self.assertEqual(FunctionDeclaration, type(declaration))
        self.assertTrue(declaration.extern)
        self.assertFalse(declaration.comptime)
        self.assertEqual('host_add', declaration.name)
        self.assertEqual('i32', declaration.return_type.name)
        self.assertEqual([], declaration.body)
        self.assertEqual(['left', 'right'], [parameter.name for parameter in declaration.parameters])

    def test_parser_reads_pub_comptime_extern_function_declaration(self):
        ast = parse('pub comptime extern i32 host_add(i32 left, i32 right);')
        declaration = ast[0]

        self.assertTrue(declaration.public)
        self.assertTrue(declaration.comptime)
        self.assertTrue(declaration.extern)

    def test_parser_reads_extern_c_type_variable_and_function_declarations(self):
        ast = parse('''
            extern "c" type FILE;
            extern "c" &inout FILE stdout;
            extern "c" usize fwrite(&in c_void data, usize size, usize count, &inout FILE stream);
        ''')

        type_declaration = ast[0]
        self.assertEqual(TypeDeclaration, type(type_declaration))
        self.assertTrue(type_declaration.extern)
        self.assertEqual('c', type_declaration.abi)
        self.assertEqual('FILE', type_declaration.name)

        variable_declaration = ast[1]
        self.assertEqual(VariableDeclaration, type(variable_declaration))
        self.assertTrue(variable_declaration.extern)
        self.assertEqual('c', variable_declaration.abi)
        self.assertEqual(TypeReference('FILE', borrow='inout'), variable_declaration.type)

        function_declaration = ast[2]
        self.assertEqual(FunctionDeclaration, type(function_declaration))
        self.assertTrue(function_declaration.extern)
        self.assertEqual('c', function_declaration.abi)
        self.assertEqual('usize', function_declaration.return_type.name)
        self.assertEqual(TypeReference('c_void', borrow='in'), function_declaration.parameters[0].type)
        self.assertEqual(TypeReference('FILE', borrow='inout'), function_declaration.parameters[3].type)

    def test_semantic_rejects_extern_variable_without_abi(self):
        with self.assertRaisesRegex(SemanticError, 'Extern variable "value" must declare an ABI'):
            emit_c(parse('extern i32 value;'))

    def test_interpreter_calls_registered_runtime_extern(self):
        ast = parse('''
            extern i32 host_add(i32 left, i32 right);
            i32 y = host_add(2, 3);
        ''')
        interpreter = Interpreter(
            externs={'host_add': lambda left, right: int(left) + int(right)}
        )

        interpreter.eval_source_ast(ast)

        self.assertEqual(5, interpreter.global_scope.get('y'))

    def test_interpreter_calls_void_str_runtime_extern(self):
        ast = parse('''
            extern void host_write(str text);
            host_write("hello");
        ''')
        messages: list[str] = []
        interpreter = Interpreter(externs={'host_write': messages.append})

        interpreter.eval_source_ast(ast)

        self.assertEqual(['hello'], messages)

    def test_interpreter_rejects_unbound_runtime_extern(self):
        ast = parse('''
            extern i32 host_add(i32 left, i32 right);
            i32 y = host_add(2, 3);
        ''')

        with self.assertRaisesRegex(EvaluationError, 'No extern binding'):
            Interpreter().eval_source_ast(ast)

    def test_c_emit_emits_str_extern_with_jack_str_signature(self):
        ast = parse('extern void host_write(str text);')

        c_source = emit_c(ast)

        self.assertIn('void host_write(jack_str text);', c_source)
        self.assertNotIn('void host_write(jack_str text) {', c_source)

    def test_c_emit_emits_extern_prototype_without_definition(self):
        ast = parse('''
            extern i32 host_add(i32 left, i32 right);
            i32 y = host_add(2, 3);
        ''')

        c_source = emit_c(ast)

        signature = 'int32_t host_add(int32_t left, int32_t right)'
        self.assertEqual(1, c_source.count(signature))
        self.assertIn(f'{signature};', c_source)
        self.assertNotIn(f'{signature} {{', c_source)
        self.assertIn('y = host_add(2, 3);', c_source)

    def test_c_emit_emits_libc_shaped_extern_declarations(self):
        ast = parse('''
            extern "c" type FILE;
            extern "c" &inout FILE stdout;
            extern "c" usize fwrite(&in c_void data, usize size, usize count, &inout FILE stream);

            u8[4] buffer;
            usize written = fwrite(&in buffer[0], 1, len(buffer), stdout);
            print(written);
        ''')

        c_source = emit_c(ast)

        self.assertIn('#include "jack_runtime.h"', c_source)
        self.assertNotIn('typedef struct FILE', c_source)
        self.assertIn('extern FILE *stdout;', c_source)
        self.assertNotIn(
            'size_t fwrite(const void *data, size_t size, size_t count, FILE *stream);',
            c_source,
        )
        self.assertIn('size_t written;', c_source)
        self.assertIn('written = fwrite(&buffer[0], 1, ((int32_t)(4)), stdout);', c_source)
        self.assertIn('printf("written = %zu\\n", (size_t)(written));', c_source)

    def test_interpreter_calls_extern_with_extern_global_borrow(self):
        ast = parse('''
            extern "c" type FILE;
            extern "c" &inout FILE stdout;
            extern "c" usize fwrite(&in c_void data, usize size, usize count, &inout FILE stream);

            u8[4] buffer;
            usize written = fwrite(&in buffer[0], 1, len(buffer), stdout);
        ''')

        stdout = object()
        calls = []

        def fwrite(data, size, count, stream):
            calls.append((data, int(size), int(count), getattr(stream, 'value', None)))
            return int(size) * int(count)

        interpreter = Interpreter(externs={'stdout': stdout, 'fwrite': fwrite})
        interpreter.eval_source_ast(ast)

        self.assertEqual(4, int(interpreter.global_scope.get('written')))
        self.assertEqual([(1, 4, stdout)], [(size, count, stream) for _, size, count, stream in calls])

    def test_semantic_rejects_opaque_extern_type_by_value(self):
        with self.assertRaisesRegex(SemanticError, 'Opaque extern type "FILE" cannot be used by value'):
            emit_c(parse('''
                extern "c" type FILE;
                extern "c" void consume(FILE file);
            '''))

    def test_semantic_rejects_c_void_by_value(self):
        with self.assertRaisesRegex(SemanticError, 'c_void can only be used behind'):
            emit_c(parse('extern "c" c_void bad();'))

    def test_semantic_rejects_c_char_by_value(self):
        with self.assertRaisesRegex(SemanticError, 'c_char can only be used behind'):
            emit_c(parse('extern "c" c_char bad();'))

    def test_comptime_extern_call_uses_registered_host_binding(self):
        ast = parse('''
            comptime extern i32 host_add(i32 left, i32 right);
            comptime i32 result = host_add(2, 3);
            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(
            ast, externs={'host_add': lambda left, right: left + right}
        )

        self.assertFalse(any(type(node) is FunctionDeclaration for node in compiled))
        declaration = next(node for node in compiled if type(node) is VariableDeclaration)
        self.assertEqual('y', declaration.name)
        self.assertEqual(5, declaration.expr.value)

    def test_comptime_extern_call_requires_registered_host_binding(self):
        ast = parse('''
            comptime extern i32 host_add(i32 left, i32 right);
            comptime i32 result = host_add(2, 3);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'No comptime extern binding'):
            apply_compile_time_pass(ast)

    def test_plain_extern_cannot_be_called_at_comptime(self):
        ast = parse('''
            extern i32 host_add(i32 left, i32 right);
            comptime i32 result = host_add(2, 3);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'cannot be called at comptime'):
            apply_compile_time_pass(ast, externs={'host_add': lambda left, right: left + right})

    def test_plain_c_extern_can_be_called_at_comptime_with_registered_binding(self):
        ast = parse('''
            extern "c" i32 host_add(i32 left, i32 right);
            comptime i32 result = host_add(2, 3);
            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(
            ast, externs={'host_add': lambda left, right: left + right}
        )

        declaration = next(node for node in compiled if type(node) is VariableDeclaration)
        self.assertEqual('y', declaration.name)
        self.assertEqual(5, declaration.expr.value)

    def test_plain_c_extern_requires_registered_binding_at_comptime(self):
        ast = parse('''
            extern "c" i32 host_add(i32 left, i32 right);
            comptime i32 result = host_add(2, 3);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'cannot be called at comptime'):
            apply_compile_time_pass(ast)

    def test_comptime_extern_cannot_be_called_at_runtime(self):
        ast = parse('''
            comptime extern i32 host_add(i32 left, i32 right);
            i32 y = host_add(2, 3);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'cannot be called at runtime'):
            apply_compile_time_pass(ast, externs={'host_add': lambda left, right: left + right})


if __name__ == '__main__':
    unittest.main()
