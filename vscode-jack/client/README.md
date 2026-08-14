# LSP Client

This folder contains the first Jack VSCode language client.

The current client is intentionally dependency-free. It starts the Python language
server with:

```bash
python3 -m jack.lsp_server
```

For now the server publishes parse diagnostics and document symbols, plus hover
and same-document go-to-definition from a lightweight per-document symbol index.
Parser-created AST nodes carry source spans; semantic diagnostics, cross-file
resolution, and completion still need later passes and richer symbol indexes to
consume them.
