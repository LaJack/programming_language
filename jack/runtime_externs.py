import sys
import os
import errno

from .builtin_types import JackPrimitiveValue
from .interpreter import (
    EvaluationError,
    ExternHandler,
    JackAllocationRecord,
    JackArray,
    JackArrayElementBorrow,
    JackBorrow,
    JackRawPointer,
    JackSlice,
    _UNINITIALIZED_VALUE,
)
from .source_model import TypeReference
from .memory_model import MaybeUninit as MemoryMaybeUninit


_next_allocation_identity = 1


def default_runtime_externs(stdout: object | None = None) -> dict[str, ExternHandler]:
    stream = sys.stdout if stdout is None else stdout
    return {
        'stdout': stream,
        'fopen': fopen,
        'jack_std_io_open_read': jack_std_io_open_read,
        'jack_string_view': jack_string_view,
        'jack_bytes_view': jack_bytes_view,
        'jack_bytes_view_mut': jack_bytes_view_mut,
        'jack_str_byte': jack_str_byte,
        'jack_io_open': jack_io_open,
        'jack_io_last_error': jack_io_last_error,
        'jack_io_read': jack_io_read,
        'jack_io_write': jack_io_write,
        'jack_io_write_str': jack_io_write_str,
        'jack_io_flush': jack_io_flush,
        'jack_io_close': jack_io_close,
        'jack_io_close_discard': jack_io_close_discard,
        'jack_io_seek': jack_io_seek,
        'jack_io_tell': jack_io_tell,
        'jack_io_metadata': jack_io_metadata,
        'jack_io_canonical_path': jack_io_canonical_path,
        'jack_io_stdin': lambda: _file_pointer(sys.stdin),
        'jack_io_stdout': lambda: _file_pointer(stream),
        'jack_io_stderr': lambda: _file_pointer(sys.stderr),
        'fread': fread,
        'fclose': fclose,
        'fwrite': fwrite,
        'malloc': malloc,
        'free': free,
    }


def jack_string_view(data: object, length: object) -> str:
    return _borrowed_bytes(data, _as_int(length)).decode('utf-8')


def jack_bytes_view(data: object, length: object) -> JackSlice:
    return _pointer_slice(data, _as_int(length), mutable=False)


def jack_bytes_view_mut(data: object, length: object) -> JackSlice:
    return _pointer_slice(data, _as_int(length), mutable=True)


def jack_str_byte(value: object, index: object) -> int:
    return _as_str(value).encode('utf-8')[_as_int(index)]


_last_io_error = 5


def jack_io_open(path: object, mode: object) -> JackRawPointer | None:
    global _last_io_error
    modes = ('rb', 'wb', 'ab', 'r+b')
    try:
        file_obj = open(_as_str(path), modes[_as_int(mode)])
    except OSError as error:
        _last_io_error = error.errno or 5
        return None
    return _file_pointer(file_obj)


def jack_io_last_error() -> int:
    return _last_io_error


def jack_io_read(file: object, data: object, length: object, count: object) -> int:
    try:
        payload = _unwrap_file_pointer(file).read(_as_int(length))
        if isinstance(payload, str):
            payload = payload.encode('utf-8')
        _write_borrowed_bytes(data, payload)
        _set_out(count, len(payload))
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_write(file: object, data: object, length: object, count: object) -> int:
    payload = _borrowed_bytes(data, _as_int(length))
    try:
        stream = _unwrap_file_pointer(file)
        if hasattr(stream, 'buffer'):
            written = stream.buffer.write(payload)
        else:
            written = stream.write(payload)
        _set_out(count, len(payload) if written is None else written)
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_write_str(file: object, value: object, count: object) -> int:
    payload = _as_str(value).encode('utf-8')
    try:
        stream = _unwrap_file_pointer(file)
        if hasattr(stream, 'buffer'):
            stream.buffer.write(payload)
        else:
            stream.write(payload.decode('utf-8'))
        # Jack str lengths and the native IO ABI count UTF-8 bytes. Python
        # text streams return a character count, which differs for non-ASCII.
        _set_out(count, len(payload))
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_flush(file: object) -> int:
    try:
        _unwrap_file_pointer(file).flush()
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_close(file: object) -> int:
    try:
        _unwrap_file_pointer(file).close()
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_close_discard(file: object) -> None:
    try:
        _unwrap_file_pointer(file).close()
    except OSError:
        pass


def jack_io_seek(
    file: object, offset: object, origin: object, position: object
) -> int:
    try:
        value = _unwrap_file_pointer(file).seek(_as_int(offset), _as_int(origin))
        _set_out(position, value)
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_tell(file: object, position: object) -> int:
    try:
        _set_out(position, _unwrap_file_pointer(file).tell())
        return 0
    except OSError as error:
        return error.errno or 5


