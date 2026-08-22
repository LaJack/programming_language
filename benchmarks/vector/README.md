# Vector Growth Benchmark

This benchmark appends 20 million `i32` values without reserving capacity. The
Jack and C++ implementations therefore exercise their automatic geometric
growth paths.

Run it from the repository root:

```sh
python3 benchmarks/vector/run.py
```

The runner builds two Jack variants at `-O2`: the explicit result-envelope
baseline with ordinary LLVM `alwaysinline`, and the effect-aware HIR inliner.
It also builds C++ with `clang++ -O2`. After one warm-up it reports five
wall-clock samples, their median, and generated LLVM call, branch, and envelope
check counts. Generated artifacts stay in the gitignored
`benchmarks/results/vector` directory.
