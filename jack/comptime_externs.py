from __future__ import annotations

from .compile_time_pass import ComptimeBorrowValue


def default_comptime_externs() -> dict[str, object]:
    return {
        'fopen': fopen,
        'jack_std_io_open_read': jack_std_io_open_read,
        'fread': fread,
        'fclose': fclose,
        'fwrite': fwrite,
    }


def fopen(path: object, mode: object) -> object:
    path_text = _borrowed_c_string(path)
    mode_text = _borrowed_c_string(mode)
    python_mode = mode_text if 'b' in mode_text else mode_text + 'b'
    return open(path_text, python_mode)


def jack_std_io_open_read(path: object) -> object:
    return open(_as_str(path), 'rb')


def fread(data: object, size: object, count: object, stream: object) -> int:
    size_value = _as_int(size)
    count_value = _as_int(count)
    if size_value < 0 or count_value < 0:
        raise ValueError('fread size and count must be non-negative.')
    if size_value == 0 or count_value == 0:
        return 0

    byte_count = size_value * count_value
    payload = stream.read(byte_count)
    if isinstance(payload, str):
        payload = payload.encode()
    _write_borrowed_bytes(data, payload)
    return len(payload) // size_value


def fclose(stream: object) -> int:
    stream.close()
    return 0


def fwrite(data: object, size: object, count: object, stream: object) -> int:
    size_value = _as_int(size)
    count_value = _as_int(count)
    if size_value < 0 or count_value < 0:
        raise ValueError('fwrite size and count must be non-negative.')
    if size_value == 0 or count_value == 0:
        return 0

    byte_count = size_value * count_value
    stream.write(_borrowed_bytes(data, byte_count))
    flush = getattr(stream, 'flush', None)
    if flush is not None:
        flush()
    return count_value


def _as_int(value: object) -> int:
    return int(value)


def _as_str(value: object) -> str:
    if type(value) is not str:
        raise TypeError(f'expected str path, got {type(value).__name__}.')
    return value


def _borrowed_c_string(value: object) -> str:
    if not isinstance(value, ComptimeBorrowValue) or value.array is None:
        raise TypeError('C string argument must be a borrowed comptime byte buffer.')

    bytes_out: list[int] = []
    for index in range(value.start, len(value.array.elements)):
        byte = _byte_value(value.array.elements[index].value)
        if byte == 0:
            return bytes(bytes_out).decode()
        bytes_out.append(byte)
    raise ValueError('C string argument is missing a null terminator.')


def _borrowed_bytes(value: object, byte_count: int) -> bytes:
    if not isinstance(value, ComptimeBorrowValue) or value.array is None:
        raise TypeError('data must be a borrowed comptime byte buffer.')
    if byte_count > value.window_length():
        raise ValueError(
            f'requested {byte_count} byte(s) from a comptime buffer of length {value.window_length()}.'
        )
    return bytes(
        _byte_value(value.array.elements[value.start + offset].value)
        for offset in range(byte_count)
    )


def _write_borrowed_bytes(value: object, payload: bytes) -> None:
    if not isinstance(value, ComptimeBorrowValue) or value.array is None:
        raise TypeError('fread data must be a borrowed mutable comptime byte buffer.')
    if not value.mutable:
        raise ValueError('fread data must be mutable.')
    if len(payload) > value.window_length():
        raise ValueError(
            f'fread received {len(payload)} byte(s) for a comptime buffer of length {value.window_length()}.'
        )
    for offset, byte in enumerate(payload):
        cell = value.array.elements[value.start + offset]
        cell.value = byte
        # Keep the existing element type; fread is byte-oriented and Jack will have
        # already coerced the borrow to a C byte-compatible view.


def _byte_value(value: object) -> int:
    byte = int(value)
    if byte < 0 or byte > 255:
        raise ValueError(f'expected byte value, got {byte}.')
    return byte
