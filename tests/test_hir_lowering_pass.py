import unittest

from jack.compile_time_pass import apply_compile_time_pass
from jack.hir_lowering_pass import compile_to_hir, lower_to_hir
from jack.hir_nodes import (
    HIRAssignment,
    HIRBorrowExpression,
    HIRCallExpression,
    HIRCompositeExpression,
    HIRExpressionStatement,
    HIRFieldAccessExpression,
    HIRFor,
    HIRFunctionDeclaration,
    HIRGlobalVariable,
    HIRIf,
    HIRIndexExpression,
    HIRPrint,
    HIRReturn,
    HIRTypeDeclaration,
    HIRVariableDeclaration,
)
from jack.parser import parse


def lower_source(source: str):
    return lower_to_hir(apply_compile_time_pass(parse(source), print_handler=None))


def type_name(type_ref) -> str:
    name = type_ref.name
    if type_ref.is_slice:
        name += '[]'
    if type_ref.borrow is not None:
        name = f'&{type_ref.borrow} {name}'
    return name


class HIRLoweringPassTests(unittest.TestCase):
    def test_compile_to_hir_runs_comptime_and_preserves_top_level_order(self):
        hir = compile_to_hir(parse('''
            comptime i32 offset = 2;
            i32 before = offset;
            print(before);
            i32 after = before + offset;
        '''), print_handler=None)

        self.assertEqual(
            ['HIRGlobalVariable', 'HIRPrint', 'HIRGlobalVariable'],
            [type(statement).__name__ for statement in hir.top_level],
        )
        self.assertEqual(2, hir.top_level[0].initializer.value)

    def test_lowers_function_calls_with_resolved_target_and_types(self):
        hir = lower_source('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 y = add(2, 3);
        ''')

        function = next(node for node in hir.declarations if type(node) is HIRFunctionDeclaration)
        returned = function.body[0]
        self.assertIs(type(returned), HIRReturn)
        self.assertIs(type(returned.expr), HIRCompositeExpression)
        self.assertEqual('i32', returned.expr.type_ref.name)

        global_y = next(node for node in hir.declarations if type(node) is HIRGlobalVariable)
        call = global_y.initializer
        self.assertIs(type(call), HIRCallExpression)
        self.assertEqual('function', call.target.kind)
        self.assertEqual('add', call.target.name)
        self.assertEqual('i32', call.type_ref.name)
        self.assertEqual(['left', 'right'], [parameter.name for parameter in call.target.parameters])
        self.assertEqual(['i32', 'i32'], [parameter.type_ref.name for parameter in call.target.parameters])
        self.assertFalse(hasattr(call.target, 'declaration'))
        self.assertFalse(hasattr(call.target, 'owner_type'))
        self.assertEqual(['i32', 'i32'], [arg.type_ref.name for arg in call.arguments])

    def test_lowers_method_calls_with_receiver_and_implicit_self_borrow(self):
        hir = lower_source('''
            struct Counter {
                i32 value;

                void bump(&inout self, i32 amount) {
                    self.value = self.value + amount;
                }
            }

            Counter counter;
            counter.bump(3);
        ''')

        statement = hir.body[0]
        self.assertIs(type(statement), HIRExpressionStatement)
        call = statement.expr
        self.assertIs(type(call), HIRCallExpression)
        self.assertEqual('method', call.target.kind)
        self.assertEqual('Counter.bump', call.target.name)
        self.assertEqual('Counter', call.target.owner_type_name)
        self.assertEqual('counter', call.target.receiver_name)
        self.assertEqual('amount', call.target.parameters[0].name)
        self.assertEqual('i32', call.target.parameters[0].type_ref.name)
        self.assertIsNotNone(call.target.self_parameter)
        self.assertEqual('&inout Counter', type_name(call.target.self_parameter.type_ref))
        self.assertFalse(hasattr(call.target, 'declaration'))
        self.assertFalse(hasattr(call.target, 'owner_type'))
        self.assertEqual('counter', call.receiver.name)
        self.assertIs(type(call.implicit_self_argument), HIRBorrowExpression)
        self.assertEqual('&inout Counter', type_name(call.implicit_self_argument.type_ref))

    def test_lowers_builtin_len_and_conversions(self):
        hir = lower_source('''
            u8[4] bytes;
            i32 n = len(bytes);
            u32 widened = u32(n);
        ''')

        n_decl = hir.declarations[1]
        widened_decl = hir.declarations[2]
        self.assertEqual('len', n_decl.initializer.target.kind)
        self.assertEqual('i32', n_decl.initializer.type_ref.name)
        self.assertEqual('builtin_conversion', widened_decl.initializer.target.kind)
        self.assertEqual('u32', widened_decl.initializer.type_ref.name)

    def test_lowers_view_borrow_fields_as_typed_field_accesses(self):
        hir = lower_source('''
            view LowByte {
                inout u8 value;
            }

            struct Register {
                u8 value;
                u8 status;
            }

            void clear(&inout LowByte reg) {
                reg.value = 0;
            }
        ''')

        type_decl = next(node for node in hir.declarations if type(node) is HIRTypeDeclaration)
        self.assertEqual('Register', type_decl.name)
        function = next(node for node in hir.declarations if type(node) is HIRFunctionDeclaration)
        assignment = function.body[0]
        self.assertIs(type(assignment.target), HIRFieldAccessExpression)
        self.assertTrue(assignment.target.from_view)
        self.assertEqual('value', assignment.target.field_name)
        self.assertEqual('u8', assignment.target.type_ref.name)

    def test_lowers_borrows_slices_and_indexes_with_types(self):
        hir = lower_source('''
            void fill(&inout u8[] dst) {
                dst[0] = 7;
            }

            u8[4] bytes;
            fill(&inout bytes[..]);
        ''')

        function = next(node for node in hir.declarations if type(node) is HIRFunctionDeclaration)
        bytes_decl = next(
            node for node in hir.declarations if type(node) is HIRGlobalVariable
        )
        assignment = function.body[0]
        self.assertIs(type(assignment.target), HIRIndexExpression)
        self.assertEqual('u8', assignment.target.type_ref.name)
        self.assertEqual(4, bytes_decl.symbol.type_ref.array_size)
        self.assertIs(type(bytes_decl.symbol.type_ref.array_size), int)

        call = hir.body[0].expr
        argument = call.arguments[0]
        self.assertIs(type(argument), HIRBorrowExpression)
        self.assertEqual('&inout u8[]', type_name(argument.type_ref))

    def test_lowers_function_statements_with_resolved_types(self):
        runtime_ast = apply_compile_time_pass(parse('''
            void run() {
                i32 value = 1;
                value = value + 2;
                print(value);
            }
        '''), print_handler=None)

        hir = lower_to_hir(runtime_ast)
        function = hir.declarations[0]
        assignment = function.body[1]
        printed = function.body[2]

        self.assertIs(type(assignment), HIRAssignment)
        self.assertEqual('i32', assignment.target_type.name)
        self.assertIs(type(printed), HIRPrint)
        self.assertEqual('value', printed.label)
        self.assertEqual('i32', printed.expr.type_ref.name)
        self.assertEqual('i32', printed.expr.read_type.name)

    def test_lowers_control_flow_with_block_scopes(self):
        hir = lower_source('''
            void run(i32 limit) {
                for (i32 i = 0; i < limit; i = i + 1) {
                    if (i == 2) {
                        i32 local = i;
                    } else {
                        i32 other = limit;
                    }
                }
            }
        ''')

        function = next(node for node in hir.declarations if type(node) is HIRFunctionDeclaration)
        loop = function.body[0]
        self.assertIs(type(loop), HIRFor)
        self.assertIs(type(loop.initializer), HIRVariableDeclaration)
        self.assertIs(type(loop.condition), HIRCompositeExpression)
        self.assertEqual('bool', loop.condition.type_ref.name)
        self.assertIs(type(loop.body[0]), HIRIf)


if __name__ == '__main__':
    unittest.main()
