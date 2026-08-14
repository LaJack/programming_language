from dataclasses import dataclass
import struct
import sys


@dataclass(frozen=True)
class BuiltinTypeSpec:
    name: str
    family: str
    bits: int
    c_type: str
    printf_macro: str | None = None
    printf_width: int | None = None
    endian: str | None = None


SIGNED_INTEGER_TYPES = {'i64', 'i32', 'i16', 'i8'}
UNSIGNED_INTEGER_TYPES = {'usize', 'u64', 'u32', 'u16', 'u8'}
ENDIAN_INTEGER_TYPES = {'be_i32', 'le_i32'}
RAW_BYTE_TYPES = {'b64', 'b32', 'b16', 'b8'}
FLOAT_TYPES = {'f64', 'f32'}
BOOL_TYPES = {'bool'}
NUMERIC_TYPES = SIGNED_INTEGER_TYPES | UNSIGNED_INTEGER_TYPES | ENDIAN_INTEGER_TYPES | FLOAT_TYPES
INTEGER_TYPES = SIGNED_INTEGER_TYPES | UNSIGNED_INTEGER_TYPES | ENDIAN_INTEGER_TYPES


BUILTIN_TYPE_SPECS: dict[str, BuiltinTypeSpec] = {
    'i64': BuiltinTypeSpec('i64', 'signed', 64, 'int64_t', 'PRId64'),
    'i32': BuiltinTypeSpec('i32', 'signed', 32, 'int32_t', 'PRId32'),
    'i16': BuiltinTypeSpec('i16', 'signed', 16, 'int16_t', 'PRId16'),
    'i8': BuiltinTypeSpec('i8', 'signed', 8, 'int8_t', 'PRId8'),
    'be_i32': BuiltinTypeSpec('be_i32', 'endian_signed', 32, 'int32_t', 'PRId32', endian='big'),
    'le_i32': BuiltinTypeSpec('le_i32', 'endian_signed', 32, 'int32_t', 'PRId32', endian='little'),
    'usize': BuiltinTypeSpec('usize', 'unsigned', sys.maxsize.bit_length() + 1, 'size_t', 'zu'),
    'u64': BuiltinTypeSpec('u64', 'unsigned', 64, 'uint64_t', 'PRIu64'),
    'u32': BuiltinTypeSpec('u32', 'unsigned', 32, 'uint32_t', 'PRIu32'),
    'u16': BuiltinTypeSpec('u16', 'unsigned', 16, 'uint16_t', 'PRIu16'),
    'u8': BuiltinTypeSpec('u8', 'unsigned', 8, 'uint8_t', 'PRIu8'),
    'b64': BuiltinTypeSpec('b64', 'raw', 64, 'uint64_t', 'PRIx64', 16),
    'b32': BuiltinTypeSpec('b32', 'raw', 32, 'uint32_t', 'PRIx32', 8),
    'b16': BuiltinTypeSpec('b16', 'raw', 16, 'uint16_t', 'PRIx16', 4),
    'b8': BuiltinTypeSpec('b8', 'raw', 8, 'uint8_t', 'PRIx8', 2),
    'f64': BuiltinTypeSpec('f64', 'float', 64, 'double'),
    'f32': BuiltinTypeSpec('f32', 'float', 32, 'float'),
    'bool': BuiltinTypeSpec('bool', 'bool', 1, 'bool'),
}


def is_builtin_type(type_name: str) -> bool:
    return type_name in BUILTIN_TYPE_SPECS


def is_numeric_type(type_name: str) -> bool:
    return type_name in NUMERIC_TYPES


def is_integer_type(type_name: str) -> bool:
    return type_name in INTEGER_TYPES


def is_raw_byte_type(type_name: str) -> bool:
    return type_name in RAW_BYTE_TYPES


def is_endian_integer_type(type_name: str) -> bool:
    return type_name in ENDIAN_INTEGER_TYPES


def is_float_type(type_name: str) -> bool:
    return type_name in FLOAT_TYPES


def is_bool_type(type_name: str) -> bool:
    return type_name in BOOL_TYPES


