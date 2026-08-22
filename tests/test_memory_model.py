import copy
import unittest

from jack.interpreter import EvaluationError
from jack.memory_model import (
    AllocationError,
    AllocationToken,
    Layout,
    LayoutError,
    MaybeUninit,
    USIZE_MAX,
    UninitializedStorageError,
)
from jack.runtime_externs import free, malloc


class MemoryModelTests(unittest.TestCase):
    def test_layout_checks_alignment_and_array_overflow(self):
        self.assertEqual(Layout(12, 4), Layout.array(Layout(4, 4), 3))
        with self.assertRaises(LayoutError):
            Layout(1, 3)
        with self.assertRaisesRegex(LayoutError, 'overflows'):
            Layout.array(Layout(2, 2), USIZE_MAX)

    def test_maybe_uninit_has_explicit_object_lifetime(self):
        slot = MaybeUninit()
        with self.assertRaises(UninitializedStorageError):
            slot.get()
        slot.write('value')
        self.assertEqual('value', slot.get())
        self.assertEqual('value', slot.take())
        with self.assertRaises(UninitializedStorageError):
            slot.take()

    def test_allocation_tokens_are_linear_and_origin_checked(self):
        token = AllocationToken(7, Layout(8, 8), allocator_identity=3)
        with self.assertRaisesRegex(AllocationError, 'belongs to allocator'):
            token.consume(4)
        token.consume(3)
        with self.assertRaisesRegex(AllocationError, 'already consumed'):
            token.consume(3)

    def test_interpreter_malloc_tracks_provenance_and_liveness(self):
        pointer = malloc(4)
        self.assertIsNotNone(pointer)
        pointer_copy = copy.deepcopy(pointer)
        with self.assertRaisesRegex(EvaluationError, 'uninitialized'):
            pointer.get()
        with self.assertRaisesRegex(EvaluationError, 'out of bounds'):
            pointer.offset(4)
        free(pointer)
        with self.assertRaisesRegex(EvaluationError, 'freed allocation'):
            pointer_copy.get()
        with self.assertRaisesRegex(ValueError, 'already freed'):
            free(pointer)


if __name__ == '__main__':
    unittest.main()
