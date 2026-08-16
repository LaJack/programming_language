const cp = require('child_process');
const path = require('path');
const vscode = require('vscode');
const { JackDebugSupport } = require('./debug');

const JACK_LANGUAGE_ID = 'jack';
const SEMANTIC_TOKEN_TYPES = ['namespace', 'type', 'struct', 'typeParameter', 'function', 'method', 'property', 'parameter', 'variable', 'keyword', 'string', 'number', 'operator', 'comment'];
const SEMANTIC_TOKEN_MODIFIERS = ['declaration', 'definition', 'readonly', 'modification', 'comptime', 'extern', 'public', 'defaultLibrary'];

class JackLanguageClient {
  constructor(context) {
    this.context = context;
    this.output = vscode.window.createOutputChannel('Jack Language Server');
    this.diagnostics = vscode.languages.createDiagnosticCollection('jack');
    this.disposables = [];
    this.pending = new Map();
    this.nextRequestId = 1;
    this.stdoutBuffer = Buffer.alloc(0);
    this.process = undefined;
    this.initialized = false;
    this.ready = Promise.resolve();
  }

  start() {
    const config = vscode.workspace.getConfiguration('jack.lsp');
    if (!config.get('enabled', true)) {
      return;
    }

    const pythonPath = config.get('pythonPath', process.platform === 'win32' ? 'python' : 'python3');
    const serverArgs = config.get('serverArgs', ['-m', 'jack.lsp_server']);
    const cwd = this.workspaceRoot() || path.dirname(this.context.extensionPath);
    const env = this.serverEnv();

    this.output.appendLine(`Starting Jack language server: ${pythonPath} ${serverArgs.join(' ')}`);
    this.process = cp.spawn(pythonPath, serverArgs, { cwd, env });

    this.process.stdout.on('data', (chunk) => this.handleStdout(chunk));
    this.process.stderr.on('data', (chunk) => this.output.append(chunk.toString()));
    this.process.on('error', (error) => {
      this.output.appendLine(`Failed to start Jack language server: ${error.message}`);
      vscode.window.showWarningMessage(`Failed to start Jack language server: ${error.message}`);
    });
    this.process.on('exit', (code, signal) => {
      this.output.appendLine(`Jack language server exited with code ${code} signal ${signal}`);
      this.process = undefined;
      this.initialized = false;
      this.diagnostics.clear();
      for (const pending of this.pending.values()) {
        pending.reject(new Error('Jack language server exited.'));
      }
      this.pending.clear();
    });

    this.disposables.push(
      vscode.workspace.onDidOpenTextDocument((document) => this.didOpen(document)),
      vscode.workspace.onDidChangeTextDocument((event) => this.didChange(event.document)),
      vscode.workspace.onDidSaveTextDocument((document) => this.didSave(document)),
      vscode.workspace.onDidCloseTextDocument((document) => this.didClose(document)),
      vscode.languages.registerDocumentSymbolProvider(
        { language: JACK_LANGUAGE_ID },
        {
          provideDocumentSymbols: (document) => this.provideDocumentSymbols(document),
        },
      ),
      vscode.languages.registerHoverProvider(
        { language: JACK_LANGUAGE_ID },
        {
          provideHover: (document, position) => this.provideHover(document, position),
        },
      ),
      vscode.languages.registerDefinitionProvider(
        { language: JACK_LANGUAGE_ID },
        {
          provideDefinition: (document, position) => this.provideDefinition(document, position),
        },
      ),
      vscode.languages.registerCompletionItemProvider(
        { language: JACK_LANGUAGE_ID },
        {
          provideCompletionItems: (document, position) => this.provideCompletionItems(document, position),
        },
        '.',
      ),
      vscode.languages.registerReferenceProvider(
        { language: JACK_LANGUAGE_ID },
        {
          provideReferences: (document, position, context) => this.provideReferences(document, position, context),
        },
      ),
      vscode.languages.registerRenameProvider(
        { language: JACK_LANGUAGE_ID },
        {
          prepareRename: (document, position) => this.prepareRename(document, position),
          provideRenameEdits: (document, position, newName) => this.provideRenameEdits(document, position, newName),
        },
      ),
      vscode.languages.registerDocumentSemanticTokensProvider(
        { language: JACK_LANGUAGE_ID },
        { provideDocumentSemanticTokens: (document) => this.provideSemanticTokens(document) },
        new vscode.SemanticTokensLegend(SEMANTIC_TOKEN_TYPES, SEMANTIC_TOKEN_MODIFIERS),
      ),
      vscode.languages.registerSignatureHelpProvider(
        { language: JACK_LANGUAGE_ID },
        { provideSignatureHelp: (document, position) => this.provideSignatureHelp(document, position) },
        '(', ',',
      ),
      vscode.languages.registerCodeActionsProvider(
        { language: JACK_LANGUAGE_ID },
        { provideCodeActions: (document, range, context) => this.provideCodeActions(document, range, context) },
        { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] },
      ),
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (
          event.affectsConfiguration('jack.lsp')
          || event.affectsConfiguration('jack.compiler.moduleRoots')
          || event.affectsConfiguration('jack.compiler.stubs')
        ) {
          vscode.window.showInformationMessage('Reload the window to restart the Jack language server with the new settings.');
        }
      }),
    );

    this.registerFileWatchers();

    this.ready = this.initialize();
  }

  initialize() {
    const workspaceFolders = vscode.workspace.workspaceFolders || [];
    const rootFolder = workspaceFolders[0];
    return this.sendRequest('initialize', {
      processId: process.pid,
      clientInfo: {
        name: 'jack-vscode',
        version: this.context.extension.packageJSON.version,
      },
      rootUri: rootFolder ? rootFolder.uri.toString() : null,
      workspaceFolders: workspaceFolders.map((folder) => ({
        uri: folder.uri.toString(),
        name: folder.name,
      })),
      capabilities: {},
      initializationOptions: this.initializationOptions(),
    }).then(() => {
      this.initialized = true;
      this.sendNotification('initialized', {});
      for (const document of vscode.workspace.textDocuments) {
        this.didOpen(document);
      }
    }).catch((error) => {
      this.output.appendLine(`Jack language server initialization failed: ${error.message}`);
    });
  }

  didOpen(document) {
    if (!this.initialized || !this.isJackDocument(document)) {
      return;
    }
    this.sendNotification('textDocument/didOpen', {
      textDocument: this.textDocumentItem(document),
    });
  }

  didChange(document) {
    if (!this.initialized || !this.isJackDocument(document)) {
      return;
    }
    this.sendNotification('textDocument/didChange', {
      textDocument: {
        uri: document.uri.toString(),
        version: document.version,
      },
      contentChanges: [
        {
          text: document.getText(),
        },
      ],
    });
  }

  didSave(document) {
    if (!this.initialized || !this.isJackDocument(document)) {
      return;
    }
    this.sendNotification('textDocument/didSave', {
      textDocument: {
        uri: document.uri.toString(),
      },
      text: document.getText(),
    });
  }

  didClose(document) {
    if (!this.initialized || !this.isJackDocument(document)) {
      return;
    }
    this.sendNotification('textDocument/didClose', {
      textDocument: {
        uri: document.uri.toString(),
      },
    });
    this.diagnostics.delete(document.uri);
  }

  provideHover(document, position) {
    if (!this.isJackDocument(document)) {
      return undefined;
    }

    const requestHover = () => {
      if (!this.initialized) {
        return undefined;
      }
      return this.sendRequest('textDocument/hover', {
        textDocument: {
          uri: document.uri.toString(),
        },
        position: this.toLspPosition(position),
      }).then((hover) => this.toVscodeHover(hover));
    };

    return this.ready.then(requestHover).catch((error) => {
      this.output.appendLine(`Hover failed: ${error.message}`);
      return undefined;
    });
  }

  provideDefinition(document, position) {
    if (!this.isJackDocument(document)) {
      return [];
    }

    const requestDefinition = () => {
      if (!this.initialized) {
        return [];
      }
      return this.sendRequest('textDocument/definition', {
        textDocument: {
          uri: document.uri.toString(),
        },
        position: this.toLspPosition(position),
      }).then((locations) => this.toVscodeLocations(locations));
    };

    return this.ready.then(requestDefinition).catch((error) => {
      this.output.appendLine(`Definition failed: ${error.message}`);
      return [];
    });
  }

  provideCompletionItems(document, position) {
    if (!this.isJackDocument(document)) {
      return [];
    }
    return this.ready.then(() => {
      if (!this.initialized) {
        return [];
      }
      return this.sendRequest('textDocument/completion', {
        textDocument: { uri: document.uri.toString() },
        position: this.toLspPosition(position),
      }).then((items) => (items || []).map((item) => this.toVscodeCompletionItem(item)));
    }).catch((error) => {
      this.output.appendLine(`Completion failed: ${error.message}`);
      return [];
    });
  }

  provideReferences(document, position, context) {
    return this.ready.then(() => {
      if (!this.initialized) {
        return [];
      }
      return this.sendRequest('textDocument/references', {
        textDocument: { uri: document.uri.toString() },
        position: this.toLspPosition(position),
        context: { includeDeclaration: Boolean(context && context.includeDeclaration) },
      }).then((locations) => this.toVscodeLocations(locations));
    }).catch((error) => {
      this.output.appendLine(`References failed: ${error.message}`);
      return [];
    });
  }

  prepareRename(document, position) {
    return this.ready.then(() => {
      if (!this.initialized) {
        return undefined;
      }
      return this.sendRequest('textDocument/prepareRename', {
        textDocument: { uri: document.uri.toString() },
        position: this.toLspPosition(position),
      }).then((result) => {
        if (!result) {
          return undefined;
        }
        return {
          range: this.toVscodeRange(result.range || result),
          placeholder: result.placeholder,
        };
      });
    }).catch((error) => {
      this.output.appendLine(`Prepare rename failed: ${error.message}`);
      return undefined;
    });
  }

  provideRenameEdits(document, position, newName) {
    return this.ready.then(() => {
      if (!this.initialized) {
        return undefined;
      }
      return this.sendRequest('textDocument/rename', {
        textDocument: { uri: document.uri.toString() },
        position: this.toLspPosition(position),
        newName,
      }).then((edit) => this.toVscodeWorkspaceEdit(edit));
    }).catch((error) => {
      vscode.window.showErrorMessage(error.message);
      this.output.appendLine(`Rename failed: ${error.message}`);
      return undefined;
    });
  }

  provideSemanticTokens(document) {
    return this.ready.then(() => this.sendRequest('textDocument/semanticTokens/full', {
      textDocument: { uri: document.uri.toString() },
    })).then((value) => new vscode.SemanticTokens(new Uint32Array((value && value.data) || [])))
      .catch((error) => { this.output.appendLine(`Semantic tokens failed: ${error.message}`); return null; });
  }

  provideSignatureHelp(document, position) {
    return this.ready.then(() => this.sendRequest('textDocument/signatureHelp', {
      textDocument: { uri: document.uri.toString() },
      position: this.toLspPosition(position),
    })).then((value) => this.toVscodeSignatureHelp(value))
      .catch((error) => { this.output.appendLine(`Signature help failed: ${error.message}`); return null; });
  }

  provideCodeActions(document, range, context) {
    return this.ready.then(() => this.sendRequest('textDocument/codeAction', {
      textDocument: { uri: document.uri.toString() },
      range: this.toLspRange(range),
      context: { diagnostics: (context.diagnostics || []).map((item) => this.toLspDiagnostic(item)) },
    })).then((values) => (values || []).map((value) => {
      const action = new vscode.CodeAction(value.title, vscode.CodeActionKind.QuickFix);
      action.edit = this.toVscodeWorkspaceEdit(value.edit);
      return action;
    })).catch((error) => { this.output.appendLine(`Code actions failed: ${error.message}`); return []; });
  }

  toVscodeSignatureHelp(value) {
    if (!value) return null;
    const help = new vscode.SignatureHelp();
    help.activeSignature = value.activeSignature || 0;
    help.activeParameter = value.activeParameter || 0;
    help.signatures = (value.signatures || []).map((item) => {
      const signature = new vscode.SignatureInformation(item.label || '');
      signature.activeParameter = item.activeParameter;
      signature.parameters = (item.parameters || []).map((parameter) => new vscode.ParameterInformation(parameter.label || ''));
      return signature;
    });
    return help;
  }

  toVscodeHover(hover) {
    if (!hover) {
      return undefined;
    }
    const contents = hover.contents || '';
    let markdown;
    if (typeof contents === 'string') {
      markdown = new vscode.MarkdownString(contents);
    } else {
      markdown = new vscode.MarkdownString(contents.value || '');
    }
    return new vscode.Hover(markdown, this.toVscodeRange(hover.range));
  }

  toVscodeLocations(locations) {
    const values = Array.isArray(locations) ? locations : locations ? [locations] : [];
    return values.map((location) => new vscode.Location(
      vscode.Uri.parse(location.uri),
      this.toVscodeRange(location.range),
    ));
  }

  toVscodeCompletionItem(item) {
    const completion = new vscode.CompletionItem(
      item.label || '',
      this.toVscodeCompletionKind(item.kind),
    );
    completion.detail = item.detail;
    if (item.textEdit) {
      completion.textEdit = vscode.TextEdit.replace(
        this.toVscodeRange(item.textEdit.range),
        item.textEdit.newText || item.label || '',
      );
    }
    return completion;
  }

  toVscodeWorkspaceEdit(value) {
    const edit = new vscode.WorkspaceEdit();
    for (const change of (value && value.documentChanges) || []) {
      if (!change.textDocument || !change.textDocument.uri) {
        continue;
      }
      edit.set(
        vscode.Uri.parse(change.textDocument.uri),
        (change.edits || []).map((item) => vscode.TextEdit.replace(
          this.toVscodeRange(item.range),
          item.newText || '',
        )),
      );
    }
    return edit;
  }

  toVscodeCompletionKind(kind) {
    const values = vscode.CompletionItemKind;
    return {
      2: values.Method,
      3: values.Function,
      5: values.Field,
      6: values.Variable,
      7: values.Class,
      8: values.Interface,
      9: values.Module,
      14: values.Keyword,
    }[kind] || values.Text;
  }

  provideDocumentSymbols(document) {
    if (!this.isJackDocument(document)) {
      return [];
    }

    const requestSymbols = () => {
      if (!this.initialized) {
        return [];
      }
      return this.sendRequest('textDocument/documentSymbol', {
        textDocument: {
          uri: document.uri.toString(),
        },
      }).then((symbols) => (symbols || []).map((symbol) => this.toVscodeDocumentSymbol(symbol)));
    };

    return this.ready.then(requestSymbols).catch((error) => {
      this.output.appendLine(`Document symbols failed: ${error.message}`);
      return [];
    });
  }

  toVscodeDocumentSymbol(symbol) {
    const item = new vscode.DocumentSymbol(
      symbol.name || '',
      symbol.detail || '',
      this.toVscodeSymbolKind(symbol.kind),
      this.toVscodeRange(symbol.range),
      this.toVscodeRange(symbol.selectionRange || symbol.range),
    );
    for (const child of symbol.children || []) {
      item.children.push(this.toVscodeDocumentSymbol(child));
    }
    return item;
  }

  textDocumentItem(document) {
    return {
      uri: document.uri.toString(),
      languageId: JACK_LANGUAGE_ID,
      version: document.version,
      text: document.getText(),
    };
  }

  isJackDocument(document) {
    return document.languageId === JACK_LANGUAGE_ID;
  }

  sendRequest(method, params) {
    const id = this.nextRequestId++;
    this.writeMessage({ jsonrpc: '2.0', id, method, params });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }

  sendNotification(method, params) {
    this.writeMessage({ jsonrpc: '2.0', method, params });
  }

  writeMessage(message) {
    if (!this.process || !this.process.stdin.writable) {
      return;
    }
    const body = JSON.stringify(message);
    const payload = `Content-Length: ${Buffer.byteLength(body, 'utf8')}\r\n\r\n${body}`;
    if (this.traceServer()) {
      this.output.appendLine(`--> ${body}`);
    }
    this.process.stdin.write(payload, 'utf8');
  }

  handleStdout(chunk) {
    this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk]);

    while (true) {
      const headerEnd = this.stdoutBuffer.indexOf('\r\n\r\n');
      if (headerEnd === -1) {
        return;
      }

      const header = this.stdoutBuffer.subarray(0, headerEnd).toString('ascii');
      const lengthMatch = /Content-Length:\s*(\d+)/i.exec(header);
      if (!lengthMatch) {
        this.output.appendLine(`Malformed LSP header: ${header}`);
        this.stdoutBuffer = Buffer.alloc(0);
        return;
      }

      const length = Number(lengthMatch[1]);
      const messageStart = headerEnd + 4;
      const messageEnd = messageStart + length;
      if (this.stdoutBuffer.length < messageEnd) {
        return;
      }

      const body = this.stdoutBuffer.subarray(messageStart, messageEnd).toString('utf8');
      this.stdoutBuffer = this.stdoutBuffer.subarray(messageEnd);
      if (this.traceServer()) {
        this.output.appendLine(`<-- ${body}`);
      }
      this.handleMessage(JSON.parse(body));
    }
  }

  handleMessage(message) {
    if (Object.prototype.hasOwnProperty.call(message, 'id')) {
      const pending = this.pending.get(message.id);
      if (!pending) {
        return;
      }
      this.pending.delete(message.id);
      if (message.error) {
        pending.reject(new Error(message.error.message || 'LSP request failed.'));
      } else {
        pending.resolve(message.result);
      }
      return;
    }

    if (message.method === 'textDocument/publishDiagnostics') {
      this.publishDiagnostics(message.params || {});
    } else if (message.method === 'window/logMessage') {
      const params = message.params || {};
      this.output.appendLine(params.message || '');
    }
  }

  publishDiagnostics(params) {
    if (!params.uri) {
      return;
    }
    const uri = vscode.Uri.parse(params.uri);
    const diagnostics = (params.diagnostics || []).map((diagnostic) => {
      const range = this.toVscodeRange(diagnostic.range);
      const severity = this.toVscodeSeverity(diagnostic.severity);
      const vscodeDiagnostic = new vscode.Diagnostic(range, diagnostic.message || '', severity);
      vscodeDiagnostic.source = diagnostic.source || 'jack';
      vscodeDiagnostic.code = diagnostic.code;
      vscodeDiagnostic.relatedInformation = (diagnostic.relatedInformation || []).map((item) => new vscode.DiagnosticRelatedInformation(
        new vscode.Location(vscode.Uri.parse(item.location.uri), this.toVscodeRange(item.location.range)),
        item.message || '',
      ));
      return vscodeDiagnostic;
    });
    this.diagnostics.set(uri, diagnostics);
  }

  toLspPosition(position) {
    return {
      line: position.line,
      character: position.character,
    };
  }

  toLspRange(range) {
    return { start: this.toLspPosition(range.start), end: this.toLspPosition(range.end) };
  }

  toLspDiagnostic(value) {
    return { range: this.toLspRange(value.range), message: value.message, code: value.code, source: value.source };
  }

  toVscodeRange(range) {
    const start = range && range.start ? range.start : { line: 0, character: 0 };
    const end = range && range.end ? range.end : start;
    return new vscode.Range(
      new vscode.Position(start.line || 0, start.character || 0),
      new vscode.Position(end.line || 0, end.character || 0),
    );
  }

  toVscodeSymbolKind(kind) {
    switch (kind) {
      case 2:
        return vscode.SymbolKind.Module;
      case 6:
        return vscode.SymbolKind.Method;
      case 8:
        return vscode.SymbolKind.Field;
      case 9:
        return vscode.SymbolKind.Constructor;
      case 11:
        return vscode.SymbolKind.Interface;
      case 12:
        return vscode.SymbolKind.Function;
      case 13:
        return vscode.SymbolKind.Variable;
      case 23:
        return vscode.SymbolKind.Struct;
      default:
        return vscode.SymbolKind.Object;
    }
  }

  toVscodeSeverity(severity) {
    switch (severity) {
      case 1:
        return vscode.DiagnosticSeverity.Error;
      case 2:
        return vscode.DiagnosticSeverity.Warning;
      case 3:
        return vscode.DiagnosticSeverity.Information;
      case 4:
        return vscode.DiagnosticSeverity.Hint;
      default:
        return vscode.DiagnosticSeverity.Error;
    }
  }

  workspaceRoot() {
    const folders = vscode.workspace.workspaceFolders;
    return folders && folders.length > 0 ? folders[0].uri.fsPath : undefined;
  }

  initializationOptions() {
    const compiler = vscode.workspace.getConfiguration('jack.compiler');
    const lsp = vscode.workspace.getConfiguration('jack.lsp');
    return {
      moduleRoots: compiler.get('moduleRoots', []),
      stubs: compiler.get('stubs', {}),
      analysisDelay: lsp.get('analysisDelay', 200),
    };
  }

  registerFileWatchers() {
    const roots = [
      ...(vscode.workspace.workspaceFolders || []).map((folder) => folder.uri.fsPath),
      ...vscode.workspace.getConfiguration('jack.compiler').get('moduleRoots', []),
    ];
    for (const root of [...new Set(roots)]) {
      const watcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(root, '**/*.{jack,jk}'),
      );
      const notify = (uri, type) => this.sendNotification(
        'workspace/didChangeWatchedFiles',
        { changes: [{ uri: uri.toString(), type }] },
      );
      this.disposables.push(
        watcher,
        watcher.onDidCreate((uri) => notify(uri, 1)),
        watcher.onDidChange((uri) => notify(uri, 2)),
        watcher.onDidDelete((uri) => notify(uri, 3)),
      );
    }
  }

  serverEnv() {
    const candidates = [
      path.dirname(this.context.extensionPath),
      ...(vscode.workspace.workspaceFolders || []).map((folder) => folder.uri.fsPath),
    ];
    const existing = process.env.PYTHONPATH || '';
    const pythonPath = [...candidates, existing].filter(Boolean).join(path.delimiter);
    return {
      ...process.env,
      PYTHONPATH: pythonPath,
    };
  }

  traceServer() {
    return vscode.workspace.getConfiguration('jack.lsp').get('trace.server', false);
  }

  dispose() {
    for (const disposable of this.disposables) {
      disposable.dispose();
    }
    this.disposables = [];
    this.diagnostics.dispose();

    if (!this.process) {
      return;
    }

    const child = this.process;
    this.sendRequest('shutdown', {}).catch(() => undefined).finally(() => {
      if (child.stdin.writable) {
        this.sendNotification('exit', {});
      }
      setTimeout(() => {
        if (!child.killed) {
          child.kill();
        }
      }, 1000).unref();
    });
  }
}

let client;
let debugSupport;

function activate(context) {
  debugSupport = new JackDebugSupport(vscode, context);
  debugSupport.register();
  client = new JackLanguageClient(context);
  context.subscriptions.push(client);
  client.start();
}

function deactivate() {
  if (client) {
    client.dispose();
    client = undefined;
  }
  debugSupport = undefined;
}

module.exports = {
  activate,
  deactivate,
};
