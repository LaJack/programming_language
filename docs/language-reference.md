# Jack Language Reference

This document describes the current Jack language as implemented by the
Python bootstrap compiler. It is intentionally small and descriptive rather
than a complete formal specification.

Jack is an experimental systems language centered around explicit compile-time
execution. Code marked `comptime` is evaluated by the compile-time pass and is
removed from the runtime program. The resulting runtime program is lowered to
typed HIR for interpretation and backend processing.

## Compiler Pipeline

The bootstrap compiler uses this pipeline:

```text
source -> module loading -> AST -> comptime -> typed HIR -> runtime consumer
                                                    |-> interpreter
                                                    |-> cleanup -> LLVM -> Clang
                                                    `-> cleanup -> C -> Clang
```

`compile_to_hir` is the canonical source-to-runtime boundary. It consumes all
compile-time constructs, validates the resulting runtime program, and lowers it
to typed HIR. The interpreter executes `HIRProgram` directly.

`CompilerDriver` owns native compilation and delegates cleaned HIR to a selected
backend. The dependency-free LLVM backend emits textual opaque-pointer LLVM IR
and is the default; the C backend remains available with `--backend c`.

`lower_hir_static_cleanups` inserts destructor calls and error-path cleanup in
HIR. The interpreter and both bundled and split-module C emitters then consume
`HIRProgram` directly; they do not use the source AST as a runtime side table.
HIR retains entry-module ownership and ordered module dependencies for backend
partitioning. Fixed-array extents are normalized to integer metadata during HIR
lowering, so source expressions do not remain attached to runtime types.

## Source Files

Jack source files usually use `.jack` or `.jk`.

Whitespace is insignificant outside tokens. Line comments use `//`; block
comments use `/* ... */`.

Statements end with `;` except block statements and declarations whose syntax
already ends with `}`.

```jack
i32 value = 1;
print(value);
```

## Modules

A file may declare a module name at the top:

```jack
module app.main;
```

Imports must appear before normal declarations:

```jack
import std.io;
import geometry as geo;
import protocol.frame.{Frame, Id};
```

Declarations are private by default. Use `pub` to export a top-level function,
struct, view, variable, or extern declaration from a module.

```jack
pub i32 add(i32 left, i32 right) {
    return left + right;
}
```

Imported modules are flattened internally by the bootstrap compiler, but module
metadata is kept so private declarations remain private.

## Declarations

Variable declarations always require an explicit type:

```jack
i32 count = 0;
u8[16] buffer;
```

Constructed variables use direct construction syntax:

```jack
File file("examples/io.txt");
```

This declares and initializes `file`; it does not create a temporary object and
then copy it.

Functions declare their return type first:

```jack
i32 add(i32 left, i32 right) {
    return left + right;
}

void reset() {
    return;
}
```

`void` is only valid as a return type.

## Primitive Types

Built-in primitive types are explicit about size:

- signed integers: `i64`, `i32`, `i16`, `i8`
- unsigned integers: `usize`, `u64`, `u32`, `u16`, `u8`
- endian-explicit signed integers: `be_i32`, `le_i32`
- raw byte values: `b64`, `b32`, `b16`, `b8`
- floats: `f64`, `f32`
- booleans: `bool`
- strings: `str`
- compile-time type values: `type`

Boolean literals are `true` and `false`.

Raw byte types are storage/transport values. They can be assigned, compared,
printed, and converted explicitly, but integer arithmetic on raw byte types is
rejected.

Conversions use type-call syntax:

```jack
u8 byte = u8(255);
i64 wide = i64(byte);
f32 ratio = f32(3);
b32 raw = b32(be_i32(45));
```

Integer conversions are range-checked. Converting endian-explicit integers to
raw bytes exposes their byte order.

## Arrays, Slices, And `len`

Fixed arrays are written with a size:

```jack
u8[16] buffer;
buffer[0] = 42;
```

Slices use an empty size:

```jack
&inout u8[] window = &inout buffer[4..8];
```

Array and slice values are not implicitly copied. Parameters that accept arrays
must use an explicit borrow or slice type:

```jack
void fill(&out u8[] dst) {
    dst[0] = 10;
}
```

`len(value)` returns the length of an array or slice.

## Borrows

Borrows are explicit:

- `&in T` is readable.
- `&out T` is write-only.
- `&inout T` is readable and writable.

```jack
void copy(&in u8[] src, &out u8[] dst) {
    dst[0] = src[0];
}
```

Borrow expressions use the same modes:

```jack
copy(&in source[..], &out destination[..]);
```

The borrow checker rejects overlapping live accesses when at least one access
may write. Disjoint struct fields may be borrowed independently.

Borrowed values can be returned only when their origin is allowed to escape,
such as a borrowed parameter, `self`, a global, or a borrow returned by another
checked function.

## Views

Views describe partial borrow interfaces over a struct. A view is not a value
type; it must be used behind an explicit `&inout` borrow. Individual view fields
carry their own access modes.

```jack
view PacketChecksumView {
    in i32 header;
    out i32 checksum;
}

void refresh(&inout PacketChecksumView packet) {
    i32 header = packet.header;
    packet.checksum = header + 1;
}
```

