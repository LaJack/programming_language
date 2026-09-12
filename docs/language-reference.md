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
Source spans retain their canonical module path through this pipeline. Native
builds requested with `-g` emit function and statement locations for Jack
sources through LLVM debug metadata or C `#line` directives.

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
Widget widget(value);
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

Fixed arrays are copied when their element type is copyable. Slices are borrows,
so parameters that accept shared array storage use a borrow slice type:

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

Calls use uniform value syntax. The parameter type determines which temporary
borrow is created:

```jack
copy(source[..], destination[..]);
```

The borrow checker rejects overlapping live accesses when at least one access
may write. Disjoint struct fields may be borrowed independently.

Borrowed values can be returned only when their origin is allowed to escape,
such as a borrowed parameter, `self`, a global, or a borrow returned by another
checked function.

## Ownership And Moves

Every parameter has a fixed ownership contract. A plain value parameter copies,
a `move` parameter consumes, and a borrow parameter aliases for the duration of
the call:

```jack
void inspect(File file) { }
void consume(move File file) { }
void update(&inout File file) { }

inspect(copyable_file);
consume(owned_file);
update(other_file);
```

Plain parameters are rejected when their type does not implement `Copyable`.
The compiler synthesizes `Copyable` for primitives, `&in` borrows, and
structs or arrays whose stored values are copyable. It is not synthesized for
slices, mutable borrows, opaque extern values, or structs with `deinit`.
Resource structs can provide an explicit deep-copy implementation. Generic
copy contracts must state `T: Copyable`; specialization never changes a copy
contract into a move.

Use `move` when transferring a whole local or owned parameter outside a call:

```jack
File second = move first;
```

The source cannot be read, borrowed, destroyed, or moved again until assignment
reinitializes it. Moving globals, fields, indexes, dereferences, and borrowed
values is not supported. Returning an owned local transfers it automatically;
no `move` marker is written on `return`.

Named borrows live until their lexical scope exits. Implicit call borrows live
only for the complete call. Owned values are destroyed exactly once in reverse
declaration order, including during error propagation. Aggregate fields are
destroyed in reverse field order after the aggregate's own `deinit` method.

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

## Interfaces And Constraints

Interfaces are compile-time contracts with an implicit implementing type named
`Self`. They do not create runtime interface values or use dynamic dispatch:

```jack
pub interface Serializable {
    usize size(&in self);
    void write(&in self, &out u8[] destination);
}
```

Implementations live outside struct declarations. A block may define an
interface-scoped method or forward an exactly matching inherent method with
`use`:

```jack
Message implements Serializable {
    use size;

    void write(&in self, &out u8[] destination) {
        // implementation
    }
}
```

An implementation must be declared by the module owning either the type or the
interface. Every requirement must appear exactly once, and signatures must
match after substituting `Self`, including ownership modes and `raises`.

Type parameters list all required interfaces inline:

```jack
void save(comptime type T: Copyable + Serializable, &in T value) {
    value.write(destination);
}
```

`Copyable` is compiler-known and has the contract
`init(&out self, &in Self other)`. An explicit implementation overrides the
synthesized structural copy and is used by initialization, assignment, struct
field copying, and plain parameter passing. Moves and returns remain ownership
transfers and do not call `Copyable.init`.

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
comptime fill(buffer[..]);

