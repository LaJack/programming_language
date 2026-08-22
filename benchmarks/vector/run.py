#!/usr/bin/env python3

from pathlib import Path
import shutil
import statistics
import subprocess
import time


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


def build() -> tuple[Path, Path]:
    RESULTS.mkdir(parents=True, exist_ok=True)
    jack = command_path(ROOT / '.venv' / 'bin' / 'jack', 'jack')
    clangxx = command_path(Path('/nonexistent'), 'clang++')
    jack_output = RESULTS / 'jack-vector'
    cpp_output = RESULTS / 'cpp-vector'
    subprocess.run(
        [
            jack,
            '--backend',
            'llvm',
            '-O2',
            str(Path(__file__).with_name('vector.jack')),
            '-o',
            str(jack_output),
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
    return jack_output, cpp_output


def measure(executable: Path) -> list[float]:
    subprocess.run([executable], stdout=subprocess.DEVNULL, check=True)
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        subprocess.run([executable], stdout=subprocess.DEVNULL, check=True)
        samples.append(time.perf_counter() - started)
    return samples


def main() -> None:
    jack, cpp = build()
    for name, executable in [('Jack LLVM', jack), ('C++', cpp)]:
        samples = measure(executable)
        rendered = ', '.join(f'{sample:.3f}s' for sample in samples)
        print(f'{name}: {rendered}; median {statistics.median(samples):.3f}s')


if __name__ == '__main__':
    main()