def jack_io_metadata(
    path: object, size: object, is_file: object, is_directory: object
) -> int:
    try:
        info = os.stat(_as_str(path))
    except OSError as error:
        return error.errno or 5
    _set_out(size, info.st_size)
    _set_out(is_file, os.path.isfile(_as_str(path)))
    _set_out(is_directory, os.path.isdir(_as_str(path)))
    return 0


def jack_io_canonical_path(path: object, data: object, capacity: object, length: object) -> int:
    _set_out(length, 0)
    try:
        text = _as_str(path)
        if not text or '\0' in text:
            return errno.EINVAL
        resolved = os.path.realpath(text, strict=True).encode('utf-8')
    except OSError as error:
        return error.errno or errno.EIO
    except UnicodeError:
        return errno.EILSEQ
    _set_out(length, len(resolved))
    if len(resolved) <= _as_int(capacity):
        _write_borrowed_bytes(data, resolved)
    return 0


def _file_pointer(file_obj: object) -> JackRawPointer:
    return JackRawPointer(JackBorrow(file_obj, mutable=True), mutable=True)


def _unwrap_file_pointer(value: object) -> object:
    if isinstance(value, JackRawPointer):
        return value.target.value
    return _unwrap_borrow(value)


def _set_out(target: object, value: object) -> None:
    if not isinstance(target, (JackBorrow, JackArrayElementBorrow)):
        raise TypeError('out parameter is not writable.')
    current = None
    try:
        current = target.value
    except EvaluationError:
        pass
    if isinstance(current, JackPrimitiveValue):
        value = JackPrimitiveValue(current.type_name, value)
    target.value = value


def _pointer_slice(data: object, length: int, *, mutable: bool) -> JackSlice:
    if not isinstance(data, JackRawPointer):
        raise TypeError('byte view requires a raw pointer.')
    target = data.target
    if not isinstance(target, JackArrayElementBorrow):
        if length == 0:
            return JackSlice(JackArray(TypeReference('u8'), []), 0, 0, mutable=mutable)
        raise TypeError('byte view requires an array-backed raw pointer.')
    if length < 0 or target.index + length > len(target.array.values):
        raise ValueError('byte view exceeds its backing allocation.')
    array = target.array
    if data.unwrap_storage:
        array = JackArray(TypeReference('u8'), array.values)
    return JackSlice(array, target.index, length, mutable=mutable)


def malloc(size: object) -> JackRawPointer | None:
    global _next_allocation_identity
    byte_count = _as_int(size)
    if byte_count < 0:
        raise ValueError('malloc size must be non-negative.')
    # Keep a unique address even for zero-sized allocations.
    storage = JackArray(
        TypeReference('b8'),
        [_UNINITIALIZED_VALUE for _ in range(max(1, byte_count))],
    )
    record = JackAllocationRecord(
        _next_allocation_identity, storage, alignment=16
    )
    _next_allocation_identity += 1
    return JackRawPointer(
        JackArrayElementBorrow(storage, 0, mutable=True, mode='inout'),
        mutable=True,
        allocation=record,
    )


def free(pointer: object) -> None:
    if pointer is None:
        return
    if not isinstance(pointer, JackRawPointer) or pointer.allocation is None:
        raise ValueError('free requires a pointer returned by malloc.')
    if not pointer.allocation.live:
        raise ValueError(
            f'allocation {pointer.allocation.identity} was already freed.'
        )
    pointer.allocation.live = False


def fopen(path: object, mode: object) -> JackBorrow:
    path_text = _borrowed_c_string(path)
    mode_text = _borrowed_c_string(mode)
    python_mode = mode_text if 'b' in mode_text else mode_text + 'b'
    return JackBorrow(open(path_text, python_mode), mutable=True)


def jack_std_io_open_read(path: object) -> JackBorrow:
    return JackBorrow(open(_as_str(path), 'rb'), mutable=True)


def fread(data: object, size: object, count: object, stream: object) -> int:
    size_value = _as_int(size)
    count_value = _as_int(count)
    if size_value < 0 or count_value < 0:
        raise ValueError('fread size and count must be non-negative.')
    if size_value == 0 or count_value == 0:
        return 0

    byte_count = size_value * count_value
    file_obj = _unwrap_borrow(stream)
    payload = file_obj.read(byte_count)
    if isinstance(payload, str):
        payload = payload.encode()
    _write_borrowed_bytes(data, payload)
    return len(payload) // size_value


def fclose(stream: object) -> int:
    _unwrap_borrow(stream).close()
    return 0


def fwrite(data: object, size: object, count: object, stream: object) -> int:
    size_value = _as_int(size)
    count_value = _as_int(count)
    if size_value < 0 or count_value < 0:
        raise ValueError('fwrite size and count must be non-negative.')
    if size_value == 0 or count_value == 0:
        return 0

    byte_count = size_value * count_value
    payload = _borrowed_bytes(data, byte_count)
    _write_bytes(_unwrap_borrow(stream), payload)
    return count_value


