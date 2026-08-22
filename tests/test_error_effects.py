import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from jack.ast_nodes import (
    FunctionDeclaration,
    Raise,
    Rethrow,
    StructLiteralExpression,
    Try,
    TypeDeclaration,
    VariableExpression,
)
from jack.c_emit_pass import emit_c, emit_c_files
from jack.cleanup_lowering_pass import (
    HIRStaticCleanupLoweringPass,
    lower_hir_static_cleanups,
)
from jack.compile_time_pass import apply_compile_time_pass
from jack.hir_lowering_pass import lower_to_hir
from jack.hir_nodes import (
    HIRAssignment,
    HIRBlock,
    HIRCallExpression,
    HIRExpressionStatement,
    HIRFunctionDeclaration,
    HIRIf,
    HIRRaise,
    HIRRethrow,
    HIRTry,
    HIRVariableDeclaration,
    HIRWhile,
)
from jack.interpreter import Interpreter
from jack.module_loader import load_source_file
from jack.parser import parse
from jack.semantic_pass import SemanticError, validate_runtime_ast


def compile_and_validate(source: str):
    return validate_runtime_ast(apply_compile_time_pass(parse(source), print_handler=None))


def access_error_source() -> str:
    return '''
        struct AccessError {
            i32 code;
        }
    '''


def fail_source(value: int = 7) -> str:
    return f'''
        void fail() raises AccessError {{
            AccessError err;
            err.code = {value};
            raise err;
        }}
    '''


def fail_literal_source(value: int = 7) -> str:
    return f'''
        void fail() raises AccessError {{
            raise AccessError {{ code = {value} }};
        }}
    '''


