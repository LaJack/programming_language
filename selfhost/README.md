# Jack Self-Hosting Sources

This directory contains compiler components written in Jack. The Python
compiler remains the stage-0 bootstrap compiler while these modules are brought
up and checked against it.

`bootstrap.lexer` is the first differential component. It tokenizes a borrowed
byte slice into `Vector(Token, A)` and records byte offsets plus line and column
provenance in `bootstrap.source.SourceSpan`. The allocator is supplied by the
caller, and the lexer propagates capacity, layout, and allocation failures.
Its token kinds and scanner tables are generated at compile time from
`bootstrap/jack.tokens`. Ordinary Jack code reads and parses the specification,
constructs a Thompson NFA, determinizes it, and freezes dense immutable DFA
tables. The runtime scanner uses maximal munch and declaration order to break
equal-length ties. String and comment rules dispatch to small handwritten
stateful handlers.

`bootstrap.main` is a separately compiled executable. It reads a UTF-8 Jack
source file, prints the numeric token stream to stdout, and writes deterministic
diagnostics to stderr. Build and run it from the repository root with:

```sh
jack --backend llvm -O2 selfhost/bootstrap/main.jack --module-root selfhost -o jack-bootstrap
./jack-bootstrap examples/vector.jack
```

Human diagnostics are the default. Differential tools can request the stable,
escaped record format without changing token stdout:

```sh
./jack-bootstrap --diagnostic-format stable examples/vector.jack
```

`bootstrap.source.SourceMap` owns source paths, UTF-8 contents, and line-start
tables. Its opaque IDs remain stable while the map is alive, including across
internal growth and movement. Spans and displayed positions are byte-oriented;
tabs expand to four spaces only while rendering excerpts.

`bootstrap.diagnostics.DiagnosticBag` owns diagnostics, labels, and notes. It
preserves insertion order, deduplicates matching code/source/offset tuples, and
reports entries omitted by its configured cap. Labels may refer to different
source files. Dotted lowercase diagnostic codes are stable tooling identifiers;
human wording is not part of the compatibility contract. The stable renderer
emits indexed `diagnostic`, `label`, `note`, and `omitted` tab-separated records
and escapes backslash, tab, carriage return, and newline in all text fields.

The token dump remains numeric for stable differential testing and lexemes
remain in the source buffer. Comptime table construction uses ordinary Jack
code and word-based DFA state sets. Parser recovery will consume the diagnostic
bag in the next frontend milestone.
