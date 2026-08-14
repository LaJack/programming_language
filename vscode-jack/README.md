# Jack VSCode Extension

This extension provides editor basics for the experimental Jack language:

- language registration for `.jack` and `.jk`
- TextMate syntax highlighting
- experimental parse diagnostics through a Python LSP server
- document symbols for Outline and Breadcrumbs
- hover information for declarations and built-in types
- same-document go-to-definition for parsed symbols
- comments, brackets, indentation, and string pairing rules

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

The language server currently reports parser errors using parser-provided source spans and builds a lightweight per-document symbol index for document symbols, hover, and same-document go-to-definition. Semantic diagnostics, cross-file resolution, and completion replacement ranges still need later passes and richer symbol indexes to preserve or consume those spans.
