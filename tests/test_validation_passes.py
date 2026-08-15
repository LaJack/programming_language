import unittest

from jack.hir_nodes import HIRProgram, HIRVariableDeclaration, HIRVariableSymbol
from jack.hir_validation_pass import HIRValidationError, validate_backend_hir
from jack.llvm_ir import LLVMFunction, LLVMModule, LLVMValidationError
from jack.source_model import TypeReference


class BackendHIRValidationTests(unittest.TestCase):
    def test_rejects_comptime_symbols(self):
        program = HIRProgram(top_level=[
            HIRVariableDeclaration(
                symbol=HIRVariableSymbol(
                    name='value', type_ref=TypeReference('i32'), comptime=True
                )
            )
        ])

        with self.assertRaisesRegex(HIRValidationError, 'Comptime symbol'):
            validate_backend_hir(program)

    def test_rejects_non_normalized_array_extent(self):
        program = HIRProgram(top_level=[
            HIRVariableDeclaration(
                symbol=HIRVariableSymbol(
                    name='values',
                    type_ref=TypeReference('i32', array_size='four'),
                )
            )
        ])

        with self.assertRaisesRegex(HIRValidationError, 'non-normalized extent'):
            validate_backend_hir(program)

    def test_rejects_unknown_runtime_type(self):
        program = HIRProgram(top_level=[
            HIRVariableDeclaration(
                symbol=HIRVariableSymbol(
                    name='value', type_ref=TypeReference('Missing')
                )
            )
        ])

        with self.assertRaisesRegex(HIRValidationError, 'not concrete'):
            validate_backend_hir(program)


class LLVMValidationTests(unittest.TestCase):
    def function(self, *instructions):
        return LLVMFunction('test', 'void', (), (('entry', tuple(instructions)),))

    def test_rejects_duplicate_ssa_names(self):
        module = LLVMModule(functions=[self.function(
            '%value = add i32 1, 2',
            '%value = add i32 3, 4',
            'ret void',
        )])

        with self.assertRaisesRegex(LLVMValidationError, 'Duplicate SSA'):
            module.validate()

    def test_rejects_missing_branch_target(self):
        module = LLVMModule(functions=[self.function('br label %missing')])

        with self.assertRaisesRegex(LLVMValidationError, 'missing block'):
            module.validate()

    def test_rejects_instructions_after_terminator(self):
        module = LLVMModule(functions=[self.function(
            'ret void',
            '%late = add i32 1, 2',
        )])

        with self.assertRaisesRegex(LLVMValidationError, 'after its terminator'):
            module.validate()

    def test_rejects_result_without_payload_type(self):
        module = LLVMModule(
            type_definitions=['%jack.result.0 = type { i1, i32 }'],
            functions=[LLVMFunction(
                'test', '%jack.result.0', (),
                (('entry', ('ret %jack.result.0 zeroinitializer',)),),
            )],
        )

        with self.assertRaisesRegex(LLVMValidationError, 'does not contain'):
            module.validate()

    def test_rejects_undeclared_concrete_payload_type(self):
        module = LLVMModule(
            type_definitions=[
                '%jack.error.payload = type { %"MissingError" }',
            ],
            functions=[self.function('ret void')],
        )

        with self.assertRaisesRegex(LLVMValidationError, 'undeclared type'):
            module.validate()


if __name__ == '__main__':
    unittest.main()
