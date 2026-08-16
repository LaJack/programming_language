import unittest
import tempfile
from pathlib import Path

from jack.ast_nodes import ImplementationDeclaration, InterfaceDeclaration
from jack.compile_time_pass import CompileTimeError, apply_compile_time_pass
from jack.hir_lowering_pass import lower_to_hir
from jack.jack_emit_pass import emit_jack
from jack.module_loader import load_source_file
from jack.parser import parse, parse_recovering
from jack.semantic_pass import SemanticError, validate_runtime_ast


class InterfaceParserTests(unittest.TestCase):
    SOURCE = '''
pub interface Serializable {
    usize size(&in self);
    void write(&in self, &out u8[] destination);
}

Message implements Serializable {
    use size;
    void write(&in self, &out u8[] destination) { }
}

void save(comptime type T: Copyable + Serializable, &in T value) { }
'''

    def test_interface_implementation_and_constraints_round_trip(self):
        ast = parse(self.SOURCE)
        self.assertIsInstance(ast[0], InterfaceDeclaration)
        self.assertIsInstance(ast[1], ImplementationDeclaration)
        self.assertEqual(['Copyable', 'Serializable'], [
            item.name for item in ast[2].parameters[0].constraints
        ])
        emitted = emit_jack(ast)
        reparsed = parse(emitted)
        self.assertEqual(ast, reparsed)

    def test_recovery_keeps_declaration_after_bad_interface_method(self):
        result = parse_recovering('interface Bad { void broken( ; }\nvoid good() {}')
        self.assertTrue(result.diagnostics)
        self.assertTrue(any(getattr(node, 'name', None) == 'good' for node in result.statements))


class InterfaceSemanticTests(unittest.TestCase):
    def _runtime(self, source):
        return apply_compile_time_pass(parse(source), print_handler=None)

    def test_use_binding_compiles_to_hir(self):
        runtime = self._runtime('''
interface Sized { usize size(&in self); }
struct Message { usize size(&in self) { return 1; } }
Message implements Sized { use size; }
void main() { Message value; print(value.size()); }
''')
        program = lower_to_hir(runtime)
        self.assertEqual(['Message', 'main'], [item.name for item in program.declarations])

    def test_implementation_method_compiles_to_hir(self):
        runtime = self._runtime('''
interface Sized { usize size(&in self); }
struct Message { }
Message implements Sized { usize size(&in self) { return 1; } }
void inspect(comptime type T: Sized, &in T value) { print(value.size()); }
void main() { Message value; inspect(Message, value); }
''')
        program = lower_to_hir(runtime)
        message = program.declarations[0]
        self.assertEqual(['Sized$size'], [method.name for method in message.methods])
        self.assertEqual('size', message.methods[0].source_name)

    def test_missing_requirement_is_rejected(self):
        with self.assertRaisesRegex(CompileTimeError, 'missing method "size"'):
            self._runtime('''
interface Sized { usize size(&in self); }
struct Message { }
Message implements Sized { }
''')

    def test_constraint_is_checked_when_specializing(self):
        with self.assertRaisesRegex(CompileTimeError, 'does not implement "Sized"'):
            self._runtime('''
interface Sized { usize size(&in self); }
struct Message { }
void inspect(comptime type T: Sized, &in T value) { }
Message value;
inspect(Message, value);
''')

    def test_implementation_must_be_owned_by_type_or_interface_module(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'types.jack').write_text(
                'module types; pub struct Value { usize size(&in self) { return 1; } }'
            )
            (root / 'contracts.jack').write_text(
                'module contracts; pub interface Sized { usize size(&in self); }'
            )
            entry = root / 'main.jack'
            entry.write_text('''
module app;
import types;
import contracts;
Value implements Sized { use size; }
''')

            with self.assertRaisesRegex(CompileTimeError, 'owning its type or interface'):
                apply_compile_time_pass(
                    load_source_file(entry, search_roots=[root]), print_handler=None
                )


if __name__ == '__main__':
    unittest.main()
