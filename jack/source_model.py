from __future__ import annotations

from dataclasses import dataclass, field
import copy
from typing import List


@dataclass(frozen=True)
class SourceSpan:
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    start_offset: int
    end_offset: int
    source_path: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class FrozenArrayValue:
    elements: tuple[object, ...]


@dataclass(frozen=True)
class FrozenStructValue:
    type_name: str
    fields: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class FrozenUnionValue:
    type_name: str
    discriminant: int
    fields: tuple[object, ...] = ()


@dataclass
class TypeReference:
    name: str
    arguments: List[object] = field(default_factory=list)
    array_size: object | None = None
    is_slice: bool = False
    borrow: str | None = None
    pointer_mode: str | None = None
    nullable: bool = False
    span: SourceSpan | None = field(default=None, compare=False, kw_only=True)

    def __deepcopy__(self, memo):
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        cloned = TypeReference(
            self.name,
            [],
            array_size=None,
            is_slice=self.is_slice,
            borrow=self.borrow,
            pointer_mode=self.pointer_mode,
            nullable=self.nullable,
            span=self.span,
        )
        memo[id(self)] = cloned
        cloned.arguments = [copy.deepcopy(argument, memo) for argument in self.arguments]
        cloned.array_size = copy.deepcopy(self.array_size, memo)
        return cloned


def is_maybe_uninit_type(type_ref: TypeReference) -> bool:
    return type_ref.name == 'MaybeUninit' and len(type_ref.arguments) == 1


def maybe_uninit_element_type(type_ref: TypeReference) -> TypeReference:
    if not is_maybe_uninit_type(type_ref):
        raise ValueError('Expected MaybeUninit(T).')
    element = type_ref.arguments[0]
    if not isinstance(element, TypeReference):
        raise ValueError('MaybeUninit requires one type argument.')
    return element