def _as_int(value: object) -> int:
    if isinstance(value, JackPrimitiveValue):
        return int(value)
    return int(value)


def _as_str(value: object) -> str:
    if type(value) is not str:
        raise TypeError(f'expected str path, got {type(value).__name__}.')
    return value


def _unwrap_borrow(value: object) -> object:
    if isinstance(value, JackBorrow):
        return value.value
    return value


def _borrowed_bytes(value: object, byte_count: int) -> bytes:
    if byte_count == 0:
        return b''
    if isinstance(value, JackRawPointer):
        target = value.target
        if isinstance(target, JackArrayElementBorrow):
            return _array_bytes(target.array, target.index, byte_count)
        return _scalar_byte(target.value, byte_count)
    if isinstance(value, JackArrayElementBorrow):
        return _array_bytes(value.array, value.index, byte_count)
    if isinstance(value, JackSlice):
        if byte_count > value.length:
            raise ValueError(
                f'fwrite requested {byte_count} byte(s) from a slice of length {value.length}.'
            )
        return _array_bytes(value.array, value.start, byte_count)
    if isinstance(value, JackBorrow):
        if isinstance(value.value, JackArray):
            return _array_bytes(value.value, 0, byte_count)
        return _scalar_byte(value.value, byte_count)
    raise TypeError('fwrite data must be a borrowed byte buffer.')


def _borrowed_c_string(value: object) -> str:
    if isinstance(value, JackArrayElementBorrow):
        data = value.array.values
        start = value.index
    elif isinstance(value, JackSlice):
        data = value.array.values
        start = value.start
    elif isinstance(value, JackBorrow) and isinstance(value.value, JackArray):
        data = value.value.values
        start = 0
    else:
        raise TypeError('C string argument must be a borrowed byte buffer.')

    bytes_out: list[int] = []
    for item in data[start:]:
        byte = _byte_value(item)
        if byte == 0:
            return bytes(bytes_out).decode()
        bytes_out.append(byte)
    raise ValueError('C string argument is missing a null terminator.')


def _array_bytes(array: JackArray, start: int, byte_count: int) -> bytes:
    end = start + byte_count
    if start < 0 or end > len(array.values):
        raise ValueError(
            f'fwrite requested {byte_count} byte(s) from offset {start}, '
            f'but the buffer length is {len(array.values)}.'
        )
    return bytes(_byte_value(item) for item in array.values[start:end])


def _scalar_byte(value: object, byte_count: int) -> bytes:
    if byte_count != 1:
        raise ValueError('fwrite can only read one byte from a scalar byte borrow.')
    return bytes([_byte_value(value)])


def _byte_value(value: object) -> int:
    if isinstance(value, MemoryMaybeUninit):
        value = value.get()
    if isinstance(value, JackPrimitiveValue):
        value = value.value
    byte = int(value)
    if byte < 0 or byte > 255:
        raise ValueError(f'fwrite expected byte value, got {byte}.')
    return byte


def _write_borrowed_bytes(value: object, payload: bytes) -> None:
    if isinstance(value, JackRawPointer):
        target = value.target
        if isinstance(target, JackArrayElementBorrow):
            _write_array_bytes(target.array, target.index, payload)
            return
    if isinstance(value, JackArrayElementBorrow):
        _write_array_bytes(value.array, value.index, payload)
        return
    if isinstance(value, JackSlice):
        if len(payload) > value.length:
            raise ValueError(
                f'fread received {len(payload)} byte(s) for a slice of length {value.length}.'
            )
        _write_array_bytes(value.array, value.start, payload)
        return
    if isinstance(value, JackBorrow) and isinstance(value.value, JackArray):
        _write_array_bytes(value.value, 0, payload)
        return
    raise TypeError('fread data must be a borrowed mutable byte buffer.')


def _write_array_bytes(array: JackArray, start: int, payload: bytes) -> None:
    end = start + len(payload)
    if start < 0 or end > len(array.values):
        raise ValueError(
            f'fread received {len(payload)} byte(s) at offset {start}, '
            f'but the buffer length is {len(array.values)}.'
        )
    for index, byte in enumerate(payload, start=start):
        current = array.values[index]
        slot = current if isinstance(current, MemoryMaybeUninit) else None
        if slot is not None:
            current = slot.get()
        if isinstance(current, JackPrimitiveValue):
            replacement = JackPrimitiveValue(current.type_name, byte)
        else:
            replacement = byte
        if slot is not None:
            slot.set_initialized(replacement)
        else:
            array.values[index] = replacement


def _write_bytes(stream: object, payload: bytes) -> None:
    if hasattr(stream, 'buffer'):
        stream.buffer.write(payload)
    elif hasattr(stream, 'write'):
        stream.write(payload.decode('utf-8', errors='replace'))
    else:
        raise TypeError('fwrite stream must provide write() or buffer.write().')

    flush = getattr(stream, 'flush', None)
    if flush is not None:
        flush()