u8 first = buffer[0];
```

### Computed Union Types

A comptime type binding can materialize a nominal union from metadata assembled
by ordinary Jack code:

```jack
comptime str[3] names;
comptime names[0] = "idle";
comptime names[1] = "running";
comptime names[2] = "failed";
pub comptime type State = Union(names[..]);
```

`Union` also accepts `UnionVariant` descriptors. `variant`, `field`, and
`move_field` describe payloads using the same copy and move contracts as a
source-declared union. The generated type is nominally owned by its binding;
assigning an existing type to a comptime type binding creates an alias instead.
Bindings are immutable, become visible in source order, and generated variants
cannot be renamed independently.

Ordinary `Vector`, `String`, allocator, ownership, and error behavior are
available while constructing metadata. `Vector.as_slice()` provides the
immutable slice accepted by `Union`. Comptime filesystem access is read-only;
successfully opened files become compiler dependencies. Creation, append,
read-write access, and output streams are rejected during comptime evaluation.
Live editor analysis defers host IO until a saved or explicit full analysis.

### Immutable Runtime Constants

`const` materializes a comptime result as an immutable runtime global:

```jack
pub const u16[4] transition = comptime build_transition();
```

The declaration requires an explicit fixed-layout type and a `comptime`
initializer. Numeric and boolean primitives, raw bytes, fixed arrays, structs,
and unions composed from those values may be materialized. Strings, slices,
borrows, pointers, allocator tokens, metadata handles, and resource-owning
values may not escape comptime storage. Constants may be read or borrowed with
`&in`, but cannot be assigned, moved, or mutably borrowed. Native backends emit
them in read-only storage.

## Tagged Unions And Matching

Unions are nominal and tagged. Variants are declared in source order and may
be fieldless or carry owned payload parameters:

```jack
union Option(comptime type T) {
    none;
    some(move T value);
}
```

Fieldless variants are values (`Option(i32).none`), while payload variants are
constructed with calls (`Option(i32).some(42)`). Plain payload parameters copy;
`move` payload parameters consume. Payload borrows, custom `init`/`deinit`, and
recursive by-value layouts are rejected.

Matching an owned place always declares its ownership operation:

```jack
match (&in value) { .some(item) { inspect(item); } .none { } }
match (&inout value) { .some(item) { update(item); } .none { } }
i32 result = match (move value) {
    .some(item) => item,
    .none => 0,
};
```

Borrowed matches bind payload borrows with the same mode. Consuming matches
transfer named payloads into branch-local owners and destroy ignored payloads
at branch exit. Arms must be exhaustive; `_` is permitted only once as the
final catch-all. Statement arms use blocks, expression arms use `=>` and must
all produce exactly the same type.

Fieldless unions support `==` and `!=`. Payload unions must be inspected with
`match`. `std.option` provides generic `Option(T)` with `none`, `some`,
`is_some`, and `is_none`.

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

### Lexical Blocks

Standalone braces introduce an unconditional lexical scope:

```jack
i32 value = 1;
{
    &in i32 borrowed = &in value;
    print(borrowed);
}
value = 2;
```

Blocks may be empty or nested and do not take a trailing semicolon. They are
statements, not expressions. Locals and named borrows end at the closing brace;
remaining owned locals are destroyed in reverse declaration order, including
on return and error propagation. Changes to outer variables and ownership state
remain effective afterward. Blocks inherit, but do not grant, unsafe access.
Module-level declarations remain module-level; a block cannot export declarations
or contain imports, local functions, or local nominal types.

`comptime { ... }` executes the complete body during compilation in a child scope,
without repeating `comptime` on each statement. It emits no runtime statements:

```jack
comptime i32 total = 0;
comptime {
    i32 increment = 3;
    total = total + increment;
}
print(total);
```

Comptime locals do not escape, runtime values cannot be read during comptime
execution, and a comptime block cannot return from an enclosing runtime function.
Existing comptime IO permissions and dependency recording still apply. Ordinary
top-level blocks belong to legacy runtime programs and cannot coexist with typed
`main`; fully comptime blocks may coexist with typed `main`.

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

`str` is a non-owning immutable UTF-8 view and its length is measured in bytes
as `usize`. `std.string` provides allocator-aware `ByteBuffer`, `StringBuilder`,
and non-copyable owned `String` types. C ABI functions must not assume that
`str` is a NUL-terminated `char *`.

Executable entry modules may declare `i32 main(&in str[] arguments)`. Such a
module cannot also contain runtime top-level statements, and `main` cannot be
extern, comptime, or raising. Legacy top-level programs remain supported.

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

Opaque C types must be used behind explicit borrows or raw pointers. `c_char`
and `c_void` cannot appear as ordinary by-value objects.

## Unsafe Memory

Unsafe operations are explicit at both declaration and use sites:

```jack
unsafe extern "c" ?*inout c_void malloc(usize size);
unsafe extern "c" void free(?*inout c_void pointer);

