from dataclasses import dataclass
import sys


class LayoutError(ValueError):
    pass


class AllocationError(RuntimeError):
    pass


class UninitializedStorageError(RuntimeError):
    pass


USIZE_MAX = (1 << (sys.maxsize.bit_length() + 1)) - 1


@dataclass(frozen=True)
class Layout:
    size: int
    alignment: int

    def __post_init__(self) -> None:
        if self.size < 0 or self.size > USIZE_MAX:
            raise LayoutError(f'Layout size {self.size} is outside usize range.')
        if (
            self.alignment <= 0
            or self.alignment > USIZE_MAX
            or self.alignment & (self.alignment - 1)
        ):
            raise LayoutError(
                f'Layout alignment {self.alignment} must be a positive power of two.'
            )

    @classmethod
    def array(cls, element: 'Layout', count: int) -> 'Layout':
        if count < 0:
            raise LayoutError('Array layout count must be non-negative.')
        if element.size and count > USIZE_MAX // element.size:
            raise LayoutError('Array layout size overflows usize.')
        return cls(element.size * count, element.alignment)


_UNINITIALIZED = object()


class MaybeUninit:
    def __init__(self) -> None:
        self._value: object = _UNINITIALIZED

    @property
    def initialized(self) -> bool:
        return self._value is not _UNINITIALIZED

    def write(self, value: object) -> None:
        if self.initialized:
            raise UninitializedStorageError(
                'MaybeUninit slot is already initialized; move it out first.'
            )
        self._value = value

    def take(self) -> object:
        if not self.initialized:
            raise UninitializedStorageError('MaybeUninit slot is not initialized.')
        value = self._value
        self._value = _UNINITIALIZED
        return value

    def get(self) -> object:
        if not self.initialized:
            raise UninitializedStorageError('MaybeUninit slot is not initialized.')
        return self._value


@dataclass
class AllocationToken:
    identity: int
    layout: Layout
    allocator_identity: int
    live: bool = True

    def consume(self, allocator_identity: int) -> None:
        if not self.live:
            raise AllocationError(f'Allocation {self.identity} was already consumed.')
        if allocator_identity != self.allocator_identity:
            raise AllocationError(
                f'Allocation {self.identity} belongs to allocator '
                f'{self.allocator_identity}, not {allocator_identity}.'
            )
        self.live = False
