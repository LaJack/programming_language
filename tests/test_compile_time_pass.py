import io
import sys
import unittest
from contextlib import redirect_stdout

from jack.ast_nodes import For, FunctionCall, FunctionDeclaration, If, Print, Return, TypeDeclaration, TypeExpression, VariableDeclaration, While
from jack.compile_time_pass import CompileTimeError, apply_compile_time_pass
from jack.interpreter import EvaluationError, Interpreter
from jack.parser import ParseError, parse
from jack.semantic_pass import SemanticError


def assert_no_comptime(test_case: unittest.TestCase, nodes):
    for node in nodes:
        test_case.assertFalse(getattr(node, 'comptime', False), node)
        if type(node) is FunctionDeclaration:
            for parameter in node.parameters:
                test_case.assertFalse(parameter.comptime, parameter)
            assert_no_comptime(test_case, node.body)
        if type(node) is TypeDeclaration:
            for parameter in node.parameters:
                test_case.assertFalse(parameter.comptime, parameter)
            for field in node.fields:
                test_case.assertFalse(field.comptime, field)
            for method in node.methods:
                test_case.assertFalse(method.comptime, method)
                for parameter in method.parameters:
                    test_case.assertFalse(parameter.comptime, parameter)
                assert_no_comptime(test_case, method.body)
        if type(node) is If:
            for branch in node.branches:
                assert_no_comptime(test_case, branch.body)
            if node.else_body is not None:
                assert_no_comptime(test_case, node.else_body)
        if type(node) is While:
            assert_no_comptime(test_case, node.body)
        if type(node) is For:
            if node.initializer is not None:
                assert_no_comptime(test_case, [node.initializer])
            if node.update is not None:
                assert_no_comptime(test_case, [node.update])
            assert_no_comptime(test_case, node.body)


