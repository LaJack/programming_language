# Jack

Jack is a small experimental language for exploring explicit `comptime` evaluation before interpretation. The Python implementation is intentionally minimalist and serves as a bootstrap path toward a compiler written in Jack itself.

For a compact description of the current language, see [the language reference](docs/language-reference.md).

Build a native executable with the LLVM backend:

```bash
jack source.jk
./source
```

Choose the output path or the C backend explicitly:

```bash
jack source.jk -o build/source
jack --backend c source.jk -o build/source-c
jack -O2 source.jk -o build/source-optimized
jack -g source.jk -o build/source-debug
```

Run a program with the interpreter instead:

```bash
jack -i source.jk
```

Editor support lives in `vscode-jack/`. It provides VSCode syntax highlighting,
project-aware diagnostics, semantic hover, cross-file definition, completion,
references, and project-wide rename through `python3 -m jack.lsp_server` for
`.jack` and `.jk` files. The semantic index includes open workspaces and
configured module roots, with unsaved editor buffers taking precedence over
disk contents.
On Linux x86-64, the extension also provides CodeLLDB source debugging: press
`F5` in a Jack file to build it with `-g -O0`, stop on Jack breakpoints, step
through statements, and inspect LLVM-backed parameters and locals.

Emit C for inspection:

```bash
jack -c source.jk
```

The emitted C output is a single bundled translation unit by default. For separate compilation of imported Jack modules, write split output to a directory:

```bash
jack -c source.jk -o build/c
```

Split output writes `main.c`, one `.h`/`.c` pair per imported Jack module, and copies the small C runtime files into the output directory. The generated files include `jack_runtime.h`; programs using `std.io` also include and compile `jack_std_io.c`.

Normal native builds keep intermediates in a temporary directory. Preserve the
textual LLVM IR or generated C inputs with `--save-temps DIR`.
Native optimization defaults to `-O0`; `-O1` through `-O3` are passed directly
to Clang for both backends.
Use `-g` to retain Jack function and statement locations in native debug
information. It can be combined with either backend and any optimization level.


Modules can declare their source name and import other files before the compile-time pass runs. Module paths are resolved from the entry file directory and module roots by mapping `foo.bar` to `foo/bar.jack` or `foo/bar.jk`; the current implementation flattens loaded declarations into the program AST, then uses module metadata to enforce import visibility.

```c
module app.main;
import math.ops;
import geometry as geo;
import protocol.frame.{Frame, Id};

i32 y = add(2, 3);
geo.Point point;
Frame frame;
```

Library files mark exported declarations with `pub`. Private declarations remain usable inside their own module, but importers can only access public declarations from modules they directly import. Selective imports expose only the listed public names, and alias imports require qualified use such as `geo.Point` or `ops.add()`. Imported declarations are internally qualified with their module path, so private helpers with the same source name can coexist across modules; public name collisions on bare imports are reported with an explicit diagnostic.

Test builds can replace an imported module at resolution time:

```bash
jack --stub hw.spi=tests.stubs.spi -i tests/can_driver.jack
```


External declarations describe symbols provided outside Jack. The default form declares a Jack ABI function, while `extern "c"` declares C ABI symbols for C emission. Opaque C types and C globals can also be declared this way:

```c
extern void host_write(str text);
comptime extern i32 host_env_i32(str name);

extern "c" type FILE;
extern "c" &inout FILE stdout;
extern "c" usize fwrite(&in c_void data, usize size, usize count, &inout FILE stream);
```

Runtime `extern` declarations are emitted as C prototypes and must be supplied to the interpreter through a Python extern registry. `comptime extern` declarations are host-only for now: they can run during the compile-time pass only when an explicit comptime binding is registered, and they are removed from the runtime AST.

The C ABI surface is intentionally narrow: `usize` maps to C `size_t`, opaque C types such as `FILE` must be used behind explicit borrows, and C helper types such as `c_char` and `c_void` can only appear as borrowed types such as `&in c_char` or `&in c_void`. Jack `str` is still a Jack ABI value, not a C `char *`, so libc-style examples should use byte buffers plus explicit lengths for now.

The interpreter CLI includes small runtime and comptime host bindings for `stdout`, `fwrite`, `fopen`, `fread`, `fclose`, and the `std.io` string-path bridge, so libc-backed byte-buffer examples can run without compiling C.

