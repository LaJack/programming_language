import unittest

from jack.ast_nodes import LiteralExpression
from jack.compile_time_pass import (
    ComptimeArrayValue,
    ComptimeBorrowValue,
    ComptimeOpaqueValue,
    ComptimeVectorValue,
    _clone_comptime_literal,
)
from jack.source_model import TypeReference


class ComptimeCloningTests(unittest.TestCase):
    def test_scalar_clone_uses_a_distinct_cell(self):
        original = LiteralExpression(7, 'usize')
        cloned = _clone_comptime_literal(original)
        self.assertIsNot(original, cloned)
        cloned.value = 9
        self.assertEqual(7, original.value)

    def test_owned_array_copy_is_mutation_isolated(self):
        original = LiteralExpression(
            ComptimeArrayValue(
                TypeReference('u8'), [LiteralExpression(1, 'u8')]
            ),
            'u8[1]',
        )
        cloned = _clone_comptime_literal(original)
        cloned.value.elements[0].value = 2
        self.assertEqual(1, original.value.elements[0].value)

    def test_borrow_clone_preserves_target_identity(self):
        cell = LiteralExpression(3, 'u64')
        original = LiteralExpression(
            ComptimeBorrowValue(TypeReference('u64', borrow='in'), False, cell=cell),
            '&in u64',
        )
        cloned = _clone_comptime_literal(original)
        self.assertIs(cell, cloned.value.cell)
        self.assertIsNot(original.value.type_ref, cloned.value.type_ref)

    def test_resource_and_opaque_identities_are_not_duplicated(self):
        vector = ComptimeVectorValue(
            TypeReference('Vector$u8'), TypeReference('u8'), []
        )
        cloned_vector = _clone_comptime_literal(
            LiteralExpression(vector, 'Vector$u8')
        )
        self.assertIs(vector, cloned_vector.value)

        token = object()
        opaque = ComptimeOpaqueValue(TypeReference('Allocation'), token)
        cloned_opaque = _clone_comptime_literal(
            LiteralExpression(opaque, 'Allocation')
        )
        self.assertIs(token, cloned_opaque.value.value)
        self.assertIsNot(opaque.type_ref, cloned_opaque.value.type_ref)


if __name__ == '__main__':
    unittest.main()
