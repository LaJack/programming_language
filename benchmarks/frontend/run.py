"""Linux frontend CPU-time and retained-storage measurements, without assertions."""

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
    parser.add_argument('source', nargs='?', type=Path)
    parser.add_argument('--mode', choices=('parser', 'project'), default='parser')
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--backend', choices=('c', 'llvm'), default='llvm')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('This benchmark uses Linux clock(), with CLOCKS_PER_SEC = 1000000.')
    if args.runs < 1:
        parser.error('--runs must be positive')
    source = args.source or ROOT / (
        'selfhost/bootstrap/main.jack' if args.mode == 'project'
        else 'selfhost/bootstrap/parser.jack'
    )
    output = ROOT / 'benchmarks/results/frontend'
    output.mkdir(parents=True, exist_ok=True)
    executable = output / f'{args.mode}-{args.backend}'
    started = time.perf_counter()
    CompilerDriver(print_handler=None).compile_executable(
        ROOT / f'benchmarks/frontend/{args.mode}.jack',
        CompilationOptions(backend=args.backend, optimization=2, output=executable,
                           module_roots=(ROOT / 'selfhost',)),
    )
    build_seconds = time.perf_counter() - started
    samples = []
    for _ in range(args.runs):
        result = subprocess.run([executable, source.resolve()], check=True,
                                capture_output=True, text=True, cwd=ROOT, timeout=120)
        samples.append({key: int(value) for key, value in
                        (line.split('\t') for line in result.stdout.splitlines())})
    source_bytes = (samples[0]['source_bytes'] if args.mode == 'project'
                    else source.stat().st_size)
    report = dict(mode=args.mode, backend=args.backend, source=str(source.resolve()),
                  source_bytes=source_bytes, build_seconds=build_seconds, samples=samples)
    if args.mode == 'parser':
        median = statistics.median(sample['ticks'] for sample in samples) / 1_000_000
        report['parser_seconds_median'] = median
        report['parser_mib_per_second'] = (
            source_bytes / (1024 * 1024 * median) if median else None
        )
    else:
        report['phase_seconds_median'] = {
            phase: statistics.median(sample[f'{phase}_ticks'] for sample in samples)
            / 1_000_000
            for phase in ('load', 'index', 'resolve', 'validate')
        }
        report['counts'] = {
            key: samples[0][key] for key in
            ('modules', 'nodes', 'symbols', 'occurrences', 'receiver_type',
             'generic_type', 'computed_type', 'pattern_type', 'comptime_selection',
             'errors')
        }
    rendered = json.dumps(report, indent=2) + '\n'
    report_name = f'{args.backend}.json' if args.mode == 'parser' else f'project-{args.backend}.json'
    (output / report_name).write_text(rendered)
    print(rendered, end='')


if __name__ == '__main__':
    main()
