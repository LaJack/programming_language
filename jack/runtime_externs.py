import sys

from .builtin_types import JackPrimitiveValue
from .interpreter import ExternHandler, JackArray, JackArrayElementBorrow, JackBorrow, JackSlice


def default_runtime_externs(stdout: object | None = None) -> dict[str, ExternHandler]:
    stream = sys.stdout if stdout is None else stdout
    return {
        'stdout': stream,
        'fopen': fopen,
        'jack_std_io_open_read': jack_std_io_open_read,
        'fread': fread,
        'fclose': fclose,
        'fwrite': fwrite,
    }


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
    if isinstance(value, JackPrimitiveValue):
        value = value.value
    byte = int(value)
    if byte < 0 or byte > 255:
        raise ValueError(f'fwrite expected byte value, got {byte}.')
    return byte


def _write_borrowed_bytes(value: object, payload: bytes) -> None:
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
        if isinstance(current, JackPrimitiveValue):
            array.values[index] = JackPrimitiveValue(current.type_name, byte)
        else:
            array.values[index] = byte


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
