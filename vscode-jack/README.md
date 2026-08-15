# Jack VSCode Extension

This extension provides editor basics for the experimental Jack language:

- language registration for `.jack` and `.jk`
- TextMate syntax highlighting
- project-aware parse, comptime, and semantic diagnostics through a Python LSP server
- document symbols for Outline and Breadcrumbs
- semantic hover and cross-file go-to-definition
- lexical and member completion, references, and project-wide rename
- Linux x86-64 source debugging through CodeLLDB
- comments, brackets, indentation, and string pairing rules

## Debugging

Install the extension and open a local Jack file inside a workspace. Press `F5`
to save it, compile it with `jack --backend llvm -g -O0`, and launch it through
CodeLLDB. Breakpoints, statement stepping, Jack stack frames, parameters, and
local values are available in entry and imported source files. Debug executables
are written below `.jack/debug/`.

The zero-configuration workflow uses `jack.compiler.*` for compiler and module
settings and `jack.debug.*` for the debuggee arguments, environment, and working
directory.

For a persistent project build, add this task to `.vscode/tasks.json`:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "jack: build debug app",
      "type": "jack",
      "entry": "${workspaceFolder}/src/main.jack",
      "output": "${workspaceFolder}/.jack/debug/app",
      "backend": "llvm",
      "optimization": 0,
      "moduleRoots": ["${workspaceFolder}/modules"],
      "stubs": {}
    }
  ]
}
```

Launch it with CodeLLDB from `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Debug Jack app",
      "type": "lldb",
      "request": "launch",
      "preLaunchTask": "jack: build debug app",
      "program": "${workspaceFolder}/.jack/debug/app",
      "cwd": "${workspaceFolder}",
      "args": [],
      "env": {},
      "terminal": "integrated"
    }
  ]
}
```

Debugging currently targets local Linux x86-64. LLVM provides Jack-aware local
types; C-backend debugging uses the information Clang can recover from generated
C and is best effort. Optimized builds may move statements or omit values.

## Development

Open this folder in VSCode and press `F5` to launch an Extension Development Host.
Open any `.jack` or `.jk` file there to inspect highlighting.

Package a local VSIX with:

```bash
npm run package:vsix
```

The packaging script preloads a small Node 18 compatibility shim before running `@vscode/vsce`.

The package intentionally has no build step yet. The first LSP client is plain JavaScript and starts `python3 -m jack.lsp_server` by default. During local development, the extension adds the repository root to `PYTHONPATH`; once installed elsewhere, either install the Python package or configure `jack.lsp.pythonPath` / `jack.lsp.serverArgs`.

## LSP Notes

The language server indexes `.jack` and `.jk` files in every open workspace and
configured `jack.compiler.moduleRoots`. Open editor contents override files on
disk throughout the import graph, so hover, definitions, completion, references,
and rename work across unsaved changes and imported modules.

Analysis starts after `jack.lsp.analysisDelay` milliseconds. While typing, pure
comptime code is evaluated without printing; comptime host extern calls are
deferred and the last complete semantic snapshot remains available. Saving runs
the complete configured comptime analysis. Rename is deliberately conservative:
builtins, extern ABI names, module names, import aliases, and generated
specializations cannot be renamed.
