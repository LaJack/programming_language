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
code and word-based DFA state sets. The parser consumes the same tokens and
reports recovery through the structured diagnostic bag.

## Compact syntax tree

`bootstrap.parser` targets the current Python parser grammar. The typed syntax
representation in `bootstrap.syntax` covers declarations, types, ownership
contracts, control flow, matches, and expressions, including embedded formatted
string expressions. This is a compiler AST, not a lossless formatting tree or
a semantic model.

`bootstrap.frontend.FrontendContext` owns both the shared `SourceMap` and all
completed `ParsedFile` values. Construct it with `frontend_context()`; standalone
source maps use `source_map()`. These factories allocate checked process-local
identities. Default-zero storage is not an initialized source map.

Each file owns its tokens, one heterogeneous node arena, a flat child-link
table, and roots. `SyntaxData` has node-specific payloads: binary nodes name
their operator, left, and right children; functions name parameters, return
type, raises types, and body. Kinds are derived from payload variants rather
than stored independently. Optional children use explicit absent/present
links. Names and literal spellings remain source-backed; operators and
declaration modifiers have typed values.

The context returns opaque `ParsedFileId` and file-qualified `NodeRef` values.
Public context lookups check their identity and bounds. Moving the context
preserves references; reparsing appends a new file identity and retains old
trees until context destruction. References do not own storage: access always
requires the live owning context. Completed files expose only read operations;
the separate builder API is used during parsing.

The frontend `parse_recovering(context, source, tokens, diagnostics)` entrypoint
returns a `ParsedFileId`, registering a structurally validated partial tree with
category-specific invalid nodes alongside later valid declarations.
`parse_strict` raises `ParseFailed` without registering a file when parsing
encounters errors, even if the diagnostic bag is full or deduplicates them.
Unrelated diagnostics already in the bag do not reject a valid file.
Allocation failures never register a partially built tree.

Recovery preserves owner delimiters and guarantees progress. Recursive parser
entrypoints share a nesting limit of 128; the `_with_options` APIs accept
`ParserOptions(limit)` to change it. Formatted-string subparses use bounded
token streams with absolute byte spans. List construction shares a single
scratch vector with nested checkpoints instead of allocating each list.

Before registration, a structural validator checks source bounds, named child
categories, required children, list ranges, reference validity, and reachable
tree ordering/acyclicity. Parser error state is independent of diagnostic
capacity. Compiler semantic resolution and HIR lowering are deferred.

Request a deterministic syntax dump with:

```sh
./jack-bootstrap --dump syntax examples/vector.jack
```

Syntax output uses preorder IDs independent of arena indexes. `root`, `node`,
`field`, `value`, and `child` records describe source ranges and named edges
such as `left`, `parameters`, and `body`. Child indexes are local to their role.
The default remains `--dump tokens`, preserving existing lexer differential
workflows. Diagnostic and dump options may appear in either order.

## Verification And Measurements

`tests/test_bootstrap_lexer.py` compares independently normalized Python ASTs
and bootstrap dumps for checked-in examples, standard modules, bootstrap
sources, and focused grammar fixtures. It compares structure, names, types,
contracts, modifiers, and operators, with UTF-8 conversion for root ranges and
direct absolute-span assertions for embedded expressions. Parentheses and
stage-0 representation differences are normalized explicitly.

Malformed fixtures and deterministic token deletions check rejection,
retained declarations, bounded diagnostics, and termination. Focused syntax
and recovery cases compare interpreter, C, and LLVM output at `-O0` and native
`-O2`. Frontend API tests exercise context movement, cross-context rejection,
reparsing, strict failure with full diagnostic bags, and injected allocation
failures. These tests establish structural parity on the tested corpus, not
semantic correctness or equivalence for every possible malformed input.

See [the frontend benchmark](../benchmarks/frontend/README.md) for parser-only
throughput, node layout, retained storage, and scratch high-water measurements.
Cold comptime lexer generation is measured separately.