unsafe void inspect(?*in u8 pointer) {
    if (pointer != null) {
        unsafe {
            u8 first = *pointer;
        }
    }
}
```

`*in T` and `*inout T` are non-null raw pointers. Prefixing either with `?`
makes it nullable. Raw pointers are copyable, do not own their pointee, and do
not extend object or allocation lifetime. `raw(&in value)` and
`raw(&inout value)` preserve the provenance of an existing place. Dereference,
typed `offset`, and `cast(T)` require an unsafe context. Nullable pointers must
first be refined by a `null` comparison; the refinement is lexical to the
proven branch.

Unsafe code disables none of Jack's ownership or borrow checks. Native code has
undefined behavior when a raw pointer is null, dangling, misaligned,
out-of-bounds, or points at storage that does not contain a live compatible
object. The interpreter diagnoses these cases deterministically where its
tracked provenance can prove them.

Storage allocation, object initialization, ownership, borrowing, destruction,
and storage deallocation are separate operations. A consuming destructor is
declared as `deinit(move self)`. It may move fields from `self`; after its body,
the compiler destroys each field that remains initialized in reverse
declaration order. Callers invoke the destructor exactly once and do not
recursively destroy its fields again.

Partial moves are tracked for fields and constant fixed-array indexes. They are
allowed only through owned locals and parameters. Globals, borrows, dynamic
indexes, and ordinary values whose type declares `deinit` cannot be partially
moved. The exception is a type's own consuming destructor. Reassignment starts
a new object lifetime for a moved place.

`MaybeUninit(T)` has exactly `T`'s layout but does not begin an object lifetime
and never destroys its contents. Its unsafe `write`, `take`, `borrow`, and
`borrow_mut` operations are the only way to assert or change slot
initialization. The interpreter checks those transitions; invalid native use is
undefined behavior.

`Allocation` is an opaque, non-copyable token. `Allocator.allocate` grants at
least a requested `Layout`, `Allocation.layout()` reports the actual grant, and
the allocator resolves temporary raw addresses relative to itself. Containers
must not cache those addresses. Read-only resolution uses `address(&in self)`;
writable resolution uses `address_mut(&inout self)`. `StaticAllocator(T, N)` owns inline
`MaybeUninit(T)[N]` storage and permits one live allocation; its first compatible
request grants all `N` slots. `SystemAllocator` uses host `malloc` and `free`.

`Vector(T, A: Allocator)` owns its allocator and allocation token. `push` is
constant time while capacity remains. When full it requests twice the current
capacity (or one from zero), then moves elements in index order only after the
replacement allocation succeeds. Heap growth is therefore linear; a bounded
static allocator rejects the growth attempt with `CapacityError`. A failed
`push` leaves the vector unchanged and consumes and destroys its moved argument
during error propagation. Capacity doubling is checked before arithmetic and
raises `LayoutError` on overflow. `get` borrows the vector through `&in self`,
while `get_mut` requires `&inout self`. `pop`, `clear`, and destruction remove
elements in reverse index order.

`Arena(T, A: Allocator)` is a monotonic typed arena built on `Vector`. Insertion
returns an opaque, copyable `ArenaHandle(T)` containing a stable insertion
index. Growth may relocate values, but handles remain valid because they are
resolved through the arena on every access. `get` and `get_mut` report
`ArenaHandleError` for out-of-range handles. Values cannot be removed or the
arena cleared; destruction releases all values in reverse insertion order.

A handle is valid only with the arena instance that created it and while that
arena remains alive. This first implementation does not encode an arena
identity, so using an in-range handle with another arena of the same element
type is a logic error that cannot be detected. Lexical borrows returned by
`get` and `get_mut` prevent insertion while they remain live.

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

- no interface inheritance, associated types, default methods, interface
  objects, or dynamic dispatch;
- allocator borrowing, shrinking containers, insertion and removal in the
  middle, pinning, and stable allocator ABI are not implemented;
- no user-facing lifetime syntax;
- partial moves are limited to fields and constant fixed-array indexes, and
  non-lexical borrow lifetimes are not supported;
- C emission is useful but not yet a stable ABI contract;
- the language reference follows the implementation and may change quickly.