Views allow independent borrowing of disjoint field sets.

## Structs And Methods

Struct fields are declared inside `struct` definitions:

```jack
struct Counter {
    i32 value;
}
```

Methods are declared inside a struct. Every method must declare an explicit
`self` borrow as its first parameter:

```jack
struct Counter {
    i32 value;

    init(&inout self, i32 value) {
        self.value = value;
    }

    i32 get(&in self) {
        return self.value;
    }

    void add(&inout self, i32 delta) {
        self.value = self.value + delta;
    }
}
```

`&in self`, `&out self`, and `&inout self` are shorthand for borrowing the
owning struct type. A method may also declare `self` as a compatible view:

```jack
void refresh(&inout PacketChecksumView self) {
    self.checksum = self.header + 1;
}
```

Constructors are named `init` and return `void` implicitly:

```jack
Counter counter(3);
```

Destructors are named `deinit`. A destructor may only take the explicit `self`
receiver and may not raise errors.

## Generic Structs

Generic structs use explicit `comptime` parameters:

```jack
struct Box(comptime type T, comptime usize N) {
    T[N] storage;
}

Box(i32, 4) box;
```

The compile-time pass specializes generic struct instances into concrete
runtime struct declarations.

Generic functions with `comptime` parameters can also be specialized:

```jack
i32 add_offset(comptime i32 offset, i32 value) {
    return offset + value;
}

i32 y = add_offset(3, 10);
```

Current generic support has no constraints or interfaces. Generic code is
validated after specialization.

## Compile-Time Execution

The `comptime` modifier marks declarations or statements that must run during
the compile-time pass:

```jack
comptime i32 offset;
comptime offset = 2;
comptime offset = offset + 5;

i32 y = offset;
```

No `comptime` statement or variable remains in the runtime AST.

`comptime` values may be used to specialize types, specialize functions,
initialize runtime constants, or perform compile-time IO through registered
host bindings.

Comptime structs and arrays can be manipulated during the compile-time pass.
Plain scalar data may cross from compile time to runtime; opaque host values and
comptime memory resources cannot be materialized as runtime values.

```jack
comptime u8[4] buffer;
comptime fill(&out buffer[..]);

u8 first = buffer[0];
```

## Control Flow

Jack supports `if`, `elif`, and `else`:

```jack
if (value == 0) {
    print(value);
}
elif (value == 1) {
    print(value);
}
else {
    print(value);
}
```

Loops use `while` and C-style `for`:

```jack
while (i < limit) {
    i = i + 1;
}

for (usize i = 0; i < len(buffer); i = i + 1) {
    print(buffer[i]);
}
```

When a conditional or loop is marked `comptime`, it is evaluated and unwound
during the compile-time pass:

```jack
comptime while (i < 4) {
    comptime buffer[i] = i;
    comptime i = i + 1;
}
```

## Errors

Errors are raised as struct values:

```jack
struct AccessError {
    i32 code;
}

void write() raises AccessError {
    raise AccessError { code = 1 };
}
```

A function can declare a concrete error set:

```jack
void run() raises AccessError {
    write();
}
```

Or request inference with a bare `raises` clause:

```jack
void run() raises {
    write();
}
```

The compile-time pass resolves inferred `raises` clauses before runtime
validation.

Errors can be caught with `try`/`catch`:

```jack
try {
    write();
}
catch AccessError err {
    print(err.code);
}
```

Inside a catch block, `rethrow;` raises the caught error again.

Error payloads are currently restricted to concrete struct types without
borrows, slices, `str`, or `deinit`.

## Strings And Formatting

String literals have type `str`:

```jack
str path = "examples/io.txt";
```

Formatted strings use `f"..."` with `{expression}` placeholders:

```jack
print(f"read {count} byte(s)");
```

The current `str` implementation is intentionally small. C ABI functions should
not assume that `str` is a C `char *`; use Jack wrappers such as `std.io` or
explicit byte buffers where needed.

## Externs

External declarations describe symbols provided outside Jack:

```jack
extern void host_write(str text);
comptime extern i32 host_env_i32(str name);

extern "c" type FILE;
extern "c" &inout FILE stdout;
extern "c" usize fwrite(&in c_void data, usize size, usize count, &inout FILE stream);
```

Runtime externs are visible to the interpreter through Python bindings and to
the C emitter as declarations. `comptime extern` declarations are only callable
during the compile-time pass and require host bindings.

Opaque C types must be used behind explicit borrows. `c_char` and `c_void` can
only appear as borrowed types.

## Built-Ins

Built-in functions and forms include:

- `print(expr);`
- `len(array_or_slice)`
- `sizeof(type)`
- `alignof(type)`
- explicit type conversions such as `u8(value)` or `b32(value)`

`sizeof` and `alignof` are compile-time layout queries.

## Current Limitations

The current Python compiler is a bootstrap implementation. Important
limitations include:

- no generic constraints, interfaces, or traits yet;
- no heap allocator model beyond experiments in `std.collections.vector`;
- no user-facing lifetime syntax;
- no full ownership or move system;
- C emission is useful but not yet a stable ABI contract;
- the language reference follows the implementation and may change quickly.
