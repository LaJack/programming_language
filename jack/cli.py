import argparse
import sys
from pathlib import Path

from .ast_nodes import SourceSpan
from .c_emit_pass import CEmitError, emit_c, emit_c_files
from .cleanup_lowering_pass import CleanupLoweringError
from .compile_time_pass import CompileTimeError, apply_compile_time_pass
from .comptime_externs import default_comptime_externs
from .interpreter import Interpreter, InterpreterError
from .hir_lowering_pass import compile_to_hir
from .jack_emit_pass import JackEmitError, emit_jack
from .module_loader import ModuleLoadError, load_source_file
from .parser import ParseError, parse
from .runtime_externs import default_runtime_externs
from .semantic_pass import SemanticError, validate_runtime_ast


def run_interpreter(
    path: Path,
    import_overrides: dict[str, str] | None = None,
    module_roots: list[Path] | None = None,
) -> None:
    ast = load_source_file(path, import_overrides=import_overrides, search_roots=module_roots)
    comptime_externs = default_comptime_externs()
    program = compile_to_hir(ast, externs=comptime_externs)
    Interpreter(
        externs=default_runtime_externs(),
        comptime_externs=comptime_externs,
    ).eval_hir_program(program)


def run_c_emitter(
    path: Path,
    import_overrides: dict[str, str] | None = None,
    module_roots: list[Path] | None = None,
    output_dir: Path | None = None,
) -> None:
    ast = load_source_file(path, import_overrides=import_overrides, search_roots=module_roots)
    if output_dir is None:
        print(
            emit_c(
                ast,
                print_handler=lambda line: print(line, file=sys.stderr),
                externs=default_comptime_externs(),
            ),
            end='',
        )
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    files = emit_c_files(
        ast,
        print_handler=lambda line: print(line, file=sys.stderr),
        externs=default_comptime_externs(),
    )
    for name, content in files.items():
        (output_dir / name).write_text(content)
    _copy_c_runtime_files(output_dir)


def run_jack_emitter(
    path: Path,
    after: str,
    import_overrides: dict[str, str] | None = None,
    module_roots: list[Path] | None = None,
) -> None:
    if after == 'parse':
        print(emit_jack(parse(path.read_text())), end='')
        return

    ast = load_source_file(path, import_overrides=import_overrides, search_roots=module_roots)
    runtime_ast = apply_compile_time_pass(
        ast,
        print_handler=lambda line: print(line, file=sys.stderr),
        externs=default_comptime_externs(),
    )
    if after == 'comptime':
        print(emit_jack(validate_runtime_ast(runtime_ast)), end='')
        return

    raise ValueError(f'Unknown Jack emission stage "{after}".')


def _copy_c_runtime_files(output_dir: Path) -> None:
    runtime_dir = Path(__file__).resolve().parent / 'c_runtime'
    for path in sorted(runtime_dir.iterdir()):
        if path.is_file():
            (output_dir / path.name).write_text(path.read_text())


def parse_stub_overrides(values: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values:
        if '=' not in value:
            raise ValueError(f'Expected stub override MODULE=REPLACEMENT, got "{value}".')
        original, replacement = value.split('=', 1)
        if not original or not replacement:
            raise ValueError(f'Expected stub override MODULE=REPLACEMENT, got "{value}".')
        overrides[original] = replacement
    return overrides


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='jack')
    parser.add_argument(
        '--module-root',
        action='append',
        default=[],
        metavar='DIR',
        type=Path,
        help='add DIR as an import search root',
    )
    parser.add_argument(
        '--stub',
        action='append',
        default=[],
        metavar='MODULE=REPLACEMENT',
        help='replace imported MODULE with REPLACEMENT while resolving imports',
    )
    parser.add_argument(
        '--allow-comptime-io',
        action='store_true',
        help='deprecated no-op; comptime IO is currently enabled by default',
    )
    parser.add_argument(
        '-o',
        '--output',
        metavar='DIR',
        type=Path,
        help='write split C output to DIR when used with -c',
    )
    parser.add_argument(
        '--after',
        choices=['parse', 'comptime'],
        default=None,
        help='choose the AST stage printed by --emit-jack',
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        '-i',
        '--interpret',
        metavar='FILE',
        type=Path,
        help='run FILE with the interpreter',
    )
    mode.add_argument(
        '-c',
        '--emit-c',
        metavar='FILE',
        type=Path,
        help='emit C for FILE',
    )
    mode.add_argument(
        '--emit-jack',
        metavar='FILE',
        type=Path,
        help='emit Jack source for FILE after a selected compiler stage',
    )
    return parser


def _diagnostic_source_path(args: argparse.Namespace) -> Path | None:
    if args.interpret is not None:
        return args.interpret
    if args.emit_c is not None:
        return args.emit_c
    if args.emit_jack is not None:
        return args.emit_jack
    return None


def _format_error(err: Exception, source_path: Path | None = None) -> str:
    span = getattr(err, 'span', None)
    if span is None or source_path is None:
        return f'jack: {err}'
    try:
        source = source_path.read_text()
    except OSError:
        return f'jack: {source_path}:{span.start_line}:{span.start_column}: {err}'
    return _format_spanned_error(str(err), source_path, source, span)


def _format_spanned_error(
    message: str, source_path: Path, source: str, span: SourceSpan
) -> str:
    header = f'jack: {source_path}:{span.start_line}:{span.start_column}: {message}'
    lines = source.splitlines()
    if span.start_line < 1 or span.start_line > len(lines):
        return header
    source_line = lines[span.start_line - 1]
    start = max(span.start_column - 1, 0)
    if span.end_line == span.start_line:
        width = max(span.end_column - span.start_column, 1)
    else:
        width = 1
    return '\n'.join([
        header,
        source_line,
        f'{" " * start}{"^" * width}',
    ])


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.output is not None and args.emit_c is None:
            parser.error('--output can only be used with -c/--emit-c')
        if args.after is not None and args.emit_jack is None:
            raise ValueError('--after can only be used with --emit-jack')
        import_overrides = parse_stub_overrides(args.stub)
        module_roots = args.module_root or None
        source_path = _diagnostic_source_path(args)
        if args.interpret is not None:
            run_interpreter(
                args.interpret,
                import_overrides=import_overrides,
                module_roots=module_roots,
            )
        elif args.emit_c is not None:
            run_c_emitter(
                args.emit_c,
                import_overrides=import_overrides,
                module_roots=module_roots,
                output_dir=args.output,
            )
        else:
            run_jack_emitter(
                args.emit_jack,
                args.after or 'comptime',
                import_overrides=import_overrides,
                module_roots=module_roots,
            )
    except OSError as err:
        print(f'jack: {err}', file=sys.stderr)
        return 1
    except (
        ValueError,
        ParseError,
        CompileTimeError,
        SemanticError,
        CleanupLoweringError,
        JackEmitError,
        CEmitError,
        InterpreterError,
        ModuleLoadError,
    ) as err:
        print(_format_error(err, locals().get('source_path')), file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
