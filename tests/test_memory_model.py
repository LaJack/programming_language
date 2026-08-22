import copy
import io
import unittest
from contextlib import redirect_stdout

from jack.interpreter import EvaluationError
from jack.cleanup_lowering_pass import lower_hir_static_cleanups
from jack.c_emit_pass import emit_hir_c
from jack.hir_lowering_pass import compile_to_hir
from jack.llvm_emit_pass import emit_hir_llvm
from jack.parser import parse
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

    def test_language_maybe_uninit_is_checked_by_interpreter(self):
        source = '''
unsafe void run() {
    MaybeUninit(i32) slot;
    i32 value = 7;
    slot.write(value);
    i32 result = slot.take();
    print(result);
}
unsafe { run(); }
'''
        from jack.interpreter import Interpreter

        output = io.StringIO()
        with redirect_stdout(output):
            Interpreter().eval_hir_program(
                compile_to_hir(parse(source), print_handler=None)
            )
        self.assertEqual('result = 7\n', output.getvalue())

    def test_language_maybe_uninit_has_element_layout_in_native_ir(self):
        source = '''
unsafe void run() {
    MaybeUninit(i32) slot;
    i32 value = 7;
    slot.write(value);
    i32 result = slot.take();
}
unsafe { run(); }
'''
        program = lower_hir_static_cleanups(
            compile_to_hir(parse(source), print_handler=None)
        )
        c_source = emit_hir_c(program)
        llvm_source = emit_hir_llvm(program)
        self.assertRegex(c_source, r'int32_t slot;')
        self.assertNotIn('%"MaybeUninit$comptime$T$i32" = type', llvm_source)
        self.assertRegex(llvm_source, r'%local\d+ = alloca i32')

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
