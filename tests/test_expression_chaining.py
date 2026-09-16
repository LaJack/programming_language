import copy
from dataclasses import fields, replace
import io
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import tempfile
import unittest

from jack.ast_nodes import (
    Assignment, FunctionCall, IndexExpression, MemberExpression,
    SliceExpression, VariableExpression,
)
from jack.jack_emit_pass import JackEmitPass
from jack.compile_time_pass import CompileTimePass
from jack.compiler_driver import CompilerDriver, CompilationOptions
from jack.interpreter import Interpreter
from jack.hir_lowering_pass import compile_to_hir
from jack.semantic_pass import SemanticError
from jack.parser import ParseError, parse, parse_recovering
from tests.bootstrap_syntax_normalization import python_node


class ExpressionChainingParserTests(unittest.TestCase):
    def test_calls_and_assignment_targets_have_structural_storage(self):
        call = FunctionCall('project.file', [VariableExpression('id')])
        self.assertIsInstance(call.callee, MemberExpression)
        self.assertNotIn('function_name', {field.name for field in fields(call)})
        self.assertEqual('project.file', call.function_name)
        self.assertEqual(call, copy.deepcopy(call))
        changed = replace(call, callee=VariableExpression('other'))
        self.assertEqual('other', changed.function_name)
        self.assertEqual('project.file', call.function_name)
        assignment = Assignment('value.field', VariableExpression('replacement'))
        self.assertIsInstance(assignment.target, MemberExpression)
        self.assertNotIn('name', {field.name for field in fields(assignment)})
        self.assertEqual('value.field', assignment.name)
        changed = replace(assignment, target=VariableExpression('other'))
        self.assertEqual('other', changed.name)

    def test_mixed_postfix_chain(self):
        expression = parse('i32 value = project.file(id).nodes()[index..end][0].span().start;')[0].expr
        self.assertIsInstance(expression, MemberExpression)
        self.assertEqual('start', expression.member)
        span_call = expression.target
        self.assertIsInstance(span_call, FunctionCall)
        self.assertEqual('span', span_call.callee.member)
        index = span_call.callee.target
        self.assertIsInstance(index, IndexExpression)
        self.assertIsInstance(index.target, SliceExpression)
        self.assertIsInstance(index.target.target, FunctionCall)

    def test_generic_union_construction_is_resolved_after_parsing(self):
        prefix = 'union Maybe(comptime type T) { none; some(move T value); } '
        for source in (
            'Maybe(bool) value = Maybe(bool).some(true);',
            'Maybe(i32[2]) value = Maybe(i32[2]).none;',
            'Maybe(*in i32) value = Maybe(*in i32).none;',
            'comptime Maybe(i32) value = Maybe(i32).some(1);',
        ):
            with self.subTest(source=source):
                tree = parse(prefix + source)
                self.assertIsInstance(tree[-1].expr, (FunctionCall, MemberExpression))
                CompileTimePass().apply(tree)

    def test_exact_member_spans(self):
        source = 'i32 result = project /* comment */ . file(id).node(id).span();'
        call = parse(source, source_path='/tmp/chains.jack')[0].expr
        for expected in ('span', 'node', 'file'):
            member = call.callee
            self.assertEqual(expected, member.member)
            span = member.member_span
            self.assertEqual(expected, source[span.start_offset:span.end_offset])
            self.assertEqual('/tmp/chains.jack', span.source_path)
            call = member.target

    def test_roundtrip_and_normalization(self):
        for source in (
            'make().update();',
            'i32 result = (left + right).method()[0].field;',
            'i32 result = values[0].method()[..][1];',
            'values.get(index).field = replacement();',
            'i32 result = Point { x = 1 }.get();',
            'i32 result = (match (&in value) { .some(item) => item, .none => 0, }).method();',
        ):
            with self.subTest(source=source):
                tree = parse(source)
                emitted = JackEmitPass().emit(tree)
                self.assertEqual(python_node(tree), python_node(parse(emitted)))

    def test_recovery_keeps_later_declarations(self):
        for source in (
            'i32 bad = make().; i32 good = 1;',
            'i32 bad = make().get(,); i32 good = 1;',
        ):
            with self.subTest(source=source):
                with self.assertRaises(ParseError):
                    parse(source)
                result = parse_recovering(source)
                self.assertTrue(result.diagnostics)
                self.assertTrue(any(getattr(node, 'name', None) == 'good' for node in result.statements))

    def test_non_call_expression_statements_remain_rejected(self):
        for source in ('values.get(index).field;', '1 + 2;', '(left + right);'):
            with self.subTest(source=source), self.assertRaises(ParseError):
                parse(source)


