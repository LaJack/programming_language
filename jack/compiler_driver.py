from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, Sequence

from .c_emit_pass import emit_hir_c_files
from .cleanup_lowering_pass import lower_hir_static_cleanups
from .comptime_externs import default_comptime_externs
from .hir_lowering_pass import compile_to_hir
from .hir_nodes import HIRProgram
from .hir_validation_pass import validate_backend_hir
from .llvm_emit_pass import emit_hir_llvm
from .module_loader import load_source_file


class CompilerDriverError(Exception):
    pass


class BackendNotFoundError(CompilerDriverError):
    pass


class BackendArtifactError(CompilerDriverError):
    pass


class ToolchainError(CompilerDriverError):
    def __init__(
        self,
        message: str,
        *,
        command: Sequence[str] = (),
        stdout: str = '',
        stderr: str = '',
        artifact_paths: Sequence[Path] = (),
    ) -> None:
        super().__init__(message)
        self.command = tuple(command)
        self.stdout = stdout
        self.stderr = stderr
        self.artifact_paths = tuple(artifact_paths)

    def __str__(self) -> str:
        message = super().__str__()
        if self.artifact_paths:
            paths = ', '.join(str(path) for path in self.artifact_paths)
            message += f'\nGenerated inputs: {paths}'
        return message


@dataclass(frozen=True)
class BackendArtifacts:
    files: Mapping[str, str]
    link_inputs: tuple[str, ...]


class CompilerBackend(Protocol):
    name: str

    def emit(self, program: HIRProgram) -> BackendArtifacts:
        ...


@dataclass(frozen=True)
class CompilationOptions:
    backend: str = 'llvm'
    output: Path | None = None
    module_roots: tuple[Path, ...] = ()
    import_overrides: Mapping[str, str] = field(default_factory=dict)
    save_temps: Path | None = None
    clang: str = 'clang'
    optimization: int = 0


@dataclass(frozen=True)
class CompilationResult:
    output_path: Path
    backend: str
    saved_artifacts: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ToolchainResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''


ToolchainRunner = Callable[[Sequence[str]], ToolchainResult]


class CBackend:
    name = 'c'

    def emit(self, program: HIRProgram) -> BackendArtifacts:
        files = dict(emit_hir_c_files(program))
        runtime_dir = Path(__file__).resolve().parent / 'c_runtime'
        for path in sorted(runtime_dir.iterdir()):
            if path.is_file():
                files[path.name] = path.read_text()
        link_inputs = tuple(
            sorted(name for name in files if name.endswith('.c'))
        )
        return BackendArtifacts(files=files, link_inputs=link_inputs)


class LLVMBackend:
    name = 'llvm'

    def emit(self, program: HIRProgram) -> BackendArtifacts:
        files = {'main.ll': emit_hir_llvm(program)}
        runtime_dir = Path(__file__).resolve().parent / 'c_runtime'
        for runtime_name in ('jack_runtime.h', 'jack_std_io.h', 'jack_std_io.c'):
            path = runtime_dir / runtime_name
            files[runtime_name] = path.read_text()
        return BackendArtifacts(
            files=files,
            link_inputs=('main.ll', 'jack_std_io.c'),
        )


