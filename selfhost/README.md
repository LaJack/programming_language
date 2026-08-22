# Jack Self-Hosting Sources

This directory contains compiler components written in Jack. The Python
compiler remains the stage-0 bootstrap compiler while these modules are brought
up and checked against it.

`bootstrap.lexer` is the first differential component. It tokenizes a borrowed
byte slice into a caller-provided `Token` slice and records byte offsets plus
line and column provenance in `bootstrap.source.SourceSpan`.

The initial API is deliberately fixed-capacity: callers own both source and
token storage. Heap-backed buffers, owned strings, fieldless enums, formatted
string tokenization, and structured lexer errors remain subsequent bootstrap
work.
