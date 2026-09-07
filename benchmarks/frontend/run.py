"""Linux parser-only CPU-time and retained-storage measurements, without assertions."""

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from jack.compiler_driver import CompilationOptions, CompilerDriver


ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', type=Path,
                        default=ROOT / 'selfhost/bootstrap/parser.jack')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--backend', choices=('c', 'llvm'), default='llvm')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('This benchmark uses Linux clock(), with CLOCKS_PER_SEC = 1000000.')
    if args.runs < 1:
        parser.error('--runs must be positive')
    output = ROOT / 'benchmarks/results/frontend'
    output.mkdir(parents=True, exist_ok=True)
    executable = output / f'parser-{args.backend}'
    started = time.perf_counter()
    CompilerDriver(print_handler=None).compile_executable(
        ROOT / 'benchmarks/frontend/parser.jack',
        CompilationOptions(backend=args.backend, optimization=2, output=executable,
                           module_roots=(ROOT / 'selfhost',)),
    )
    build_seconds = time.perf_counter() - started
    samples = []
    for _ in range(args.runs):
        result = subprocess.run([executable, args.source.resolve()], check=True,
                                capture_output=True, text=True, cwd=ROOT, timeout=120)
        samples.append({key: int(value) for key, value in
                        (line.split('\t') for line in result.stdout.splitlines())})
    median = statistics.median(sample['ticks'] for sample in samples) / 1_000_000
    source_bytes = args.source.stat().st_size
    report = dict(backend=args.backend, source=str(args.source.resolve()),
                  source_bytes=source_bytes, build_seconds=build_seconds,
                  parser_seconds_median=median,
                  parser_mib_per_second=source_bytes / (1024 * 1024 * median) if median else None,
                  samples=samples)
    rendered = json.dumps(report, indent=2) + '\n'
    (output / f'{args.backend}.json').write_text(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
