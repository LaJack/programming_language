# Frontend Representation Benchmark

From the repository root, on Linux:

```sh
.venv/bin/python benchmarks/frontend/run.py --runs 5
.venv/bin/python benchmarks/frontend/run.py selfhost/bootstrap/syntax.jack --backend c
```

The executable measures process CPU time immediately around `parse_strict`,
including tree validation and context registration. File IO, source indexing,
runtime lexing, tree dumping, and final context destruction are outside that
interval. Linux `clock()` reports millionths of a second. Very small inputs may
be below its useful measurement resolution.

Cold native build time, including comptime DFA generation, is reported
separately. Use `benchmarks/comptime_lexer/run.py` for its phase breakdown.
No timing is a test assertion.

Reports include arena node count and host-layout node size, retained buffer
capacity in bytes (tokens, nodes, links, roots), and scratch high-water/capacity.
Source-map storage, allocator metadata, and diagnostic storage are not included
in retained syntax bytes. Scratch storage is released after parsing; its peak
is reported separately. Arena capacity includes any unreachable recovery or
lookahead nodes. Results and executables go in ignored `benchmarks/results/`.

## Initial Measurement

An LLVM `-O2` run on the development host, parsing an approximately 86 KB
revision of `selfhost/bootstrap/parser.jack`, reported a five-run median of
20.4 ms (about 4 MiB/s). Nodes occupied 240 bytes, retained syntax capacity was
about 4.7 MiB, and scratch peaked at 61 entries with capacity 64. Compacting
draft links reduced stored links from roughly 9,900 to 4,800; reserved buffer
capacity is still based on token count. This is a local observation, not a
performance guarantee. Typed node payloads take priority over minimum size;
arena reservation and unused lookahead nodes remain possible optimization work.
