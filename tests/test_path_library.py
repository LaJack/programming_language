import errno
import os
import tempfile
import unittest
from pathlib import Path

from jack.interpreter import JackArray, JackArrayElementBorrow, JackBorrow, JackRawPointer
from jack.memory_model import MaybeUninit, UninitializedStorageError
from jack.runtime_externs import jack_io_canonical_path, _write_array_bytes
from jack.source_model import TypeReference


class PathBridgeTests(unittest.TestCase):
    def _call(self, path, capacity):
        array = JackArray(TypeReference('u8'), [77] * capacity)
        pointer = JackRawPointer(
            JackArrayElementBorrow(array, 0, mutable=True, mode='out'), mutable=True
        )
        length = JackBorrow(999, mutable=True, mode='out')
        status = jack_io_canonical_path(path, pointer, capacity, length)
        return status, length.value, array.values

    def test_size_query_exact_fit_and_undersized_buffers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'unicode-\u00e9.jack'
            path.touch()
            expected = os.fsencode(path.resolve())
            for capacity in (0, 1, len(expected) - 1, len(expected), len(expected) + 1):
                with self.subTest(capacity=capacity):
                    status, length, output = self._call(os.path.relpath(path), capacity)
                    self.assertEqual(0, status)
                    self.assertEqual(len(expected), length)
                    if capacity < length:
                        self.assertEqual([77] * capacity, output)
                    else:
                        self.assertEqual(expected, bytes(output[:length]))
                        self.assertEqual([77] * (capacity - length), output[length:])

    def test_invalid_and_missing_paths_and_symlink_loops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loop = root / 'loop'
            loop.symlink_to(loop)
            for path, expected in (
                ('', errno.EINVAL), ('bad\0path', errno.EINVAL),
                (str(root / 'missing'), errno.ENOENT), (str(loop), errno.ELOOP),
            ):
                with self.subTest(path=path):
                    status, length, output = self._call(path, 8)
                    self.assertEqual(expected, status)
                    self.assertEqual(0, length)
                    self.assertEqual([77] * 8, output)

    def test_io_writes_preserve_initialized_storage_slots(self):
        slot = MaybeUninit()
        slot.write(12)
        array = JackArray(TypeReference('u8'), [slot])
        _write_array_bytes(array, 0, b'A')
        self.assertIs(slot, array.values[0])
        self.assertEqual(65, slot.take())
        with self.assertRaises(UninitializedStorageError):
            _write_array_bytes(array, 0, b'B')