class CompilerDriver:
    def __init__(
        self,
        backends: Sequence[CompilerBackend] | None = None,
        *,
        toolchain_runner: ToolchainRunner | None = None,
        print_handler: Callable[[str], None] | None = print,
        comptime_externs: Mapping[str, object] | None = None,
    ) -> None:
        available = backends if backends is not None else (LLVMBackend(), CBackend())
        self.backends = {backend.name: backend for backend in available}
        self.toolchain_runner = toolchain_runner or self._run_toolchain
        self.print_handler = print_handler
        self.comptime_externs = (
            dict(comptime_externs)
            if comptime_externs is not None
            else default_comptime_externs()
        )

    def compile_hir(
        self, entry: Path, options: CompilationOptions | None = None
    ) -> HIRProgram:
        options = options or CompilationOptions()
        ast = load_source_file(
            entry,
            import_overrides=dict(options.import_overrides),
            search_roots=list(options.module_roots) or None,
        )
        return compile_to_hir(
            ast,
            print_handler=self.print_handler,
            externs=self.comptime_externs,
        )

    def backend_hir(
        self, entry: Path, options: CompilationOptions | None = None
    ) -> HIRProgram:
        return validate_backend_hir(
            lower_hir_static_cleanups(self.compile_hir(entry, options))
        )

    def compile_executable(
        self, entry: Path, options: CompilationOptions | None = None
    ) -> CompilationResult:
        options = options or CompilationOptions()
        backend = self.backends.get(options.backend)
        if backend is None:
            choices = ', '.join(sorted(self.backends)) or 'none'
            raise BackendNotFoundError(
                f'Unknown compiler backend "{options.backend}"; available backends: {choices}.'
            )
        if type(options.optimization) is not int or options.optimization not in range(4):
            raise CompilerDriverError('Optimization level must be an integer from 0 to 3.')

        program = self.backend_hir(entry, options)
        artifacts = backend.emit(program)
        self._validate_artifacts(artifacts)
        output = (options.output or (Path.cwd() / entry.stem)).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        if options.save_temps is not None:
            build_dir = options.save_temps.resolve()
            build_dir.mkdir(parents=True, exist_ok=True)
            saved = self._materialize_artifacts(build_dir, artifacts)
            self._link_atomically(build_dir, artifacts, output, options)
            return CompilationResult(output, backend.name, saved)

        with tempfile.TemporaryDirectory(prefix='jack-build-') as tmpdir:
            build_dir = Path(tmpdir)
            self._materialize_artifacts(build_dir, artifacts)
            self._link_atomically(build_dir, artifacts, output, options)
        return CompilationResult(output, backend.name)

    @staticmethod
    def _validate_artifacts(artifacts: BackendArtifacts) -> None:
        if not artifacts.link_inputs:
            raise BackendArtifactError('Compiler backend emitted no link inputs.')
        normalized: set[str] = set()
        for name, content in artifacts.files.items():
            if not isinstance(name, str):
                raise BackendArtifactError('Compiler backend artifact paths must be text.')
            path = PurePosixPath(name)
            if (
                not name
                or '\\' in name
                or path.is_absolute()
                or '..' in path.parts
                or '.' in path.parts
                or str(path) != name
            ):
                raise BackendArtifactError(
                    f'Compiler backend emitted unsafe artifact path "{name}".'
                )
            if str(path) in normalized:
                raise BackendArtifactError(
                    f'Compiler backend emitted duplicate artifact path "{name}".'
                )
            if not isinstance(content, str):
                raise BackendArtifactError(
                    f'Compiler backend artifact "{name}" is not text.'
                )
            normalized.add(str(path))
        if any(not isinstance(name, str) for name in artifacts.link_inputs):
            raise BackendArtifactError('Compiler backend link input paths must be text.')
        if len(artifacts.link_inputs) != len(set(artifacts.link_inputs)):
            raise BackendArtifactError('Compiler backend emitted duplicate link inputs.')
        for name in artifacts.link_inputs:
            if not isinstance(name, str) or not name or name not in artifacts.files:
                raise BackendArtifactError(
                    f'Compiler backend link input "{name}" is not an emitted file.'
                )
            if not artifacts.files[name]:
                raise BackendArtifactError(
                    f'Compiler backend link input "{name}" is empty.'
                )

    def _materialize_artifacts(
        self, build_dir: Path, artifacts: BackendArtifacts
    ) -> tuple[Path, ...]:
        paths: list[Path] = []
        for name, content in sorted(artifacts.files.items()):
            path = build_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            paths.append(path)
        return tuple(paths)

    def _link_atomically(
        self,
        build_dir: Path,
        artifacts: BackendArtifacts,
        output: Path,
        options: CompilationOptions,
    ) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=output.parent,
            prefix=f'.{output.name}.',
            suffix='.tmp',
        )
        os.close(descriptor)
        temporary_output = Path(temporary_name)
        temporary_output.unlink()
        try:
            self._link(
                build_dir,
                artifacts,
                temporary_output,
                options.clang,
                options.optimization,
            )
            if not temporary_output.is_file():
                raise ToolchainError(
                    'Clang reported success but did not create an executable.',
                    artifact_paths=tuple(build_dir / name for name in artifacts.link_inputs),
                )
            os.replace(temporary_output, output)
        finally:
            temporary_output.unlink(missing_ok=True)

    def _link(
        self,
        build_dir: Path,
        artifacts: BackendArtifacts,
        output: Path,
        clang: str,
        optimization: int,
    ) -> None:
        artifact_paths = tuple(build_dir / name for name in artifacts.link_inputs)
        compiler = shutil.which(clang)
        if compiler is None:
            raise ToolchainError(
                f'Cannot find Clang executable "{clang}".',
                artifact_paths=artifact_paths,
            )
        inputs = [str(build_dir / name) for name in artifacts.link_inputs]
        command = [
            compiler,
            *inputs,
            '-I',
            str(build_dir),
            f'-O{optimization}',
            '-o',
            str(output),
        ]
        try:
            result = self.toolchain_runner(command)
        except ToolchainError as err:
            if not err.artifact_paths:
                err.artifact_paths = artifact_paths
            raise
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            message = 'Clang failed while building the executable.'
            if detail:
                message = f'{message}\n{detail}'
            raise ToolchainError(
                message,
                command=command,
                stdout=result.stdout,
                stderr=result.stderr,
                artifact_paths=artifact_paths,
            )

    @staticmethod
    def _run_toolchain(command: Sequence[str]) -> ToolchainResult:
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as err:
            raise ToolchainError(
                f'Cannot execute Clang: {err}', command=command
            ) from err
        return ToolchainResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


def compile_executable(
    entry: Path, options: CompilationOptions | None = None
) -> CompilationResult:
    return CompilerDriver().compile_executable(entry, options)
