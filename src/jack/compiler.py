from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .comptime_pass import ComptimePass
from .llvm_emitter import emit_llvm
from .lowering import lower
from .parser import parse_sources


def compile_sources_to_llvm_ir(sources: Sequence[Path]) -> str:
    """Compile source files to LLVM IR text."""
    ast = parse_sources(sources)
    ast = ComptimePass().run(ast)
    ir_module = lower(ast)
    return emit_llvm(ir_module)


def compile_sources(sources: Sequence[Path]) -> None:
    """Compile source files and emit LLVM IR to stdout."""
    print(compile_sources_to_llvm_ir(sources), end="")
