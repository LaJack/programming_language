# Cold Comptime Lexer Generation

This benchmark measures a cold, uncached build of the generated bootstrap
lexer. It reports module loading, ordinary Jack comptime execution, HIR
lowering, NFA/DFA dimensions, and the frozen dense-table size.

Run it from the repository root:

```sh
.venv/bin/python benchmarks/comptime_lexer/run.py
```

Add `--profile` to write a cProfile report under the gitignored
`benchmarks/results/comptime-lexer` directory and report Python call count plus
generic `deepcopy` time. Profiling substantially increases wall-clock time.

Before packed state sets and type-directed cloning, the local cold baseline was
60-65 seconds and roughly 494 million Python calls. The optimized implementation
keeps dense 256-way runtime tables, performs no persistent caching, and uses no
lexer-specific compiler intrinsic. Wall-clock results are measurements, not CI
assertions.
