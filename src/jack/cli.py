from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .compiler import CompileError, compile_sources, compile_sources_to_llvm_ir
from .interpreter import interpret_sources
from .parser import ParseError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jack")
    parser.add_argument(
        "-i",
        "--interpret",
        action="store_true",
        help="interpret the source files instead of compiling them",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("a.out"),
        help="write the compiled executable to this path",
    )
    parser.add_argument(
        "--emit-llvm",
        action="store_true",
        help="emit LLVM IR to stdout instead of linking an executable",
    )
    parser.add_argument("sources", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.interpret:
            interpret_sources(args.sources)
        elif args.emit_llvm:
            print(compile_sources_to_llvm_ir(args.sources), end="")
        else:
            compile_sources(args.sources, args.output)
    except (
        CompileError,
        NotImplementedError,
        ParseError,
        ValueError,
        KeyError,
        OSError,
    ) as exc:
        parser.exit(1, f"jack: {exc}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
