# jack-lang

Experimental programming language toolkit.

## CLI

```sh
jack [-i] [-o OUTPUT] [--emit-llvm] SOURCE [SOURCE ...]
```

By default the CLI compiles sources to a native executable named `a.out`.
Passing `-i` interprets the source instead, and `--emit-llvm` writes LLVM IR to
stdout.
