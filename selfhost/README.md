# Jack Self-Hosting Sources

This directory contains compiler components written in Jack. The Python
compiler remains the stage-0 bootstrap compiler while these modules are brought
up and checked against it.

`bootstrap.lexer` is the first differential component. It tokenizes a borrowed
byte slice into `Vector(Token, A)` and records byte offsets plus line and column
provenance in `bootstrap.source.SourceSpan`. The allocator is supplied by the
caller, and the lexer propagates capacity, layout, and allocation failures.
Its `TokenKind` union is generated at compile time from
`bootstrap/jack.tokens`: ordinary Jack file IO, strings, and a metadata vector
produce the nominal union while token recognition remains handwritten.

`bootstrap.main` is a separately compiled executable. It reads a UTF-8 Jack
source file, prints the numeric token stream to stdout, and writes deterministic
errors to stderr. Build and run it from the repository root with:

```sh
jack --backend llvm -O2 selfhost/bootstrap/main.jack --module-root selfhost -o jack-bootstrap
./jack-bootstrap examples/vector.jack
```

The token dump remains numeric for stable differential testing, lexemes remain
in the source buffer, and source positions are byte-oriented. Parsing and richer
bootstrap diagnostics are later milestones.
