const cp = require('child_process');
const path = require('path');
const vscode = require('vscode');
const { JackDebugSupport } = require('./debug');

const JACK_LANGUAGE_ID = 'jack';

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
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration('jack.lsp')) {
          vscode.window.showInformationMessage('Reload the window to restart the Jack language server with the new settings.');
        }
      }),
    );

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
