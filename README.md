# Jack

Jack is an experimental systems programming language built around explicit
ownership, static dispatch, and compile-time specialization. It is primarily
aimed at embedded and critical software, while the current bootstrap work uses
host IO and dynamic allocation to move toward a compiler written in Jack.

The stage-0 compiler is implemented in Python. It can interpret Jack directly
or emit native executables through LLVM or C and Clang.

```jack
i32 main(&in str[] arguments) {
    print(f"received {len(arguments)} process arguments");
    return 0;
}
```

The language is under active development and does not yet promise a stable
source or binary interface. See the [language reference](docs/language-reference.md)
for the syntax currently implemented.

## Getting Started

Jack requires Python 3.11 or newer. Native builds require Clang 18 or a
compatible newer release.

```bash
python3 -m pip install -e .
mkdir -p build
jack examples/built_in.jack -o build/built-in
./build/built-in
```

LLVM is the default native backend. Other useful modes are:

```bash
jack -i source.jack                     # interpret
jack -O2 source.jack -o build/program   # optimized native build
jack -g source.jack -o build/program    # Jack source debug information
jack --backend c source.jack            # build through generated C
jack -c source.jack                     # print generated C
jack --save-temps build/ir source.jack  # preserve backend artifacts
```

Jack source files conventionally use `.jack`; `.jk` is also accepted.

## Language Tour

### Compile-Time Specialization

`comptime` parameters form part of a declaration's specialization. Their
values are removed before runtime HIR is produced:

```jack
i32 scale(comptime i32 factor, i32 value) {
    return factor * value;
}

i32 doubled = scale(2, 21);
i32 tripled = scale(3, 14);
```

Generic types use the same mechanism. Interface constraints make required
operations explicit rather than discovering them during specialization:

```jack
struct Pair(comptime type T: Copyable) {
    T first;
    T second;
}
```

Compile-time code may also construct a nominal union from metadata. The
self-hosted lexer reads `selfhost/bootstrap/jack.tokens`, collects its names in
an ordinary `Vector`, and binds the result as its concrete `TokenKind` type.
The same Jack code compiles byte-regex rules into immutable DFA tables used by
the runtime scanner. Comptime file access is read-only and tracked as a build
dependency.

### Ownership And Borrows

Function signatures completely describe argument behavior. Plain parameters
copy, `move` parameters consume, and borrow parameters alias temporarily. Call
sites use ordinary call syntax in every case:

```jack
void inspect(&in Message message) { }
void update(&inout Message message) { }
void enqueue(move Message message) { }

inspect(message);
update(message);
enqueue(message); // message is moved
```

Returning an owned value transfers it automatically. Explicit `move` is used
when transferring between local owners:

```jack
Message pending = move message;
```

Destructors consume their object with `deinit(move self)`. They are intended
for actual owned resources; ordinary data structs need no destructor. The
compiler tracks moved and partially moved places and inserts cleanup on normal
and error exits.

### Tagged Unions

Nominal tagged unions can carry owned payloads. Matching an owned place states
whether it is borrowed or consumed:

```jack
import std.option;

Option(i32) answer = Option(i32).some(42);

i32 value = match (move answer) {
    .some(item) => item,
    .none => 0,
};
```

Matches are exhaustive. `&in` and `&inout` matches expose borrowed payloads;
`move` matches transfer payload ownership into the selected arm.

### Errors

Errors are typed values declared in a function's `raises` clause. They use
ordinary structured control flow rather than implicit global error state:

```jack
struct BoundsError { }

i32 read_index(usize index) raises BoundsError {
    if (index >= 4) {
        raise BoundsError { };
    }
    return i32(index);
}

try {
    print(read_index(5));
}
catch BoundsError {
    print("index out of bounds");
}
```

The LLVM backend uses an explicit result ABI at function boundaries and
effect-aware inlining to remove that envelope from eligible optimized calls.

### Allocation And Collections

Allocation policy is a type parameter. The same `Vector` automatically grows
with a system allocator or reports `CapacityError` when bounded static storage
cannot satisfy growth:

```jack
import std.collections.vector;
import std.memory;

void collect() raises CapacityError, LayoutError, AllocationError {
    StaticAllocator(i32, 8) storage;
    Vector(i32, StaticAllocator(i32, 8)) values(storage, 4);

    values.push(10);
    values.push(20);
    print(values.len());
}
```

`MaybeUninit(T)`, `Allocation`, raw pointers, and `unsafe` blocks provide the
low-level foundation. Safe collections do not retain pointers into movable
allocators. `Arena(T, A)` adds stable typed index handles over monotonic
storage.

### IO And Strings

`str` is a non-owning immutable UTF-8 view. `std.string` provides allocator-
aware owned strings and byte buffers. `std.io.File` is a non-copyable owned
resource with typed failures:

```jack
import std.io;

void read_header() raises IoError {
    File file = open_read("examples/io.txt");
    u8[8] buffer;
    usize count = file.read(buffer[..]);
    close(file);

    print(count);
}
```

EOF is represented by a successful zero-byte read. Explicit `close` reports
errors; automatic destruction performs best-effort closure.

## Modules

A source file may declare a module and import public declarations from other
modules:

```jack
module app.main;

import math.ops;
import geometry as geo;
import protocol.frame.{Frame, Id};

i32 total = add(2, 3);
geo.Point origin;
Frame frame;
```

Module paths map to files such as `protocol/frame.jack`. Imports may be bare,
aliased, or selective. Declarations are private unless marked `pub`.
Additional search roots and test-time module replacements are available from
the CLI:

```bash
jack --module-root libraries source.jack
jack --stub hw.spi=tests.stubs.spi -i source.jack
```

## Editor And Debugger

The extension in `vscode-jack/` provides:

- project-aware diagnostics and recovering syntax analysis;
- semantic highlighting, hover, completion, and signature help;
- cross-file definitions, references, rename, and safe code actions;
- Linux x86-64 source debugging through CodeLLDB.

Press `F5` in a Jack file to compile it with LLVM at `-g -O0` and start a debug
session. Breakpoints, Jack statement stepping, stack frames, parameters, and
source-visible locals are supported.

## Self-Hosting

`selfhost/bootstrap/` contains the growing Jack frontend. Its lexer reads real
files through `std.io`, stores tokens dynamically, and uses a tagged
`TokenKind` union while preserving deterministic source spans. Its recovering
parser builds typed, arena-backed syntax trees owned by a multi-file frontend
context. Structural differential tests compare these trees with the Python
parser. The self-hosted project pass loads imports, indexes declarations,
builds lexical scopes, and resolves source names without executing comptime
code. Independent tests compare its bindings with stage 0 and account for
identifiers throughout the complete bootstrap graph. Type checking,
specialization, and ownership validation remain in stage 0.
See the
[self-hosting documentation](selfhost/README.md) for APIs, syntax dumps, and
measurements. The Python compiler remains stage 0.

## Development

Run the Python test suite from the repository root:

```bash
python3 -m unittest discover -s tests
```

Extension tests live separately:

```bash
cd vscode-jack
npm test
```

The checked-in programs under `examples/` are exercised across the interpreter,
C, and LLVM implementations as part of the conformance suite.
