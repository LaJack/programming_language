from .hir_nodes import HIRProgram
from .llvm_lowering_pass import LLVMLoweringError, lower_to_llvm


def emit_hir_llvm(
    program: HIRProgram, *, debug: bool = False, optimization: int = 0
) -> str:
    return lower_to_llvm(
        program, debug=debug, optimization=optimization
    ).render()


__all__ = ['LLVMLoweringError', 'emit_hir_llvm']
