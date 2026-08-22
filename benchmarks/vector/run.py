#!/usr/bin/env python3

import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver
from jack.llvm_lowering_pass import LLVMLoweringPass


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'benchmarks' / 'results' / 'vector'
RUNS = 5


def command_path(local: Path, fallback: str) -> str:
    if local.exists():
        return str(local)
    resolved = shutil.which(fallback)
    if resolved is None:
        raise SystemExit(f'Required command not found: {fallback}')
    return resolved


def build() -> tuple[tuple[str, Path, Path | None], ...]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    jack = command_path(ROOT / '.venv' / 'bin' / 'jack', 'jack')
    clangxx = command_path(Path('/nonexistent'), 'clang++')
    jack_output = RESULTS / 'jack-vector'
    baseline_output = RESULTS / 'jack-vector-envelope'
    cpp_output = RESULTS / 'cpp-vector'
    temps = RESULTS / 'effect-ir'
    if temps.exists():
        shutil.rmtree(temps)
    subprocess.run(
        [
            jack,
            '--backend',
            'llvm',
            '-O2',
            str(Path(__file__).with_name('vector.jack')),
            '-o',
            str(jack_output),
            '--save-temps',
            str(temps),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            clangxx,
            '-O2',
            '-std=c++20',
            str(Path(__file__).with_name('vector.cpp')),
            '-o',
            str(cpp_output),
        ],
        check=True,
    )

    program = CompilerDriver(print_handler=None).backend_hir(
        Path(__file__).with_name('vector.jack'),
        CompilationOptions(optimization=2),
    )
    baseline_ir = LLVMLoweringPass(
        optimization=2, effect_inlining=False
    ).lower(program).render()
    baseline_module = RESULTS / 'baseline.ll'
    baseline_module.write_text(baseline_ir)
    runtime = ROOT / 'jack' / 'c_runtime'
    subprocess.run(
        [
            'clang',
            '-O2',
            '-Wno-override-module',
            str(baseline_module),
            str(runtime / 'jack_std_io.c'),
            '-I',
            str(runtime),
            '-o',
            str(baseline_output),
        ],
        check=True,
    )
    return (
        ('Jack envelope baseline', baseline_output, baseline_module),
        ('Jack effect-aware', jack_output, temps / 'main.ll'),
        ('C++', cpp_output, None),
    )


def measure(executable: Path) -> list[float]:
    subprocess.run([executable], stdout=subprocess.DEVNULL, check=True)
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        subprocess.run([executable], stdout=subprocess.DEVNULL, check=True)
        samples.append(time.perf_counter() - started)
    return samples


def ir_counts(path: Path | None) -> str:
    if path is None:
        return ''
    source = path.read_text()
    calls = len(re.findall(r'^\s*(?:%[-\w.]+ = )?call ', source, re.MULTILINE))
    branches = len(re.findall(r'^\s*br ', source, re.MULTILINE))
    envelopes = source.count('call.ok')
    return f'; IR calls {calls}, branches {branches}, envelope checks {envelopes}'


def main() -> None:
    builds = build()
    for name, executable, llvm_ir in builds:
        samples = measure(executable)
        rendered = ', '.join(f'{sample:.3f}s' for sample in samples)
        print(
            f'{name}: {rendered}; median {statistics.median(samples):.3f}s'
            f'{ir_counts(llvm_ir)}'
        )


if __name__ == '__main__':
    main()