def builtin_conversion_allowed(source_type: str | None, target_type: str) -> bool:
    target = BUILTIN_TYPE_SPECS[target_type]
    if source_type is None:
        return True

    source = BUILTIN_TYPE_SPECS[source_type]
    if target.family == 'bool' or source.family == 'bool':
        return target.family == source.family == 'bool'
    integer_families = {'signed', 'unsigned', 'endian_signed'}
    if target.family == 'float':
        return source.family in {*integer_families, 'float'}
    if target.family in integer_families:
        return source.family in integer_families
    if target.family == 'raw':
        return source.family in {*integer_families, 'raw'}
    return False


def cast_builtin_value(
    value: object,
    type_name: str,
    source_type: str | None = None,
    memory_raw: bool = False,
) -> object:
    spec = BUILTIN_TYPE_SPECS[type_name]
    if isinstance(value, JackPrimitiveValue):
        source_type = value.type_name
        value = value.value

    if source_type is not None and not builtin_conversion_allowed(source_type, type_name):
        raise TypeError(f'Cannot convert {source_type} to {type_name}.')

    if spec.family == 'bool':
        if type(value) is not bool:
            raise TypeError(f'Cannot convert {value!r} to bool.')
        return value

    if type(value) is bool:
        raise TypeError(f'Cannot convert bool to {type_name}.')

    if spec.family == 'float':
        converted = float(value)
        if type_name == 'f32':
            converted = struct.unpack('f', struct.pack('f', converted))[0]
        return converted

    if type(value) is float:
        raise TypeError(f'Cannot convert float to {type_name}.')

    source_spec = BUILTIN_TYPE_SPECS.get(source_type or '')
    if spec.family == 'raw' and memory_raw and source_spec is not None:
        if source_spec.family in {'signed', 'unsigned', 'endian_signed'} and source_spec.bits == spec.bits:
            return _memory_raw_integer_value(int(value), source_spec)

    converted = int(value)
    if spec.family in {'signed', 'endian_signed'}:
        lower = -(1 << (spec.bits - 1))
        upper = (1 << (spec.bits - 1)) - 1
    else:
        lower = 0
        upper = (1 << spec.bits) - 1
    if converted < lower or converted > upper:
        raise ValueError(f'{converted!r} is outside the range of {type_name}.')
    return converted


def _memory_raw_integer_value(value: int, source_spec: BuiltinTypeSpec) -> int:
    byte_count = source_spec.bits // 8
    endian = source_spec.endian or sys.byteorder
    signed = source_spec.family in {'signed', 'endian_signed'}
    data = value.to_bytes(byte_count, endian, signed=signed)
    return int.from_bytes(data, 'big', signed=False)


def default_builtin_value(type_name: str) -> object:
    if is_bool_type(type_name):
        return False
    if is_float_type(type_name):
        return 0.0
    return 0


def format_builtin_value(value: object, type_name: str) -> str:
    raw_value = value.value if isinstance(value, JackPrimitiveValue) else value
    if is_bool_type(type_name):
        return 'true' if raw_value else 'false'
    if is_raw_byte_type(type_name):
        spec = BUILTIN_TYPE_SPECS[type_name]
        return f'0x{int(raw_value):0{spec.printf_width}x}'
    if is_float_type(type_name):
        return f'{float(raw_value):g}'
    return str(int(raw_value))


class BuiltinType:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(
        self,
        value: object | None = None,
        source_type: str | None = None,
        memory_raw: bool = False,
    ) -> 'JackPrimitiveValue':
        if value is None:
            value = default_builtin_value(self.name)
        return JackPrimitiveValue(
            self.name,
            cast_builtin_value(
                value, self.name, source_type=source_type, memory_raw=memory_raw
            ),
        )


@dataclass(frozen=True)
class JackPrimitiveValue:
    type_name: str
    value: object

    def __str__(self) -> str:
        return format_builtin_value(self, self.type_name)

    def __bool__(self) -> bool:
        return bool(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, JackPrimitiveValue):
            return self.type_name == other.type_name and self.value == other.value
        return self.value == other

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)


def runtime_builtin_types() -> dict[str, BuiltinType]:
    return {name: BuiltinType(name) for name in BUILTIN_TYPE_SPECS}
