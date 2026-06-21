from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
from typing import Sequence

from .comptime_pass import ComptimePass
from .llvm_emitter import emit_llvm
from .lowering import lower
from .parser import parse_sources


class CompileError(RuntimeError):
    pass


def compile_sources_to_llvm_ir(sources: Sequence[Path]) -> str:
    """Compile source files to LLVM IR text."""
    ast = parse_sources(sources)
    ast = ComptimePass().run(ast)
    ir_module = lower(ast)
    return emit_llvm(ir_module)


def compile_sources_to_executable(
    sources: Sequence[Path],
    output: Path,
    clang: str = "clang",
) -> None:
    """Compile source files to a native executable using clang."""
    llvm_ir = compile_sources_to_llvm_ir(sources)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile("w", suffix=".ll", delete=False) as temp_file:
            temp_file.write(llvm_ir)
            temp_path = Path(temp_file.name)

        result = subprocess.run(
            [clang, str(temp_path), "-o", str(output)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise CompileError(message or f"{clang} failed with exit code {result.returncode}")


def compile_sources(sources: Sequence[Path], output: Path = Path("a.out")) -> None:
    """Compile source files to a native executable."""
    compile_sources_to_executable(sources, output)