class ExpressionChainingRuntimeTests(unittest.TestCase):
    def assert_runtime_output(self, source, expected_values):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / 'chains.jack'
            entry.write_text(source)
            driver = CompilerDriver(print_handler=None)
            program = driver.compile_hir(entry, CompilationOptions())
            output = io.StringIO()
            with redirect_stdout(output):
                Interpreter().eval_hir_program(program)
            expected = output.getvalue()
            self.assertEqual(expected_values,
                             [line.rsplit(' = ', 1)[-1] for line in expected.splitlines()])
            for backend in ('c', 'llvm'):
                for optimization in (0, 2):
                    with self.subTest(backend=backend, optimization=optimization):
                        executable = Path(directory) / f'{backend}-{optimization}'
                        driver.compile_executable(entry, CompilationOptions(
                            backend=backend, optimization=optimization, output=executable))
                        result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
                        self.assertEqual((0, expected, ''), (result.returncode, result.stdout, result.stderr))

    def test_borrowed_receivers_and_fields_across_backends(self):
        source = '''
            struct Leaf {
                i32 value;
                i32 read(&in self) { return self.value; }
                void set(&inout self, i32 value) { self.value = value; }
            }
            struct Owner {
                Leaf leaf;
                &in Leaf get(&in self) { print(1); return &in self.leaf; }
                &inout Leaf get_mut(&inout self) { print(2); return &inout self.leaf; }
            }
            i32 replacement() { print(3); return 8; }
            Owner owner = Owner { leaf = Leaf { value = 5 } };
            print(owner.get().read());
            owner.get_mut().set(7);
            print(owner.get().value);
            owner.get_mut().value = replacement();
            print(owner.get().read());
        '''
        self.assert_runtime_output(source, ['1', '5', '2', '1', '7', '2', '3', '1', '8'])

    def test_assignment_order_and_errors(self):
        source = '''
            struct Failure { }
            struct Leaf { i32 value; }
            struct Owner {
                Leaf leaf;
                &inout Leaf get_mut(&inout self, bool fail) raises Failure {
                    print(1);
                    if (fail) { raise Failure { }; }
                    return &inout self.leaf;
                }
            }
            i32 replacement(bool fail) raises Failure {
                print(2);
                if (fail) { raise Failure { }; }
                return 9;
            }
            usize pick_index() { print(3); return 0; }
            Owner owner = Owner { leaf = Leaf { value = 5 } };
            try { owner.get_mut(true).value = replacement(false); }
            catch Failure { print(4); }
            try { owner.get_mut(false).value = replacement(true); }
            catch Failure { print(6); }
            print(owner.leaf.value);
            i32[1] values;
            try { values[pick_index()] = replacement(false); }
            catch Failure { print(99); }
            print(values[0]);
        '''
        self.assert_runtime_output(source, ['1', '4', '1', '2', '6', '5', '3', '2', '9'])

    def test_owned_temporary_receiver_lives_through_full_expression(self):
        source = '''
            struct Resource {
                i32 value;
                i32 read(&in self) { print(2); return self.value; }
                Resource next(&in self) {
                    print(4);
                    return Resource { value = self.value + 1 };
                }
                &in i32 borrow_value(&in self) { return &in self.value; }
                deinit(move self) { print(3); }
            }
            Resource make() { print(1); return Resource { value = 7 }; }
            i32 main(&in str[] arguments) {
                i32 saved = make().read();
                print(saved);
                print(make().read());
                print(make().value);
                print(make().borrow_value());
                print(make().next().read());
                return 0;
            }
        '''
        self.assert_runtime_output(
            source,
            [
                '1', '2', '3', '7',
                '1', '2', '7', '3',
                '1', '7', '3',
                '1', '7', '3',
                '1', '4', '2', '8', '3', '3',
            ],
        )

    def test_legacy_global_initializer_has_a_temporary_scope(self):
        source = '''
            struct Resource {
                i32 value;
                i32 read(&in self) { return self.value; }
                deinit(move self) { print(2); }
            }
            Resource make() { print(1); return Resource { value = 7 }; }
            i32 result = make().read();
            print(result);
        '''
        self.assert_runtime_output(source, ['1', '2', '7'])

    def test_borrow_from_owned_temporary_cannot_escape(self):
        source = '''
            struct Resource {
                i32 value;
                &in i32 borrow_value(&in self) { return &in self.value; }
                deinit(move self) { }
            }
            Resource make() { return Resource { value = 7 }; }
        '''
        with self.assertRaisesRegex(SemanticError, 'owned temporary'):
            compile_to_hir(parse(source + '&in i32 value = make().borrow_value();'))
        with self.assertRaisesRegex(SemanticError, 'local value'):
            compile_to_hir(parse(source + '''
                &in i32 bad() { return make().borrow_value(); }
            '''))

    def test_string_view_uses_the_temporary_owner_lifetime(self):
        source = '''
            struct Resource {
                str text;
                str text_view(&in self) { return self.text; }
                deinit(move self) { print(2); }
            }
            Resource make() { print(1); return Resource { text = "hello" }; }
            str static_text() { return "static"; }
            i32 main(&in str[] arguments) {
                print(make().text_view());
                print(static_text());
                return 0;
            }
        '''
        self.assert_runtime_output(source, ['1', 'hello', '2', 'static'])
        with self.assertRaisesRegex(SemanticError, 'owned temporary'):
            compile_to_hir(parse(source.replace(
                'print(make().text_view());',
                'str escaped = make().text_view();'
            )))

    def test_call_arguments_are_evaluated_left_to_right_and_cleaned_on_error(self):
        source = '''
            struct Failure { }
            struct Resource {
                i32 id;
                deinit(move self) { print(self.id); }
            }
            Resource make_resource(i32 id) { print(id); return Resource { id = id }; }
            i32 fail() raises Failure { print(3); raise Failure { }; }
            void consume(move Resource resource, i32 value) { print(value); }
            i32 main(&in str[] arguments) {
                try { consume(make_resource(1), fail()); }
                catch Failure { print(4); }
                return 0;
            }
        '''
        self.assert_runtime_output(source, ['1', '3', '1', '4'])

    def test_condition_temporary_dies_before_selected_branch(self):
        source = '''
            struct Probe {
                bool answer;
                bool test(&in self) { print(2); return self.answer; }
                deinit(move self) { print(3); }
            }
            Probe probe(bool answer) { print(1); return Probe { answer = answer }; }
            i32 main(&in str[] arguments) {
                if (probe(false).test()) { print(99); }
                else { if (probe(true).test()) { print(4); } }
                return 0;
            }
        '''
        self.assert_runtime_output(source, ['1', '2', '3', '1', '2', '3', '4'])

    def test_loop_condition_gets_a_fresh_temporary_scope(self):
        source = '''
            struct Probe {
                bool answer;
                bool test(&in self) { print(2); return self.answer; }
                deinit(move self) { print(3); }
            }
            Probe probe(bool answer) { print(1); return Probe { answer = answer }; }
            i32 main(&in str[] arguments) {
                i32 count = 0;
                while (probe(count < 2).test()) {
                    print(4);
                    count = count + 1;
                }
                return 0;
            }
        '''
        self.assert_runtime_output(
            source,
            ['1', '2', '3', '4', '1', '2', '3', '4', '1', '2', '3'],
        )

    def test_for_condition_gets_a_fresh_temporary_scope(self):
        source = '''
            struct Probe {
                bool answer;
                bool test(&in self) { print(2); return self.answer; }
                deinit(move self) { print(3); }
            }
            Probe probe(bool answer) { print(1); return Probe { answer = answer }; }
            i32 main(&in str[] arguments) {
                for (i32 count = 0; probe(count < 2).test(); count = count + 1) {
                    print(4);
                }
                return 0;
            }
        '''
        self.assert_runtime_output(
            source,
            ['1', '2', '3', '4', '1', '2', '3', '4', '1', '2', '3'],
        )

    def test_short_circuit_does_not_create_unselected_temporaries(self):
        source = '''
            struct Probe {
                bool test(&in self) { print(2); return true; }
                deinit(move self) { print(3); }
            }
            bool left(bool value) { print(1); return value; }
            Probe probe() { print(4); return Probe { }; }
            i32 main(&in str[] arguments) {
                if (left(false) && probe().test()) { print(99); }
                if (left(true) && probe().test()) { print(5); }
                if (left(true) || probe().test()) { print(6); }
                return 0;
            }
        '''
        self.assert_runtime_output(
            source, ['1', '1', '4', '2', '3', '5', '1', '6']
        )

    def test_match_arm_temporaries_are_lazy_and_branch_local(self):
        source = '''
            union Choice { no; yes; }
            struct Probe {
                i32 read(&in self) { print(2); return 7; }
                deinit(move self) { print(3); }
            }
            Probe probe() { print(1); return Probe { }; }
            i32 main(&in str[] arguments) {
                Choice yes = Choice.yes;
                i32 selected = match (&in yes) {
                    .yes => probe().read(),
                    .no => 0,
                };
                print(selected);
                Choice no = Choice.no;
                i32 skipped = match (&in no) {
                    .yes => probe().read(),
                    .no => 8,
                };
                print(skipped);
                return 0;
            }
        '''
        self.assert_runtime_output(source, ['1', '2', '3', '7', '8'])

    def test_chained_borrow_provenance(self):
        prefix = '''
            struct Leaf { i32 value; }
            struct Owner {
                Leaf leaf;
                &in Leaf get(&in self) { return &in self.leaf; }
                &inout Leaf get_mut(&inout self) { return &inout self.leaf; }
            }
            Owner owner = Owner { leaf = Leaf { value = 1 } };
        '''
        for suffix in (
            'owner.get().value = 2;',
            '&in i32 loan = &in owner.get().value; owner.get_mut().value = 2;',
        ):
            with self.subTest(suffix=suffix), self.assertRaises(SemanticError):
                compile_to_hir(parse(prefix + suffix))
        compile_to_hir(parse(prefix + '&inout Owner loan = &inout owner; loan.get_mut().value = 2;'))

    def test_assignment_receiver_loan_lasts_through_value_evaluation(self):
        prefix = '''
            struct Leaf { i32 value; }
            struct Owner {
                Leaf leaf;
                &inout Leaf get_mut(&inout self) { return &inout self.leaf; }
            }
            i32 change(&inout Owner owner) { owner.leaf.value = 9; return 2; }
            Owner owner = Owner { leaf = Leaf { value = 1 } };
        '''
        with self.assertRaisesRegex(SemanticError, 'assignment receiver'):
            compile_to_hir(parse(prefix + 'owner.get_mut().value = change(owner);'))
        compile_to_hir(parse(prefix + 'owner.leaf.value = owner.leaf.value + 1;'))
        compile_to_hir(parse(prefix + '''
            Owner other = Owner { leaf = Leaf { value = 3 } };
            owner.get_mut().value = change(other);
            change(owner);
        '''))