Comptime IO can currently touch files on the compiling machine without an explicit permission prompt. This is intentionally simple for now; stronger safeguards will be added once the model matures.

The first Jack IO module is `std.io`, shipped with the Python package under `jack/std`. It wraps the libc symbols in a Jack `File` struct and exposes method-based reading over caller-owned buffers. File paths use Jack `str`; the backend bridge converts them to C strings when needed.

```c
import std.io;

File file("examples/io.txt");
usize bytes_read = file.read(buffer[..]);
file.close();
```

Run the example from the repository root with:

```bash
jack -i examples/io_read_file.jack
```


Built-in primitive types are explicit about size:

```c
i64 signed_value = 12;
u8 byte = 255;
b16 raw = 48879;
f32 ratio = 1.5;
bool enabled = true;

print(f"{signed_value} {byte} {raw} {ratio} {enabled}");
```

Signed integers are `i64`, `i32`, `i16`, and `i8`; unsigned integers are `usize`, `u64`, `u32`, `u16`, and `u8`; floats are `f64` and `f32`; booleans are `bool` with `true` and `false` literals. Endian-explicit signed 32-bit integers are `be_i32` and `le_i32`; they are signed numeric values with `i32` range, and their byte order becomes visible when converted to raw bytes. Raw byte values are `b64`, `b32`, `b16`, and `b8`: they can be stored, compared, and printed as fixed-width hex, but integer arithmetic is intentionally rejected.

Built-in value conversions use explicit type-call syntax:

```c
u8 byte = u8(255);
i64 wide = i64(byte);
f32 ratio = f32(3);
b16 raw = b16(48879);
be_i32 big = be_i32(45);
le_i32 little = le_i32(45);
b32 big_raw = b32(big);       // 0x0000002d
b32 little_raw = b32(little); // 0x2d000000
```

Integer conversions are range-checked by the interpreter and compile-time pass. Direct raw assignments such as `b32 raw = 45;` are numeric raw values. Explicit same-width integer-to-raw conversions such as `b32(i32_value)`, `b32(be_i32_value)`, and `b32(le_i32_value)` expose the value's memory byte order. Float-to-integer, bool-to-numeric, and numeric-to-bool conversions are rejected for now.


Generic structs are expressed with explicit `comptime` parameters:

```c
struct Box(comptime type T, comptime i32 N) {
    T value;
}

Box(i32, 4) small;
```

The compile-time pass specializes this to a concrete runtime struct before interpretation.


Struct values can also live entirely at comptime. Their scalar fields may be used by runtime code after the compile-time pass substitutes them:

```c
struct Point {
    i32 x;
    i32 y;
}

comptime Point point;
comptime point.x = 3;
comptime point.y = point.x + 4;

i32 y = point.y;
```

Comptime arrays support indexed assignment, indexing, `len`, and explicit borrows/slices. Mutable comptime borrows alias the original array, so helper functions can fill caller-owned buffers during the compile-time pass:

```c
void fill(&inout u8[] dst) {
    dst[0] = 42;
}

comptime u8[4] buffer;
comptime fill(buffer[..]);

u8 first = buffer[0];
i32 count = len(buffer);
```

Comptime structs may contain opaque host values such as borrowed C handles. Those opaque values can be used during the compile-time pass, but they cannot be translated into runtime values. Plain data derived from them can still cross the phase boundary:

```c
import std.io;

comptime File file("examples/io.txt");
comptime u8[8] buffer;
comptime usize count = file.read(buffer[..]);

usize runtime_count = count; // ok: plain data crosses phases
// File runtime_file = file; // rejected: contains a comptime host handle
```


Methods can be declared inside a type definition. Every method declares its `self` borrow explicitly:

```c
struct Line {
    i32 p1;
    i32 p2;

    i32 sum(&in self) {
        return self.p1 + self.p2;
    }
}

Line line;
i32 total = line.sum();
```


Functions and methods can return `void`; use `return;` for an early bare return:

```c
void reset() {
    return;
}
```


Structs can define an `init` constructor and a `deinit` destructor. `deinit` only takes the explicit `self` receiver. Direct construction uses the variable name, so no temporary copy is implied:

```c
struct CanDriver {
    i32 slave_address;

    init(&inout self, i32 slave_address) {
        self.slave_address = slave_address;
    }

    deinit(move self) {
        print(self.slave_address);
    }
}

CanDriver can(5);
comptime CanDriver comptime_can(7);
```
