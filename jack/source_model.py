from __future__ import annotations

from dataclasses import dataclass, field
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
