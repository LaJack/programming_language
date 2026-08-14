import unittest

from jack.ast_nodes import TypeReference
from jack.c_emit_pass import emit_c
from jack.hir_lowering_pass import compile_to_hir
from jack.hir_nodes import HIRCallExpression
from jack.interpreter import Interpreter
from jack.jack_emit_pass import emit_jack
from jack.parser import parse
from jack.semantic_pass import SemanticError, validate_runtime_ast


class ExplicitMethodSelfBorrowTests(unittest.TestCase):
    def test_parser_extracts_explicit_self_parameter(self):
        ast = parse('''
            struct Counter {
                i32 value;

                i32 get(&in self) {
                    return self.value;
                }

                void add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                }
            }
        ''')

        get_method = ast[0].methods[0]
        add_method = ast[0].methods[1]

        self.assertEqual(TypeReference('self', borrow='in'), get_method.self_parameter.type)
        self.assertEqual([], get_method.parameters)
        self.assertEqual(TypeReference('self', borrow='inout'), add_method.self_parameter.type)
        self.assertEqual(['delta'], [parameter.name for parameter in add_method.parameters])

    def test_semantic_requires_explicit_self_parameter(self):
        with self.assertRaisesRegex(SemanticError, 'explicit self parameter'):
            validate_runtime_ast(parse('''
                struct Counter {
                    i32 value;

                    void add(i32 delta) {
                        self.value = self.value + delta;
                    }
                }
            '''))

    def test_semantic_rejects_write_through_in_self(self):
        with self.assertRaisesRegex(SemanticError, 'through &in borrow'):
            validate_runtime_ast(parse('''
                struct Counter {
                    i32 value;

                    void bad(&in self) {
                        self.value = 1;
                    }
                }
            '''))

    def test_interpreter_uses_explicit_self_borrows(self):
        interpreter = Interpreter()
        interpreter.eval_source_ast(parse('''
            struct Counter {
                i32 value;

                init(&inout self, i32 value) {
                    self.value = value;
                }

                i32 get(&in self) {
                    return self.value;
                }

                void add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                }
            }

            Counter counter(3);
            i32 before = counter.get();
            counter.add(4);
            i32 after = counter.get();
        '''))

        self.assertEqual(3, interpreter.global_scope.get('before'))
        self.assertEqual(7, interpreter.global_scope.get('after'))
        self.assertEqual(7, interpreter.global_scope.get('counter.value'))

    def test_view_self_borrow_limits_method_surface(self):
        interpreter = Interpreter()
        interpreter.eval_source_ast(parse('''
            struct Packet {
                i32 header;
                i32 checksum;

                void refresh(&inout PacketChecksumView self) {
                    i32 header = self.header;
                    self.checksum = header + 1;
                }
            }

            view PacketChecksumView {
                in i32 header;
                out i32 checksum;
            }

            Packet packet;
            packet.header = 41;
            packet.refresh();
        '''))

        self.assertEqual(42, interpreter.global_scope.get('packet.checksum'))

    def test_interpreter_prepares_hir_method_call_targets(self):
        program = compile_to_hir(parse('''
            struct Counter {
                i32 value;

                void add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                }
            }

            Counter counter;
            counter.add(5);
        '''), print_handler=None)
        interpreter = Interpreter()
        interpreter.eval_hir_program(program)
        call = program.top_level[-1].expr

        self.assertEqual(5, interpreter.global_scope.get('counter.value'))
        self.assertIsInstance(call, HIRCallExpression)
        self.assertEqual('method', call.target.kind)
        self.assertEqual('Counter.add', call.target.name)
        self.assertEqual('counter', call.target.receiver_name)

    def test_c_emit_uses_declared_self_borrow(self):
        c_source = emit_c(parse('''
            struct Counter {
                i32 value;

                i32 get(&in self) {
                    return self.value;
                }

                void add(&inout self, i32 delta) {
                    self.value = self.value + delta;
                }
            }

            Counter counter;
            i32 value = counter.get();
            counter.add(2);
        '''))

        self.assertIn('int32_t Counter_get(const Counter *self);', c_source)
        self.assertIn('void Counter_add(Counter *self, int32_t delta);', c_source)
        self.assertIn('value = Counter_get(&counter);', c_source)
        self.assertIn('Counter_add(&counter, 2);', c_source)

    def test_c_emit_uses_view_self_descriptor(self):
        c_source = emit_c(parse('''
            struct Packet {
                i32 header;
                i32 checksum;

                void refresh(&inout PacketChecksumView self) {
                    i32 header = self.header;
                    self.checksum = header + 1;
                }
            }

            view PacketChecksumView {
                in i32 header;
                out i32 checksum;
            }

            Packet packet;
            packet.refresh();
        '''))

        self.assertIn('void Packet_refresh(PacketChecksumView self);', c_source)
        self.assertIn('int32_t header = (*self.header);', c_source)
        self.assertIn('(*self.checksum) = (header + 1);', c_source)
        self.assertIn('Packet_refresh((PacketChecksumView){.header = &packet.header, .checksum = &packet.checksum});', c_source)

    def test_jack_emit_round_trips_explicit_self(self):
        emitted = emit_jack(parse('''
            struct Counter {
                i32 value;

                i32 get(&in self) {
                    return self.value;
                }
            }
        '''))

        self.assertIn('i32 get(&in self)', emitted)


if __name__ == '__main__':
    unittest.main()