class ErrorEffectsTests(unittest.TestCase):
    def test_hir_cleanup_lowering_inserts_deinit_before_return(self):
        program = lower_to_hir(compile_and_validate('''
            struct Tracer {
                deinit(move self) {
                }
            }

            void run() {
                Tracer tracer;
                return;
            }
        '''))

        lowered = lower_hir_static_cleanups(program)
        function = next(
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
        )

        self.assertEqual(
            ['HIRVariableDeclaration', 'HIRVariableDeclaration', 'HIRIf', 'HIRReturn'],
            [type(statement).__name__ for statement in function.body],
        )
        cleanup = function.body[2]
        self.assertIsInstance(cleanup, HIRIf)
        self.assertEqual('Tracer.deinit', cleanup.branches[0].body[0].expr.target.name)

    def test_hir_cleanup_lowering_wraps_raising_calls(self):
        program = lower_to_hir(compile_and_validate(
            access_error_source() + fail_source() + '''
                struct Tracer {
                    deinit(move self) {
                    }
                }

                void caller() raises AccessError {
                    Tracer tracer;
                    fail();
                }
            '''
        ))

        lowered = lower_hir_static_cleanups(program)
        caller = next(
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'caller'
        )
        wrapped = caller.body[2]

        self.assertIsInstance(wrapped, HIRTry)
        self.assertEqual('fail', wrapped.body[0].expr.target.name)
        self.assertEqual(
            'Tracer.deinit',
            wrapped.catches[0].body[0].branches[0].body[0].expr.target.name,
        )
        self.assertIsInstance(wrapped.catches[0].body[1], HIRRethrow)

    def test_hir_cleanup_lowering_copies_raise_payload(self):
        program = lower_to_hir(compile_and_validate(
            access_error_source() + '''
                struct Tracer {
                    deinit(move self) {
                    }
                }

                void caller() raises AccessError {
                    Tracer tracer;
                    AccessError err;
                    err.code = 9;
                    raise err;
                }
            '''
        ))

        lowered = lower_hir_static_cleanups(program)
        caller = next(
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'caller'
        )

        self.assertIsInstance(caller.body[4], HIRVariableDeclaration)
        self.assertEqual('jack_cleanup_error_value_1', caller.body[4].symbol.name)
        self.assertIsInstance(caller.body[5], HIRAssignment)
        self.assertEqual(
            'Tracer.deinit', caller.body[6].branches[0].body[0].expr.target.name
        )
        self.assertIsInstance(caller.body[7], HIRRaise)

    def test_parser_reads_struct_error_raises_and_raise_statements(self):
        ast = parse(access_error_source() + '''
            void fail() raises AccessError {
                AccessError err;
                err.code = 7;
                raise err;
            }

            void caller() raises {
                fail();
            }
        ''')

        self.assertEqual(TypeDeclaration, type(ast[0]))
        self.assertEqual('AccessError', ast[0].name)
        self.assertEqual(['code'], [field.name for field in ast[0].fields])
        self.assertEqual(FunctionDeclaration, type(ast[1]))
        self.assertEqual(['AccessError'], [error.name for error in ast[1].raises])
        self.assertFalse(ast[1].raises_inferred)
        self.assertEqual(Raise, type(ast[1].body[2]))
        self.assertEqual(VariableExpression('err'), ast[1].body[2].expr)
        self.assertTrue(ast[2].raises_inferred)

    def test_parser_reads_struct_literal_raise_payload(self):
        ast = parse(access_error_source() + '''
            void fail() raises AccessError {
                raise AccessError { code = 7 };
            }
        ''')

        payload = ast[1].body[0].expr
        self.assertEqual(StructLiteralExpression, type(payload))
        self.assertEqual('AccessError', payload.type_ref.name)
        self.assertEqual(['code'], [field.name for field in payload.fields])

    def test_semantic_accepts_struct_literal_error_payload(self):
        ast = compile_and_validate(access_error_source() + fail_literal_source() + '''
            void caller() raises AccessError {
                fail();
            }
        ''')

        self.assertTrue(ast)

    def test_semantic_accepts_declared_error_propagation(self):
        ast = compile_and_validate(access_error_source() + fail_source() + '''
            void caller() raises AccessError {
                fail();
            }
        ''')

        self.assertTrue(ast)

    def test_semantic_rejects_undeclared_error_propagation(self):
        with self.assertRaisesRegex(SemanticError, 'does not declare it'):
            compile_and_validate(access_error_source() + fail_source() + '''
                void caller() {
                    fail();
                }
            ''')

    def test_semantic_rejects_top_level_raising_calls(self):
        with self.assertRaisesRegex(SemanticError, 'top-level code cannot propagate'):
            compile_and_validate(access_error_source() + fail_source() + '''
                fail();
            ''')

    def test_compile_time_pass_infers_bare_raises(self):
        compiled = apply_compile_time_pass(parse(access_error_source() + '''
            void fail() raises {
                AccessError err;
                err.code = 7;
                raise err;
            }

            void caller() raises {
                fail();
            }
        '''), print_handler=None)

        functions = {node.name: node for node in compiled if type(node) is FunctionDeclaration}
        self.assertEqual(['AccessError'], [error.name for error in functions['fail'].raises])
        self.assertEqual(['AccessError'], [error.name for error in functions['caller'].raises])
        self.assertFalse(functions['fail'].raises_inferred)
        self.assertFalse(functions['caller'].raises_inferred)

    def test_compile_time_pass_infers_specialized_function_variant_raises(self):
        compiled = apply_compile_time_pass(parse(access_error_source() + '''
            void fail(comptime i32 value) raises {
                AccessError err;
                err.code = value;
                raise err;
            }

            void caller() raises AccessError {
                fail(3);
            }
        '''), print_handler=None)

        variants = [
            node
            for node in compiled
            if type(node) is FunctionDeclaration and node.name.startswith('fail$comptime$')
        ]
        self.assertEqual(1, len(variants))
        self.assertEqual(['AccessError'], [error.name for error in variants[0].raises])
        validate_runtime_ast(compiled)

    def test_c_emit_preserves_raising_function_abi_and_uses_jack_throw(self):
        c_source = emit_c(parse(access_error_source() + fail_source() + '''
            void caller() raises AccessError {
                fail();
            }
        '''), print_handler=None)

        self.assertIn('jack_error_frame *jack_error_frame_stack = NULL;', c_source)
        self.assertIn('jack_error jack_current_error = {JACK_ERROR_OK, {0}};', c_source)
        self.assertIn('#define AccessError_error_tag 1', c_source)
        self.assertIn('void fail(void);', c_source)
        self.assertIn('void fail(void) {', c_source)
        self.assertIn('jack_throw(AccessError_error_tag, &', c_source)
        self.assertIn('void caller(void) {', c_source)
        self.assertIn('fail();', c_source)
        self.assertNotIn('int fail(void)', c_source)
        self.assertNotIn('error_status', c_source)

    def test_c_emit_supports_non_void_raising_function_abi(self):
        c_source = emit_c(parse(access_error_source() + '''
            i32 fail() raises AccessError {
                AccessError err;
                err.code = 7;
                raise err;
                return 0;
            }

            i32 caller() raises AccessError {
                return fail();
            }
        '''), print_handler=None)

        self.assertIn('int32_t fail(void);', c_source)
        self.assertIn('int32_t fail(void) {', c_source)
        self.assertIn('jack_throw(AccessError_error_tag, &', c_source)
        self.assertIn('int32_t caller(void) {', c_source)
        self.assertIn('return fail();', c_source)
        self.assertNotIn('int fail(void)', c_source)

    def test_cleanup_lowering_prepares_hir_call_targets(self):
        ast = compile_and_validate(access_error_source() + fail_source() + '''
            struct Tracer {
                deinit(move self) {
                }
            }

            void caller() raises AccessError {
                Tracer tracer;
                fail();
            }
        ''')

        lowered = lower_hir_static_cleanups(lower_to_hir(ast))
        caller = next(
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'caller'
        )
        call = caller.body[2].body[0].expr

        self.assertTrue(lowered)
        self.assertIsInstance(call, HIRCallExpression)
        self.assertEqual('function', call.target.kind)
        self.assertEqual('fail', call.target.name)
        self.assertEqual(['AccessError'], [error.name for error in call.target.raises])

    def test_cleanup_lowering_uses_hir_expression_error_walk(self):
        class SpyLowering(HIRStaticCleanupLoweringPass):
            def __init__(self):
                super().__init__()
                self.hir_expression_types = []

            def _hir_expression_raised_errors(self, expression):
                self.hir_expression_types.append(type(expression).__name__)
                return super()._hir_expression_raised_errors(expression)

        program = lower_to_hir(compile_and_validate(access_error_source() + fail_source() + '''
            struct Tracer {
                deinit(move self) {
                }
            }

            i32 value() raises AccessError {
                fail();
                return 1;
            }

            void caller() raises AccessError {
                Tracer tracer;
                i32 y = value();
            }
        '''))

        lowering = SpyLowering()
        lowered = lowering.lower(program)
        caller = next(
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'caller'
        )

        self.assertIn('HIRCallExpression', lowering.hir_expression_types)
        self.assertIsInstance(caller.body[3], HIRTry)
        self.assertIsInstance(caller.body[3].body[0], HIRAssignment)
        self.assertEqual(
            'Tracer.deinit',
            caller.body[3].catches[0].body[0].branches[0].body[0].expr.target.name,
        )
        self.assertIsInstance(caller.body[3].catches[0].body[1], HIRRethrow)

    def test_cleanup_lowering_uses_hir_statement_error_walk(self):
        class SpyLowering(HIRStaticCleanupLoweringPass):
            def __init__(self):
                super().__init__()
                self.hir_statement_types = []

            def _hir_statement_raised_errors(self, statement, env):
                self.hir_statement_types.append(type(statement).__name__)
                return super()._hir_statement_raised_errors(statement, env)

        program = lower_to_hir(compile_and_validate(access_error_source() + fail_source() + '''
            struct Tracer {
                deinit(move self) {
                }
            }

            void caller() raises AccessError {
                Tracer tracer;
                try {
                    fail();
                } catch AccessError {
                }
            }
        '''))

        lowered = SpyLowering()
        self.assertTrue(lowered.lower(program))
        self.assertIn('HIRExpressionStatement', lowered.hir_statement_types)

    def test_c_emit_wraps_raising_calls_with_live_destructors(self):
        c_source = emit_c(parse(access_error_source() + '''
            struct Tracer {
                i32 value;

                deinit(move self) {
                    print(self.value);
                }
            }

            void fail() raises AccessError {
                AccessError err;
                err.code = 7;
                raise err;
            }

            void caller() raises AccessError {
                Tracer tracer;
                fail();
            }
        '''), print_handler=None)

        self.assertIn('jack_error_frame error_frame_1;', c_source)
        self.assertIn('if (jack_try(&error_frame_1) == 0) {', c_source)
        self.assertIn('fail();', c_source)
        self.assertIn('jack_end_try(&error_frame_1);', c_source)
        self.assertIn('Tracer_deinit(&tracer);', c_source)
        self.assertIn('jack_rethrow(caught_error_1);', c_source)

    def test_c_emit_wraps_raising_initializer_with_live_destructors(self):
        c_source = emit_c(parse(access_error_source() + fail_source() + '''
            i32 value() raises AccessError {
                fail();
                return 1;
            }

            struct Tracer {
                deinit(move self) {
                }
            }

            void caller() raises AccessError {
                Tracer tracer;
                i32 result = value();
            }
        '''), print_handler=None)

        self.assertIn('int32_t result = 0;', c_source)
        self.assertIn('result = value();', c_source)
        self.assertIn('if (jack_try(&error_frame_1) == 0) {', c_source)
        self.assertIn('Tracer_deinit(&tracer);', c_source)
        self.assertIn('jack_rethrow(caught_error_1);', c_source)

    def test_cleanup_lowering_does_not_destroy_failed_constructor_value(self):
        program = lower_to_hir(compile_and_validate(
            access_error_source() + fail_source() + '''
                struct Tracer {
                    deinit(move self) {
                    }
                }

                struct Resource {
                    init(&inout self, i32 marker) raises AccessError {
                        fail();
                    }

                    deinit(move self) {
                    }
                }

                void caller() raises AccessError {
                    Tracer tracer;
                    Resource resource(1);
                }
            '''
        ))

        lowered = lower_hir_static_cleanups(program)
        caller = next(
            declaration
            for declaration in lowered.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'caller'
        )
        wrapped = caller.body[3]

        self.assertIsInstance(wrapped, HIRTry)
        self.assertEqual('Resource.init', wrapped.body[0].expr.target.name)
        self.assertEqual(
            'Tracer.deinit',
            wrapped.catches[0].body[0].branches[0].body[0].expr.target.name,
        )
        self.assertNotIn(
            'Resource.deinit',
            [
                statement.branches[0].body[0].expr.target.name
                for statement in wrapped.catches[0].body[:-1]
                if isinstance(statement, HIRIf)
            ],
        )

    def test_c_emit_wraps_raising_constructor_arguments(self):
        c_source = emit_c(parse(access_error_source() + fail_source() + '''
            i32 value() raises AccessError {
                fail();
                return 1;
            }

            struct Tracer {
                deinit(move self) {
                }
            }

            struct Resource {
                init(&inout self, i32 marker) {
                }
            }

            void caller() raises AccessError {
                Tracer tracer;
                Resource resource(value());
            }
        '''), print_handler=None)

        self.assertIn('Resource_init(&resource, value());', c_source)
        self.assertIn('if (jack_try(&error_frame_1) == 0) {', c_source)
        self.assertIn('Tracer_deinit(&tracer);', c_source)
        self.assertIn('jack_rethrow(caught_error_1);', c_source)

    def test_cleanup_lowering_supports_raising_expressions_in_statements(self):
        c_source = emit_c(parse(access_error_source() + fail_source() + '''
            struct Tracer {
                deinit(move self) {
                }
            }

            i32 value() raises AccessError {
                fail();
                return 1;
            }

            AccessError error_value() raises AccessError {
                fail();
                return AccessError { code = 1 };
            }

            void consume(i32 value) {
            }

            i32 statements() raises AccessError {
                Tracer tracer;
                i32 result = 0;
                result = value();
                consume(value());
                print(value());
                return value();
            }

            void payload() raises AccessError {
                Tracer tracer;
                raise error_value();
            }
        '''), print_handler=None)

        self.assertIn('result = value();', c_source)
        self.assertIn('consume(value());', c_source)
        self.assertIn('printf("value() = %" PRId32 "\\n",', c_source)
        self.assertIn('jack_cleanup_return_value_', c_source)
        self.assertIn('jack_cleanup_error_value_', c_source)
        self.assertGreaterEqual(c_source.count('Tracer_deinit(&tracer);'), 7)

    def test_cleanup_lowering_supports_raising_branch_and_loop_conditions(self):
        program = lower_hir_static_cleanups(lower_to_hir(compile_and_validate(
            access_error_source() + fail_source() + '''
                struct Tracer {
                    deinit(move self) {
                    }
                }

                bool condition() raises AccessError {
                    fail();
                    return true;
                }

                void run() raises AccessError {
                    Tracer tracer;
                    if (condition()) {
                    } elif (condition()) {
                    }
                    while (condition()) {
                    }
                }
            '''
        )))
        run = next(
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'run'
        )

        condition_temporaries = [
            statement
            for statement in run.body
            if isinstance(statement, HIRVariableDeclaration)
            and statement.symbol.name.startswith('jack_cleanup_condition_')
        ]
        self.assertEqual(2, len(condition_temporaries))
        self.assertTrue(any(isinstance(statement, HIRIf) for statement in run.body))
        self.assertTrue(any(isinstance(statement, HIRWhile) for statement in run.body))
        self.assertGreaterEqual(
            sum(isinstance(statement, HIRTry) for statement in run.body),
            2,
        )

    def test_cleanup_lowering_desugars_effectful_for_headers_in_scoped_block(self):
        program = lower_hir_static_cleanups(lower_to_hir(compile_and_validate(
            access_error_source() + fail_source() + '''
                struct Tracer {
                    deinit(move self) {
                    }
                }

                bool condition() raises AccessError {
                    fail();
                    return true;
                }

                void update() raises AccessError {
                    fail();
                }

                void run() raises AccessError {
                    for (Tracer tracer; condition(); update()) {
                    }
                }
            '''
        )))
        run = next(
            declaration
            for declaration in program.declarations
            if isinstance(declaration, HIRFunctionDeclaration)
            and declaration.name == 'run'
        )

        self.assertEqual(1, len(run.body))
        self.assertIsInstance(run.body[0], HIRBlock)
        block = run.body[0]
        self.assertIsInstance(block.body[0], HIRVariableDeclaration)
        self.assertTrue(any(isinstance(statement, HIRWhile) for statement in block.body))
        self.assertEqual('Tracer.deinit', block.body[-1].expr.target.name)

    def test_parser_reads_rethrow_statements(self):
        ast = parse(access_error_source() + '''
            void caller() raises AccessError {
                try {
                    AccessError err;
                    err.code = 7;
                    raise err;
                } catch AccessError {
                    rethrow;
                }
            }
        ''')

        statement = ast[1].body[0]
        self.assertEqual(Rethrow, type(statement.catches[0].body[0]))

    def test_compile_time_pass_infers_rethrow_from_catch(self):
        compiled = apply_compile_time_pass(parse(access_error_source() + fail_source() + '''
            void caller() raises {
                try {
                    fail();
                } catch AccessError {
                    rethrow;
                }
            }
        '''), print_handler=None)

        functions = {node.name: node for node in compiled if type(node) is FunctionDeclaration}
        self.assertEqual(['AccessError'], [error.name for error in functions['caller'].raises])

    def test_parser_reads_try_catch_statements(self):
        ast = parse(access_error_source() + '''
            void caller() {
                try {
                    AccessError err;
                    err.code = 7;
                    raise err;
                } catch AccessError err {
                    print(err.code);
                } catch AccessError {
                    print(2);
                }
            }
        ''')

        statement = ast[1].body[0]
        self.assertEqual(Try, type(statement))
        self.assertEqual('AccessError', statement.catches[0].error_type.name)
        self.assertEqual('err', statement.catches[0].name)
        self.assertEqual('AccessError', statement.catches[1].error_type.name)
        self.assertIsNone(statement.catches[1].name)

    def test_semantic_accepts_try_catch_at_top_level(self):
        ast = compile_and_validate(access_error_source() + fail_source() + '''
            try {
                fail();
            } catch AccessError {
                print(1);
            }
        ''')

        self.assertTrue(ast)

    def test_semantic_accepts_catch_binding_payload_access(self):
        ast = compile_and_validate(access_error_source() + '''
            void caller() {
                try {
                    AccessError err;
                    err.code = 7;
                    raise err;
                } catch AccessError err {
                    print(err.code);
                }
            }
        ''')

        self.assertTrue(ast)

    def test_compile_time_pass_removes_caught_errors_from_inferred_raises(self):
        compiled = apply_compile_time_pass(parse(access_error_source() + fail_source() + '''
            void caller() raises {
                try {
                    fail();
                } catch AccessError {
                    print(1);
                }
            }
        '''), print_handler=None)

        functions = {node.name: node for node in compiled if type(node) is FunctionDeclaration}
        self.assertEqual([], functions['caller'].raises)
        self.assertFalse(functions['caller'].raises_inferred)

    def test_compile_time_try_catch_can_bind_payload(self):
        messages: list[str] = []
        apply_compile_time_pass(parse(access_error_source() + '''
            comptime try {
                AccessError err;
                err.code = 11;
                raise err;
            } catch AccessError err {
                print(err.code);
            }
        '''), print_handler=messages.append)

        self.assertEqual(['err.code = 11'], messages)

    def test_compile_time_try_catch_can_bind_struct_literal_payload(self):
        messages: list[str] = []
        apply_compile_time_pass(parse(access_error_source() + '''
            comptime try {
                raise AccessError { code = 12 };
            } catch AccessError err {
                print(err.code);
            }
        '''), print_handler=messages.append)

        self.assertEqual(['err.code = 12'], messages)

    def test_interpreter_runs_try_catch_with_payload_binding(self):
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval(parse(access_error_source() + fail_source(42) + '''
                try {
                    fail();
                    print(1);
                } catch AccessError err {
                    print(err.code);
                }
            '''))

        self.assertEqual('err.code = 42\n', output.getvalue())

    def test_interpreter_runs_try_catch_with_struct_literal_payload_binding(self):
        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval(parse(access_error_source() + fail_literal_source(43) + '''
                try {
                    fail();
                    print(1);
                } catch AccessError err {
                    print(err.code);
                }
            '''))

        self.assertEqual('err.code = 43\n', output.getvalue())

    def test_c_emit_uses_compound_literal_for_struct_literal_payload(self):
        c_source = emit_c(parse(access_error_source() + fail_literal_source(44) + '''
            void caller() raises AccessError {
                fail();
            }
        '''), print_handler=None)

        self.assertIn('(AccessError){.code = 44}', c_source)
        self.assertIn('jack_throw(AccessError_error_tag, &', c_source)

    def test_c_emit_supports_return_inside_try_body(self):
        c_source = emit_c(parse(access_error_source() + '''
            i32 caller() {
                try {
                    return 7;
                } catch AccessError err {
                    return err.code;
                }
            }
        '''), print_handler=None)

        self.assertIn('if (jack_try(&error_frame_1) == 0) {', c_source)
        self.assertIn('jack_end_try(&error_frame_1);\n        return 7;', c_source)

    def test_c_emit_emits_try_catch_dispatch(self):
        c_source = emit_c(parse(access_error_source() + fail_source() + '''
            void caller() {
                try {
                    fail();
                } catch AccessError err {
                    print(err.code);
                }
            }
        '''), print_handler=None)

        self.assertIn('jack_error_frame error_frame_1;', c_source)
        self.assertIn('jack_error caught_error_1;', c_source)
        self.assertIn('if (jack_try(&error_frame_1) == 0) {', c_source)
        self.assertIn('caught_error_1 = jack_current_error;', c_source)
        self.assertIn('if (caught_error_1.tag == AccessError_error_tag) {', c_source)
        self.assertIn('memcpy(&err, caught_error_1.payload, sizeof(err));', c_source)
        self.assertIn('jack_rethrow(caught_error_1);', c_source)

    def test_public_imported_error_structs_rewrite_and_emit_in_split_c(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            module = root / 'errors.jack'
            module.write_text('''
                module errors;

                pub struct AccessError {
                    i32 code;
                }

                pub void fail() raises AccessError {
                    AccessError err;
                    err.code = 7;
                    raise err;
                }
            ''')
            entry = root / 'main.jack'
            entry.write_text('''
                import errors;

                void caller() raises AccessError {
                    fail();
                }
            ''')

            ast = load_source_file(entry)
            validate_runtime_ast(apply_compile_time_pass(ast, print_handler=None))
            files = emit_c_files(ast, print_handler=None)

        self.assertIn('#define errors_AccessError_error_tag 1', files['errors.h'])
        self.assertIn('void errors_fail(void);', files['errors.h'])
        self.assertIn('jack_throw(errors_AccessError_error_tag, &', files['errors.c'])
        self.assertIn('jack_error_frame *jack_error_frame_stack = NULL;', files['main.c'])
        self.assertIn('void caller(void);', files['main.c'])
        self.assertIn('errors_fail();', files['main.c'])
        self.assertNotIn('error_status', files['main.c'])


if __name__ == '__main__':
    unittest.main()
