# Vector Growth Benchmark

This benchmark appends 20 million `i32` values without reserving capacity. The
Jack and C++ implementations therefore exercise their automatic geometric
growth paths.

Run it from the repository root:

```sh
python3 benchmarks/vector/run.py
```

The runner builds Jack with the LLVM backend at `-O2`, builds C++ with
`clang++ -O2`, performs one warm-up, and reports five wall-clock samples and
their median. Generated executables stay in the gitignored
`benchmarks/results/vector` directory.
