from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .ast_nodes import Statement


def parse_sources(sources: Sequence[Path]) -> list[Statement]:
    """Parse source files into an AST."""
    for source in sources:
        source.read_text()
    raise NotImplementedError("source parsing is not implemented yet")
