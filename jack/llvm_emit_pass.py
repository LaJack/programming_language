from .hir_nodes import HIRProgram
from .llvm_lowering_pass import LLVMLoweringError, lower_to_llvm


def emit_hir_llvm(program: HIRProgram) -> str:
    return lower_to_llvm(program).render()


__all__ = ['LLVMLoweringError', 'emit_hir_llvm']