class CompileTimePassTests(unittest.TestCase):
    def test_comptime_statements_are_consumed_and_values_are_substituted(self):
        ast = parse('''
            comptime i32 offset;
            comptime offset = 2;
            comptime offset = offset + 5;

            i32 y = offset;
            print(offset);
        ''')

        compiled = apply_compile_time_pass(ast)

        assert_no_comptime(self, compiled)
        self.assertEqual(2, len(compiled))
        declaration = compiled[0]
        printed = compiled[1]

        self.assertEqual(VariableDeclaration, type(declaration))
        self.assertEqual('y', declaration.name)
        self.assertEqual(7, declaration.expr.value)

        self.assertEqual(Print, type(printed))
        self.assertEqual('offset', printed.name)
        self.assertEqual(7, printed.expr.value)

    def test_comptime_prints_execute_and_are_consumed(self):
        ast = parse('''
            comptime i32 offset;
            comptime offset = 2;
            comptime offset = offset + 5;
            comptime print(offset);

            i32 y = offset;
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            compiled = apply_compile_time_pass(ast)

        assert_no_comptime(self, compiled)
        self.assertEqual('offset = 7\n', output.getvalue())
        self.assertFalse(any(type(node) is Print for node in compiled))
        declaration = next(node for node in compiled if type(node) is VariableDeclaration)
        self.assertEqual(7, declaration.expr.value)

    def test_print_inside_comptime_function_call_executes(self):
        ast = parse('''
            i32 echo(i32 value) {
                print(value);
                return value;
            }

            comptime i32 result = echo(5);
            i32 y = result;
        ''')

        messages: list[str] = []
        compiled = apply_compile_time_pass(ast, print_handler=messages.append)

        self.assertEqual(['value = 5'], messages)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')
        self.assertEqual(5, declaration.expr.value)

    def test_comptime_function_parameters_generate_runtime_only_variants(self):
        ast = parse('''
            i32 add_offset(comptime i32 offset, i32 value) {
                comptime offset = offset + 1;
                return offset + value;
            }

            i32 first = add_offset(3, 10);
            i32 second = add_offset(5, first);
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        variants = [node for node in compiled if type(node) is FunctionDeclaration]
        self.assertEqual(['add_offset$comptime$offset$3', 'add_offset$comptime$offset$5'], [v.name for v in variants])
        self.assertEqual([1, 1], [len(v.parameters) for v in variants])
        self.assertEqual(['value', 'value'], [v.parameters[0].name for v in variants])

    def test_generated_variant_names_use_non_source_identifier_separator(self):
        ast = parse('''
            i32 add_offset(comptime i32 offset, i32 value) {
                return offset + value;
            }

            i32 first = add_offset(3, 10);
        ''')

        compiled = apply_compile_time_pass(ast)
        variants = [node for node in compiled if type(node) is FunctionDeclaration]
        self.assertEqual('add_offset$comptime$offset$3', variants[0].name)

        with self.assertRaises(ParseError):
            parse('i32 add_offset$comptime$offset$3(i32 value) { return value; }')

    def test_shared_variant_is_emitted_once_at_top_level(self):
        ast = parse('''
            i32 add_offset(comptime i32 offset, i32 value) {
                return offset + value;
            }

            i32 first(i32 value) {
                return add_offset(3, value);
            }

            i32 second(i32 value) {
                return add_offset(3, value);
            }
        ''')

        compiled = apply_compile_time_pass(ast)
        variants = [node for node in compiled if node.name.startswith('add_offset$comptime$')]

        self.assertEqual(1, len(variants))
        self.assertEqual(variants[0], compiled[0])
        for function in compiled[1:]:
            self.assertFalse(any(type(stmt) is FunctionDeclaration for stmt in function.body))

    def test_function_registration_does_not_depend_on_declaration_order(self):
        ast = parse('''
            i32 caller(i32 value) {
                return add_offset(3, value);
            }

            i32 add_offset(comptime i32 offset, i32 value) {
                return offset + value;
            }
        ''')

        compiled = apply_compile_time_pass(ast)
        caller = next(node for node in compiled if type(node) is FunctionDeclaration and node.name == 'caller')
        call = caller.body[0].expr

        self.assertEqual(FunctionCall, type(call))
        self.assertEqual('add_offset$comptime$offset$3', call.function_name)
        self.assertEqual(1, len(call.parameters))

    def test_keywords_are_reserved_as_names(self):
        for source in [
            'i32 bool;',
            'i32 comptime;',
            'i32 false;',
            'i32 print;',
            'i32 return;',
            'i32 struct;',
            'i32 true;',
        ]:
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source)

    def test_resource_is_not_a_reserved_keyword(self):
        ast = parse('i32 resource;')

        declaration = ast[0]

        self.assertEqual(VariableDeclaration, type(declaration))
        self.assertEqual('resource', declaration.name)

    def test_runtime_ast_can_be_interpreted_without_rerunning_compile_time_pass(self):
        ast = parse('''
            comptime i32 offset;
            comptime offset = 7;
            i32 y = offset;
        ''')
        compiled = apply_compile_time_pass(ast)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(7, interpreter.global_scope.get('y'))


    def test_comptime_function_call_evaluates_to_literal(self):
        ast = parse('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            comptime i32 result = add(2, 3);
            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual(5, declaration.expr.value)

    def test_comptime_function_call_can_use_local_state(self):
        ast = parse('''
            i32 double(i32 value) {
                i32 result = value;
                result = result + value;
                return result;
            }

            comptime i32 result = double(4);
            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual(8, declaration.expr.value)

    def test_comptime_function_call_rejects_runtime_arguments(self):
        ast = parse('''
            i32 add(i32 left, i32 right) {
                return left + right;
            }

            i32 runtime;
            comptime i32 result = add(2, runtime);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'runtime name'):
            apply_compile_time_pass(ast)

    def test_generic_struct_generates_runtime_type_variant(self):
        ast = parse('''
            struct Box(comptime type T, comptime i32 N) {
                T value;
            }

            Box(i32, 4) box;
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        generated_type = next(node for node in compiled if type(node) is TypeDeclaration)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration)

        self.assertEqual('Box$comptime$T$i32$N$4', generated_type.name)
        self.assertEqual([], generated_type.parameters)
        self.assertEqual('value', generated_type.fields[0].name)
        self.assertEqual('i32', generated_type.fields[0].type.name)
        self.assertEqual('Box$comptime$T$i32$N$4', declaration.type.name)

    def test_generic_struct_runtime_ast_can_be_interpreted(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;
            }

            Box(i32) box;
            box.value = 42;
        ''')
        compiled = apply_compile_time_pass(ast)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(42, interpreter.global_scope.get('box.value'))

    def test_nested_generic_type_arguments_are_specialized(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;
            }

            struct Holder(comptime type T) {
                T value;
            }

            Holder(Box(i32)) holder;
        ''')

        compiled = apply_compile_time_pass(ast)
        type_declarations = [node for node in compiled if type(node) is TypeDeclaration]

        self.assertEqual(
            ['Box$comptime$T$i32', 'Holder$comptime$T$Box_comptime_T_i32'],
            [node.name for node in type_declarations],
        )
        self.assertEqual('Box$comptime$T$i32', type_declarations[1].fields[0].type.name)

    def test_comptime_struct_values_can_be_mutated_printed_and_substituted(self):
        ast = parse('''
            struct Point {
                i32 x;
                i32 y;
            }

            comptime Point point;
            comptime point.x = 3;
            comptime point.y = point.x + 4;
            comptime print(point);

            i32 x = point.x;
            i32 y = point.y;
            print(point.x);
        ''')

        messages: list[str] = []
        compiled = apply_compile_time_pass(ast, print_handler=messages.append)

        assert_no_comptime(self, compiled)
        self.assertEqual(['point = Point{x = 3, y = 7}'], messages)
        x_declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'x')
        y_declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')
        printed = next(node for node in compiled if type(node) is Print)

        self.assertEqual(3, x_declaration.expr.value)
        self.assertEqual(7, y_declaration.expr.value)
        self.assertEqual('point.x', printed.name)
        self.assertEqual(3, printed.expr.value)

    def test_comptime_struct_values_support_nested_field_access(self):
        ast = parse('''
            struct Point {
                i32 x;
                i32 y;
            }

            struct Box {
                Point point;
                i32 scale;
            }

            comptime Box box;
            comptime box.point.x = 2;
            comptime box.point.y = 5;
            comptime box.scale = 3;

            i32 x = box.point.x;
            i32 y = box.point.y;
            i32 scale = box.scale;
        ''')

        compiled = apply_compile_time_pass(ast)

        assert_no_comptime(self, compiled)
        values = {
            node.name: node.expr.value
            for node in compiled
            if type(node) is VariableDeclaration and node.expr is not None
        }
        self.assertEqual({'x': 2, 'y': 5, 'scale': 3}, values)

    def test_comptime_struct_values_support_specialized_generic_types(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;
            }

            comptime Box(i32) box;
            comptime box.value = 42;

            i32 y = box.value;
        ''')

        compiled = apply_compile_time_pass(ast)

        assert_no_comptime(self, compiled)
        generated_type = next(node for node in compiled if type(node) is TypeDeclaration)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual('Box$comptime$T$i32', generated_type.name)
        self.assertEqual(42, declaration.expr.value)

    def test_comptime_function_call_accepts_struct_value(self):
        ast = parse('''
            struct Point {
                i32 x;
                i32 y;
            }

            i32 sum_point(comptime Point point) {
                return point.x + point.y;
            }

            comptime Point point;
            comptime point.x = 3;
            comptime point.y = 4;
            comptime i32 result = sum_point(point);

            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual(7, declaration.expr.value)

    def test_comptime_struct_parameter_specializes_runtime_function_variant(self):
        ast = parse('''
            struct Point {
                i32 x;
                i32 y;
            }

            i32 shifted(comptime Point point, i32 value) {
                return point.x + point.y + value;
            }

            comptime Point point;
            comptime point.x = 3;
            comptime point.y = 4;

            i32 y = shifted(point, 10);
        ''')

        compiled = apply_compile_time_pass(ast)

        variants = [
            node
            for node in compiled
            if type(node) is FunctionDeclaration and node.name.startswith('shifted$comptime$')
        ]
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual(1, len(variants))
        self.assertEqual(['value'], [parameter.name for parameter in variants[0].parameters])
        self.assertEqual(FunctionCall, type(declaration.expr))
        self.assertEqual(variants[0].name, declaration.expr.function_name)
        self.assertEqual(1, len(declaration.expr.parameters))

    def test_comptime_struct_value_cannot_be_used_as_runtime_value(self):
        ast = parse('''
            struct Point {
                i32 x;
            }

            comptime Point point;
            Point runtime = point;
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Cannot use comptime struct value'):
            apply_compile_time_pass(ast)

    def test_comptime_struct_value_cannot_be_printed_at_runtime(self):
        ast = parse('''
            struct Point {
                i32 x;
            }

            comptime Point point;
            print(point);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Cannot print comptime struct value'):
            apply_compile_time_pass(ast)

    def test_comptime_struct_assignment_rejects_wrong_type(self):
        ast = parse('''
            struct Left {
                i32 x;
            }

            struct Right {
                i32 x;
            }

            comptime Left left;
            comptime Right right;
            comptime left = right;
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Cannot convert comptime value of type'):
            apply_compile_time_pass(ast)

    def test_generic_type_parameters_bind_left_to_right_for_comptime_values(self):
        ast = parse('''
            struct Holder(comptime type T, comptime T value) {
                T stored;
            }

            comptime i32 number = 12;
            Holder(i32, number) holder;
        ''')

        compiled = apply_compile_time_pass(ast)

        generated_type = next(
            node
            for node in compiled
            if type(node) is TypeDeclaration and node.name.startswith('Holder$comptime$')
        )
        declaration = next(
            node
            for node in compiled
            if type(node) is VariableDeclaration and node.name == 'holder'
        )

        self.assertEqual('i32', generated_type.fields[0].type.name)
        self.assertEqual(generated_type.name, declaration.type.name)

    def test_comptime_struct_fields_can_feed_runtime_values(self):
        ast = parse('''
            struct Handle {
                i32 fd;
            }

            comptime Handle handle;
            comptime handle.fd = 12;
            i32 runtime_fd = handle.fd;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime_fd'
        )

        self.assertEqual(12, declaration.expr.value)

    def test_comptime_struct_can_hold_opaque_extern_field(self):
        ast = parse('''
            extern "c" type FILE;

            struct File {
                &inout FILE handle;
            }

            comptime File file;
            comptime print(file);
        ''')

        messages: list[str] = []
        compiled = apply_compile_time_pass(ast, print_handler=messages.append)

        self.assertEqual(['file = File{handle = <opaque &inout FILE>}'], messages)
        self.assertFalse(any(node.comptime for node in compiled))

    def test_comptime_constructor_reports_non_comptime_extern(self):
        ast = parse('''
            extern "c" type FILE;
            extern "c" &inout FILE open_handle();

            struct File {
                &inout FILE handle;

                init(&inout self, i32 marker) {
                    self.handle = open_handle();
                }
            }

            comptime File file(0);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Extern function "open_handle" cannot be called at comptime'):
            apply_compile_time_pass(ast)

    def test_comptime_extern_can_return_and_receive_opaque_extern_borrow(self):
        ast = parse('''
            extern "c" type FILE;
            comptime extern "c" &inout FILE open_handle();
            comptime extern "c" i32 use_handle(&inout FILE handle);

            comptime &inout FILE handle = open_handle();
            comptime i32 status = use_handle(handle);
            i32 runtime_status = status;
        ''')

        handle = object()
        seen: list[object] = []
        compiled = apply_compile_time_pass(
            ast,
            externs={
                'open_handle': lambda: handle,
                'use_handle': lambda value: seen.append(value) or 7,
            },
        )
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime_status'
        )

        self.assertEqual([handle], seen)
        self.assertEqual(7, declaration.expr.value)

    def test_opaque_comptime_field_cannot_feed_runtime_values(self):
        ast = parse('''
            extern "c" type FILE;

            struct File {
                &inout FILE handle;
            }

            comptime File file;
            &inout FILE leaked = file.handle;
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Cannot use opaque comptime value'):
            apply_compile_time_pass(ast)

    def test_comptime_struct_with_opaque_field_cannot_be_materialized_for_runtime_method_call(self):
        ast = parse('''
            extern "c" type FILE;

            struct File {
                &inout FILE handle;

                void touch(&inout self) {
                    print(0);
                }
            }

            comptime File file;
            file.touch();
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Cannot materialize opaque comptime field'):
            apply_compile_time_pass(ast)

    def test_nested_opaque_comptime_field_cannot_be_materialized_for_runtime_method_call(self):
        ast = parse('''
            extern "c" type FILE;

            struct File {
                &inout FILE handle;
            }

            struct Wrapper {
                File file;

                void touch(&inout self) {
                    print(0);
                }
            }

            comptime Wrapper wrapper;
            wrapper.touch();
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Cannot materialize opaque comptime field'):
            apply_compile_time_pass(ast)

    def test_comptime_receiver_method_call_uses_normal_method_for_plain_receiver_type(self):
        ast = parse('''
            struct ExampleDriver(comptime type Accessor, comptime Accessor accessor) {
                void do_stuff(&inout self) {
                    accessor.write(0, 18);
                    accessor.write(4, 25);
                }
            }

            struct CanDriver {
                void write(&inout self, i32 address, i32 value) {
                    print(address);
                    print(value);
                }
            }

            comptime CanDriver can1;
            ExampleDriver(CanDriver, can1) exampleDriver;
            exampleDriver.do_stuff();
        ''')

        compiled = apply_compile_time_pass(ast)

        assert_no_comptime(self, compiled)
        variants = [
            node
            for node in compiled
            if type(node) is FunctionDeclaration
            and node.name.startswith('CanDriver$write$comptime$self$')
        ]
        self.assertEqual([], variants)

        example_type = next(
            node
            for node in compiled
            if type(node) is TypeDeclaration and node.name.startswith('ExampleDriver$comptime$')
        )
        calls = [statement for statement in example_type.methods[0].body if type(statement) is FunctionCall]

        self.assertEqual(['write', 'write'], [call.function_name.rsplit('.', 1)[1] for call in calls])
        self.assertTrue(all(call.function_name.startswith('accessor$runtime$') for call in calls))
        self.assertEqual([0, 4], [call.parameters[0].value for call in calls])
        self.assertEqual([18, 25], [call.parameters[1].value for call in calls])

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_runtime_ast(compiled)

        self.assertEqual(
            'address = 0\nvalue = 18\naddress = 4\nvalue = 25\n',
            output.getvalue(),
        )

    def test_generated_generic_type_is_ordered_after_runtime_field_dependency(self):
        ast = parse('''
            struct ExampleDriver(comptime type Accessor) {
                Accessor device;

                init(&inout self, Accessor device) {
                    self.device = device;
                }

                void do_stuff(&inout self) {
                    self.device.write(0, 18);
                }
            }

            struct CanDriver {
                i32 slave_address;

                init(&inout self, i32 slave_address) {
                    self.slave_address = slave_address;
                }

                void write(&inout self, i32 address, i32 value) {
                    print(self.slave_address);
                    print(address);
                    print(value);
                }
            }

            CanDriver device(5);
            ExampleDriver(CanDriver) driver(device);
            driver.do_stuff();
        ''')

        compiled = apply_compile_time_pass(ast)
        type_names = [node.name for node in compiled if type(node) is TypeDeclaration]

        self.assertEqual('CanDriver', type_names[0])
        self.assertTrue(type_names[1].startswith('ExampleDriver$comptime$'))

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_runtime_ast(compiled)

        self.assertEqual(
            'self.slave_address = 5\naddress = 0\nvalue = 18\n',
            output.getvalue(),
        )

    def test_inferred_raises_follow_specialized_method_receiver_fields(self):
        ast = parse('''
            struct AccessError {
                i32 code;
            }

            struct ExampleDriver(comptime type Accessor) {
                Accessor device;

                init(&inout self, Accessor device) {
                    self.device = device;
                }

                void do_stuff(&inout self) raises {
                    self.device.write(0);
                }
            }

            struct CanDriver {
                void write(&inout self, i32 address) raises AccessError {
                    AccessError err;
                    err.code = address;
                    raise err;
                }
            }

            CanDriver device;
            ExampleDriver(CanDriver) driver(device);
        ''')

        compiled = apply_compile_time_pass(ast)
        driver_type = next(
            node
            for node in compiled
            if type(node) is TypeDeclaration and node.name.startswith('ExampleDriver$comptime$')
        )
        do_stuff = next(method for method in driver_type.methods if method.name == 'do_stuff')

        self.assertEqual(['AccessError'], [error.name for error in do_stuff.raises])
        self.assertFalse(do_stuff.raises_inferred)

    def test_generic_type_must_be_instantiated_with_comptime_arguments(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;
            }

            Box box;
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'requires comptime arguments'):
            apply_compile_time_pass(ast)

    def test_comptime_function_accepts_generic_type_argument(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;
            }

            i32 marker(comptime type T) {
                return 1;
            }

            comptime i32 result = marker(Box(i32));
            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual(1, declaration.expr.value)

    def test_layout_queries_parse_type_arguments(self):
        ast = parse('''
            usize size = sizeof(i32);
            usize alignment = alignof(u8[4]);
        ''')

        size_call = ast[0].expr
        alignment_call = ast[1].expr

        self.assertEqual(FunctionCall, type(size_call))
        self.assertEqual(TypeExpression, type(size_call.parameters[0]))
        self.assertEqual('i32', size_call.parameters[0].type_ref.name)
        self.assertEqual(FunctionCall, type(alignment_call))
        self.assertEqual(TypeExpression, type(alignment_call.parameters[0]))
        self.assertEqual('u8', alignment_call.parameters[0].type_ref.name)

    def test_layout_queries_are_folded_to_usize_literals(self):
        ast = parse('''
            extern "c" type FILE;

            struct Pair {
                u8 tag;
                i32 value;
            }

            usize i32_size = sizeof(i32);
            usize i32_align = alignof(i32);
            usize array_size = sizeof(u8[4]);
            usize array_align = alignof(u8[4]);
            usize pair_size = sizeof(Pair);
            usize pair_align = alignof(Pair);
            usize file_ptr_size = sizeof(&inout FILE);
            usize file_ptr_align = alignof(&inout FILE);
        ''')

        compiled = apply_compile_time_pass(ast)
        declarations = {
            node.name: node
            for node in compiled
            if type(node) is VariableDeclaration
        }

        pointer_size = (sys.maxsize.bit_length() + 1) // 8
        self.assertEqual(('usize', 4), (declarations['i32_size'].expr.type, declarations['i32_size'].expr.value))
        self.assertEqual(('usize', 4), (declarations['i32_align'].expr.type, declarations['i32_align'].expr.value))
        self.assertEqual(('usize', 4), (declarations['array_size'].expr.type, declarations['array_size'].expr.value))
        self.assertEqual(('usize', 1), (declarations['array_align'].expr.type, declarations['array_align'].expr.value))
        self.assertEqual(('usize', 8), (declarations['pair_size'].expr.type, declarations['pair_size'].expr.value))
        self.assertEqual(('usize', 4), (declarations['pair_align'].expr.type, declarations['pair_align'].expr.value))
        self.assertEqual(('usize', pointer_size), (declarations['file_ptr_size'].expr.type, declarations['file_ptr_size'].expr.value))
        self.assertEqual(('usize', pointer_size), (declarations['file_ptr_align'].expr.type, declarations['file_ptr_align'].expr.value))

    def test_layout_queries_resolve_generic_types(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;
            }

            usize boxed_size = sizeof(Box(i32));
            usize boxed_align = alignof(Box(u16));
        ''')

        compiled = apply_compile_time_pass(ast)
        declarations = {
            node.name: node
            for node in compiled
            if type(node) is VariableDeclaration
        }

        self.assertEqual(4, declarations['boxed_size'].expr.value)
        self.assertEqual(2, declarations['boxed_align'].expr.value)

    def test_layout_queries_reject_values_and_unsized_types(self):
        for source, message in [
            ('i32 value = 1; usize size = sizeof(value);', 'Unknown type "value"'),
            ('usize size = sizeof(&in Unknown);', 'Unknown type "Unknown"'),
            ('usize size = sizeof(void);', 'Cannot query layout of type "void"'),
        ]:
            with self.subTest(source=source):
                with self.assertRaisesRegex(CompileTimeError, message):
                    apply_compile_time_pass(parse(source))

    def test_constructor_syntax_initializes_runtime_value(self):
        ast = parse('''
            struct CanDriver {
                i32 slave_address;

                init(&inout self, i32 slave_address) {
                    self.slave_address = slave_address;
                }
            }

            CanDriver can(5);
            i32 value = can.slave_address;
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(5, interpreter.global_scope.get('can.slave_address'))
        self.assertEqual(5, interpreter.global_scope.get('value'))

    def test_constructor_syntax_initializes_comptime_value(self):
        ast = parse('''
            struct CanDriver {
                i32 slave_address;

                init(&inout self, i32 slave_address) {
                    self.slave_address = slave_address;
                }
            }

            comptime CanDriver can(7);
            i32 value = can.slave_address;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'value')

        self.assertEqual(7, declaration.expr.value)

    def test_constructor_syntax_works_with_generic_type_arguments(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;

                init(&inout self, T value) {
                    self.value = value;
                }
            }

            Box(i32) box(42);
            i32 value = box.value;
        ''')

        compiled = apply_compile_time_pass(ast)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(42, interpreter.global_scope.get('box.value'))
        self.assertEqual(42, interpreter.global_scope.get('value'))

    def test_destructor_runs_at_scope_exit_in_reverse_declaration_order(self):
        ast = parse('''
            struct Tracer {
                i32 value;

                init(&inout self, i32 value) {
                    self.value = value;
                }

                deinit(&inout self) {
                    print(self.value);
                }
            }

            void run() {
                Tracer first(1);
                Tracer second(2);
                return;
            }

            run();
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_runtime_ast(apply_compile_time_pass(ast))

        self.assertEqual('self.value = 2\nself.value = 1\n', output.getvalue())

    def test_deinit_cannot_take_parameters(self):
        ast = parse('''
            struct Nope {
                deinit(&inout self, i32 value) {
                    return;
                }
            }
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'deinit'):
            apply_compile_time_pass(ast)

    def test_deinit_cannot_declare_raises(self):
        ast = parse('''
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
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'deinit.*cannot raise'):
            apply_compile_time_pass(ast)

    def test_deinit_cannot_use_inferred_raises(self):
        ast = parse('''
            struct CleanupError {
                i32 code;
            }

            struct Nope {
                deinit(&inout self) raises {
                    CleanupError err;
                    err.code = 1;
                    raise err;
                }
            }
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'deinit.*cannot raise'):
            apply_compile_time_pass(ast)

    def test_type_method_can_be_interpreted(self):
        ast = parse('''
            struct Line {
                i32 p1;
                i32 p2;

                i32 sum(&inout self) {
                    return self.p1 + self.p2;
                }
            }

            Line line;
            line.p1 = 3;
            line.p2 = 4;
            i32 total = line.sum();
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)
        type_decl = next(node for node in compiled if type(node) is TypeDeclaration)

        self.assertEqual(['sum'], [method.name for method in type_decl.methods])
        self.assertEqual('i32', type_decl.methods[0].return_type.name)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(7, interpreter.global_scope.get('total'))

    def test_type_method_can_mutate_self(self):
        ast = parse('''
            struct Counter {
                i32 value;

                i32 add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                    return self.value;
                }
            }

            Counter counter;
            counter.value = 10;
            i32 value = counter.add(5);
        ''')
        compiled = apply_compile_time_pass(ast)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(15, interpreter.global_scope.get('counter.value'))
        self.assertEqual(15, interpreter.global_scope.get('value'))

    def test_generic_struct_method_is_specialized(self):
        ast = parse('''
            struct Box(comptime type T) {
                T value;

                T get(&inout self) {
                    return self.value;
                }
            }

            Box(i32) box;
            box.value = 42;
            i32 y = box.get();
        ''')

        compiled = apply_compile_time_pass(ast)
        type_decl = next(node for node in compiled if type(node) is TypeDeclaration)

        self.assertEqual('Box$comptime$T$i32', type_decl.name)
        self.assertEqual('i32', type_decl.methods[0].return_type.name)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(42, interpreter.global_scope.get('y'))

    def test_comptime_method_parameters_are_rejected(self):
        ast = parse('''
            struct Offset {
                i32 value;

                i32 add(&inout self, comptime i32 delta) {
                    return self.value + delta;
                }
            }
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'Comptime method parameters'):
            apply_compile_time_pass(ast)

    def test_void_function_call_can_be_interpreted(self):
        ast = parse('''
            i32 target;

            void set_target(i32 value) {
                target = value;
                return;
            }

            set_target(12);
        ''')

        compiled = apply_compile_time_pass(ast)
        function = next(node for node in compiled if type(node) is FunctionDeclaration)
        returned = next(node for node in function.body if type(node) is Return)

        self.assertEqual('void', function.return_type.name)
        self.assertIsNone(returned.expr)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(12, interpreter.global_scope.get('target'))

    def test_void_method_call_can_be_interpreted(self):
        ast = parse('''
            struct Counter {
                i32 value;

                void add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                    return;
                }
            }

            Counter counter;
            counter.value = 10;
            counter.add(5);
        ''')
        compiled = apply_compile_time_pass(ast)
        type_decl = next(node for node in compiled if type(node) is TypeDeclaration)

        self.assertEqual('void', type_decl.methods[0].return_type.name)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(15, interpreter.global_scope.get('counter.value'))

    def test_void_variables_are_rejected(self):
        ast = parse('''
            void value;
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'cannot have type "void"'):
            apply_compile_time_pass(ast)

    def test_void_function_cannot_return_a_value(self):
        ast = parse('''
            void nope() {
                return 1;
            }
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'cannot return a value'):
            apply_compile_time_pass(ast)

    def test_non_void_function_must_return_a_value(self):
        ast = parse('''
            i32 nope() {
                return;
            }
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'must return a value'):
            apply_compile_time_pass(ast)

    def test_assignment_to_comptime_variable_must_be_marked(self):
        ast = parse('''
            comptime i32 offset;
            offset = 2;
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'must be marked comptime'):
            apply_compile_time_pass(ast)

    def test_runtime_if_elif_else_can_be_interpreted(self):
        ast = parse('''
            i32 x = 2;
            i32 result;

            if (x == 1) {
                result = 10;
            } elif (x == 2) {
                result = 20;
            } else {
                result = 30;
            }
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(compiled)

        self.assertEqual(20, interpreter.global_scope.get('result'))

    def test_runtime_while_can_be_interpreted(self):
        ast = parse('''
            i32 i;
            i32 total;

            while (i < 4) {
                total = total + i;
                i = i + 1;
            }
        ''')

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(apply_compile_time_pass(ast))

        self.assertEqual(4, interpreter.global_scope.get('i'))
        self.assertEqual(6, interpreter.global_scope.get('total'))

    def test_runtime_for_can_be_interpreted(self):
        ast = parse('''
            i32 total;

            for (i32 i = 0; i < 4; i = i + 1) {
                total = total + i;
            }
        ''')

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(apply_compile_time_pass(ast))

        self.assertEqual(6, interpreter.global_scope.get('total'))

    def test_return_inside_control_flow_exits_function(self):
        ast = parse('''
            i32 pick(i32 value) {
                if (value == 0) {
                    return 10;
                }

                while (value < 3) {
                    return 20;
                }

                return 30;
            }

            i32 first = pick(0);
            i32 second = pick(1);
            i32 third = pick(5);
        ''')

        interpreter = Interpreter()
        interpreter.eval_runtime_ast(apply_compile_time_pass(ast))

        self.assertEqual(10, interpreter.global_scope.get('first'))
        self.assertEqual(20, interpreter.global_scope.get('second'))
        self.assertEqual(30, interpreter.global_scope.get('third'))

    def test_comptime_if_is_unwound(self):
        ast = parse('''
            comptime i32 mode = 2;

            comptime if (mode == 1) {
                i32 y = 10;
            } elif (mode == 2) {
                i32 y = mode;
            } else {
                i32 y = 30;
            }
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        self.assertFalse(any(type(node) is If for node in compiled))
        self.assertEqual(1, len(compiled))
        self.assertEqual(VariableDeclaration, type(compiled[0]))
        self.assertEqual('y', compiled[0].name)
        self.assertEqual(2, compiled[0].expr.value)

    def test_comptime_while_is_unwound(self):
        ast = parse('''
            comptime i32 i;
            comptime i32 total;

            comptime while (i < 4) {
                comptime total = total + i;
                comptime i = i + 1;
            }

            i32 y = total;
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        self.assertFalse(any(type(node) is While for node in compiled))
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')
        self.assertEqual(6, declaration.expr.value)

    def test_comptime_for_is_unwound(self):
        ast = parse('''
            comptime i32 total;

            comptime for (i32 i = 0; i < 4; i = i + 1) {
                comptime total = total + i;
            }

            i32 y = total;
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)

        self.assertFalse(any(type(node) is For for node in compiled))
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')
        self.assertEqual(6, declaration.expr.value)

    def test_comptime_function_call_executes_control_flow(self):
        ast = parse('''
            i32 sum_to(i32 limit) {
                i32 total;

                for (i32 i = 0; i < limit; i = i + 1) {
                    total = total + i;
                }

                if (total == 6) {
                    return total + 1;
                }

                return total;
            }

            comptime i32 result = sum_to(4);
            i32 y = result;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(node for node in compiled if type(node) is VariableDeclaration and node.name == 'y')

        self.assertEqual(7, declaration.expr.value)

    def test_comptime_array_index_assignment_len_and_substitution(self):
        ast = parse('''
            comptime u8[4] buffer;
            comptime buffer[1] = 9;

            u8 value = buffer[1];
            i32 count = len(buffer);
        ''')

        compiled = apply_compile_time_pass(ast)
        declarations = {
            node.name: node
            for node in compiled
            if type(node) is VariableDeclaration
        }

        self.assertEqual(9, declarations['value'].expr.value)
        self.assertEqual('u8', declarations['value'].expr.type)
        self.assertEqual(4, declarations['count'].expr.value)

    def test_comptime_inout_slice_call_mutates_caller_array(self):
        ast = parse('''
            void fill(&inout u8[] dst) {
                dst[0] = 42;
            }

            comptime u8[4] buffer;
            comptime fill(buffer[..]);

            u8 first = buffer[0];
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'first'
        )

        self.assertEqual(42, declaration.expr.value)

    def test_comptime_borrowed_slice_variable_aliases_caller_array(self):
        ast = parse('''
            comptime u8[4] buffer;
            comptime &inout u8[] window = &inout buffer[1..3];
            comptime window[0] = 77;

            u8 value = buffer[1];
            i32 count = len(window);
        ''')

        compiled = apply_compile_time_pass(ast)
        declarations = {
            node.name: node
            for node in compiled
            if type(node) is VariableDeclaration
        }

        self.assertEqual(77, declarations['value'].expr.value)
        self.assertEqual(2, declarations['count'].expr.value)

    def test_comptime_in_slice_rejects_mutation(self):
        ast = parse('''
            void overwrite(&in u8[] src) {
                src[0] = 1;
            }

            comptime u8[2] buffer;
            comptime overwrite(buffer[..]);
        ''')

        with self.assertRaisesRegex(CompileTimeError, 'read-only comptime borrow'):
            apply_compile_time_pass(ast)

    def test_comptime_extern_can_receive_c_char_borrow_from_u8_array(self):
        ast = parse('''
            comptime extern "c" i32 second_byte(&in c_char data);

            comptime u8[3] data;
            comptime data[0] = 10;
            comptime data[1] = 20;
            comptime data[2] = 0;
            comptime i32 value = second_byte(data[0]);

            i32 runtime_value = value;
        ''')

        seen: list[int] = []

        def second_byte(data):
            seen.append(data.element_cell(1).value)
            return seen[-1]

        compiled = apply_compile_time_pass(ast, externs={'second_byte': second_byte})
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime_value'
        )

        self.assertEqual([20], seen)
        self.assertEqual(20, declaration.expr.value)

    def test_str_literals_can_be_interpreted_and_compared(self):
        ast = parse('''
            str message = "hello";
            bool same = message == "hello";
            bool different = message != "world";
            print(message);
            print(same);
            print(different);
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual('message = hello\nsame = true\ndifferent = true\n', output.getvalue())

    def test_comptime_str_values_are_substituted_and_printed(self):
        ast = parse('''
            comptime str name;
            comptime name = "can0";
            comptime print(name);

            str runtime = name;
            print(runtime);
        ''')

        messages: list[str] = []
        compiled = apply_compile_time_pass(ast, print_handler=messages.append)

        assert_no_comptime(self, compiled)
        self.assertEqual(['name = can0'], messages)
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime'
        )
        self.assertEqual('str', declaration.expr.type)
        self.assertEqual('can0', declaration.expr.value)

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_runtime_ast(compiled)

        self.assertEqual('runtime = can0\n', output.getvalue())

    def test_comptime_str_parameter_specializes_generic_type(self):
        ast = parse('''
            struct Device(comptime str name) {
                void show(&inout self) {
                    print(name);
                }
            }

            Device("can0") device;
            device.show();
        ''')

        compiled = apply_compile_time_pass(ast)
        assert_no_comptime(self, compiled)
        type_decl = next(node for node in compiled if type(node) is TypeDeclaration)

        self.assertEqual('Device$comptime$name$can0', type_decl.name)
        self.assertEqual(0, len(type_decl.fields))

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_runtime_ast(compiled)

        self.assertEqual('name = can0\n', output.getvalue())


    def test_runtime_formatted_print_interprets_values(self):
        ast = parse('''
            str name = "can0";
            i32 value = 18;
            print(f"{name}: {value}");
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual('can0: 18\n', output.getvalue())

    def test_formatted_print_escapes_literal_braces(self):
        ast = parse('''
            i32 value = 12;
            print(f"{{{value}}}");
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual('{12}\n', output.getvalue())

    def test_comptime_formatted_string_folds_to_str_literal(self):
        ast = parse('''
            comptime str name = "can0";
            str label = f"driver:{name}";
            print(label);
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'label'
        )

        self.assertEqual('str', declaration.expr.type)
        self.assertEqual('driver:can0', declaration.expr.value)

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_runtime_ast(compiled)

        self.assertEqual('label = driver:can0\n', output.getvalue())

    def test_comptime_formatted_print_executes_directly(self):
        ast = parse('''
            comptime str name = "can0";
            comptime print(f"driver {name}");
        ''')

        messages: list[str] = []
        compiled = apply_compile_time_pass(ast, print_handler=messages.append)

        self.assertEqual(['driver can0'], messages)
        self.assertEqual([], compiled)

    def test_runtime_formatted_string_assignment_is_rejected(self):
        ast = parse('''
            str name = "can0";
            str label = f"driver:{name}";
        ''')

        with self.assertRaises(CompileTimeError):
            apply_compile_time_pass(ast)


    def test_bool_values_can_be_interpreted_compared_and_printed(self):
        ast = parse('''
            bool enabled = true;
            bool disabled = false;
            bool same = enabled != disabled;
            print(enabled);
            print(disabled);
            print(same);
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual('enabled = true\ndisabled = false\nsame = true\n', output.getvalue())

    def test_bool_is_required_for_conditions(self):
        ast = parse('''
            i32 value = 1;
            if (value) {
                print(value);
            }
        ''')

        with self.assertRaises(SemanticError):
            Interpreter().eval_source_ast(ast)

    def test_bool_does_not_implicitly_cast_to_or_from_i32(self):
        with self.assertRaises(SemanticError):
            Interpreter().eval_source_ast(parse('i32 value = true;'))

        with self.assertRaises(SemanticError):
            Interpreter().eval_source_ast(parse('bool value = 1;'))

    def test_function_declaration_requires_explicit_return_type_in_ast(self):
        with self.assertRaises(TypeError):
            FunctionDeclaration('implicit', [], [])


    def test_builtin_type_conversions_can_be_interpreted(self):
        ast = parse('''
            u8 byte = u8(255);
            i64 wide = i64(byte);
            f32 ratio = f32(3);
            b16 raw = b16(48879);
            print(byte);
            print(wide);
            print(ratio);
            print(raw);
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual(
            'byte = 255\n'
            'wide = 255\n'
            'ratio = 3\n'
            'raw = 0xbeef\n',
            output.getvalue(),
        )

    def test_builtin_type_conversions_reject_invalid_values(self):
        for source in [
            'u8 value = u8(256);',
            'i32 value = i32(1.5);',
            'i32 value = i32(true);',
            'bool value = bool(1);',
        ]:
            with self.subTest(source=source):
                with self.assertRaises(SemanticError):
                    Interpreter().eval_source_ast(parse(source))

    def test_endian_integer_to_raw_conversions_can_be_interpreted(self):
        ast = parse('''
            b32 numeric = 45;
            i32 native = 45;
            be_i32 big = be_i32(45);
            le_i32 little = le_i32(45);
            b32 native_raw = b32(native);
            b32 big_raw = b32(big);
            b32 little_raw = b32(little);
            print(numeric);
            print(native_raw);
            print(big_raw);
            print(little_raw);
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        native_raw = '0x2d000000' if sys.byteorder == 'little' else '0x0000002d'
        self.assertEqual(
            'numeric = 0x0000002d\n'
            f'native_raw = {native_raw}\n'
            'big_raw = 0x0000002d\n'
            'little_raw = 0x2d000000\n',
            output.getvalue(),
        )

    def test_comptime_builtin_type_conversions_are_evaluated(self):
        ast = parse('''
            comptime u8 byte = u8(250);
            comptime u16 port = u16(byte);
            u16 runtime_port = port;
        ''')

        compiled = apply_compile_time_pass(ast)
        declaration = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime_port'
        )

        self.assertEqual('u16', declaration.expr.type)
        self.assertEqual(250, declaration.expr.value)

    def test_comptime_endian_integer_to_raw_conversion_is_evaluated(self):
        ast = parse('''
            comptime be_i32 big = be_i32(45);
            comptime le_i32 little = le_i32(45);
            comptime b32 big_raw = b32(big);
            comptime b32 little_raw = b32(little);
            b32 runtime_big = big_raw;
            b32 runtime_little = little_raw;
        ''')

        compiled = apply_compile_time_pass(ast)
        declarations = {
            node.name: node
            for node in compiled
            if type(node) is VariableDeclaration
        }

        self.assertEqual('b32', declarations['runtime_big'].expr.type)
        self.assertEqual(0x0000002d, declarations['runtime_big'].expr.value)
        self.assertEqual('b32', declarations['runtime_little'].expr.type)
        self.assertEqual(0x2d000000, declarations['runtime_little'].expr.value)

    def test_comptime_builtin_type_conversions_reject_invalid_values(self):
        for source in [
            'comptime u8 value = u8(256);',
            'comptime i32 value = i32(1.5);',
            'comptime i32 value = i32(true);',
            'comptime bool value = bool(1);',
        ]:
            with self.subTest(source=source):
                with self.assertRaises(CompileTimeError):
                    apply_compile_time_pass(parse(source))


    def test_sized_builtin_values_can_be_interpreted_and_printed(self):
        ast = parse('''
            i64 signed_value = 9223372036854775807;
            u8 byte = 255;
            b16 raw = 48879;
            f32 ratio = 1.5;
            print(signed_value);
            print(byte);
            print(raw);
            print(ratio);
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual(
            'signed_value = 9223372036854775807\n'
            'byte = 255\n'
            'raw = 0xbeef\n'
            'ratio = 1.5\n',
            output.getvalue(),
        )

    def test_sized_numeric_values_support_matching_type_arithmetic(self):
        ast = parse('''
            i16 left = 7;
            i16 right = 5;
            i16 total = left + right;
            print(total);
        ''')

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_source_ast(ast)

        self.assertEqual('total = 12\n', output.getvalue())

    def test_comptime_sized_builtin_values_are_substituted(self):
        ast = parse('''
            comptime u16 port = 250;
            comptime f64 scale = 1.25;
            u16 runtime_port = port;
            f64 runtime_scale = scale;
        ''')

        compiled = apply_compile_time_pass(ast)
        port_decl = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime_port'
        )
        scale_decl = next(
            node for node in compiled
            if type(node) is VariableDeclaration and node.name == 'runtime_scale'
        )

        self.assertEqual('u16', port_decl.expr.type)
        self.assertEqual(250, port_decl.expr.value)
        self.assertEqual('f64', scale_decl.expr.type)
        self.assertEqual(1.25, scale_decl.expr.value)

    def test_raw_byte_values_reject_arithmetic(self):
        ast = parse('''
            b8 left = 1;
            b8 right = 2;
            b8 total = left + right;
        ''')

        with self.assertRaises(SemanticError):
            Interpreter().eval_source_ast(ast)

    def test_sized_builtin_cast_rejects_out_of_range_values(self):
        ast = parse('''
            u8 byte = 256;
        ''')

        with self.assertRaises(SemanticError):
            Interpreter().eval_source_ast(ast)



if __name__ == '__main__':
    unittest.main()
