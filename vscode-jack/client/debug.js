const cp = require('child_process');
const path = require('path');

const JACK_LANGUAGE_ID = 'jack';
const JACK_TASK_TYPE = 'jack';

function debugOutputPath(workspacePath, entryPath) {
  const relative = path.relative(workspacePath, entryPath);
  const parsed = path.parse(relative);
  return path.join(workspacePath, '.jack', 'debug', parsed.dir, parsed.name);
}

function compilerArguments(options) {
  const args = [
    '--backend', options.backend || 'llvm',
    '-g',
    `-O${options.optimization === undefined ? 0 : options.optimization}`,
    '--cc', options.clang || 'clang',
  ];
  for (const root of options.moduleRoots || []) {
    args.push('--module-root', root);
  }
  for (const name of Object.keys(options.stubs || {}).sort()) {
    args.push('--stub', `${name}=${options.stubs[name]}`);
  }
  args.push(options.entry, '-o', options.output);
  return args;
}

class JackTaskProvider {
  constructor(vscode) {
    this.vscode = vscode;
  }

  provideTasks() {
    const editor = this.vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== JACK_LANGUAGE_ID) {
      return [];
    }
    const folder = this.vscode.workspace.getWorkspaceFolder(editor.document.uri);
    if (!folder) {
      return [];
    }
    const entry = editor.document.uri.fsPath;
    const definition = {
      type: JACK_TASK_TYPE,
      entry,
      output: debugOutputPath(folder.uri.fsPath, entry),
      backend: 'llvm',
      optimization: 0,
    };
    return [this.taskForDefinition(definition, folder)];
  }

  resolveTask(task) {
    const definition = task.definition;
    if (!definition || !definition.entry || !definition.output) {
      return undefined;
    }
    return this.taskForDefinition(definition, task.scope, task.name);
  }

  taskForDefinition(definition, scope, name = 'Build debug executable') {
    const compiler = this.vscode.workspace
      .getConfiguration('jack.compiler', scope && scope.uri)
      .get('path', 'jack');
    const execution = new this.vscode.ProcessExecution(
      compiler,
      compilerArguments({
        ...definition,
        clang: definition.clang || 'clang',
      }),
      { cwd: scope && scope.uri ? scope.uri.fsPath : undefined },
    );
    return new this.vscode.Task(
      definition,
      scope || this.vscode.TaskScope.Workspace,
      name,
      'Jack',
      execution,
      ['$jack'],
    );
  }
}

class JackDebugSupport {
  constructor(vscode, context, childProcess = cp, host = process) {
    this.vscode = vscode;
    this.context = context;
    this.childProcess = childProcess;
    this.host = host;
    this.output = vscode.window.createOutputChannel('Jack Build');
    this.taskProvider = new JackTaskProvider(vscode);
  }

  register() {
    this.context.subscriptions.push(
      this.output,
      this.vscode.commands.registerCommand(
        'jack.debugCurrentFile',
        () => this.debugCurrentFile(),
      ),
      this.vscode.tasks.registerTaskProvider(JACK_TASK_TYPE, this.taskProvider),
    );
  }

  async debugCurrentFile() {
    if (this.host.platform !== 'linux' || this.host.arch !== 'x64') {
      this.vscode.window.showErrorMessage(
        'Jack debugging currently requires local Linux x86-64.',
      );
      return false;
    }
    const editor = this.vscode.window.activeTextEditor;
    const document = editor && editor.document;
    if (!document || document.languageId !== JACK_LANGUAGE_ID) {
      this.vscode.window.showErrorMessage('Open a Jack source file before starting the debugger.');
      return false;
    }
    if (document.uri.scheme !== 'file') {
      this.vscode.window.showErrorMessage('Jack debugging requires a local source file.');
      return false;
    }
    const folder = this.vscode.workspace.getWorkspaceFolder(document.uri);
    if (!folder) {
      this.vscode.window.showErrorMessage('Open the Jack source inside a VS Code workspace.');
      return false;
    }
    if (!(await document.save())) {
      this.vscode.window.showErrorMessage('Could not save the Jack source before debugging.');
      return false;
    }
    const codeLLDB = this.vscode.extensions.getExtension('vadimcn.vscode-lldb');
    if (!codeLLDB) {
      this.vscode.window.showErrorMessage('Install CodeLLDB to debug Jack programs.');
      return false;
    }

    const compiler = this.vscode.workspace.getConfiguration('jack.compiler', document.uri);
    const debug = this.vscode.workspace.getConfiguration('jack.debug', document.uri);
    const entry = document.uri.fsPath;
    const output = debugOutputPath(folder.uri.fsPath, entry);
    const options = {
      entry,
      output,
      backend: 'llvm',
      optimization: 0,
      clang: compiler.get('clangPath', 'clang'),
      moduleRoots: compiler.get('moduleRoots', []),
      stubs: compiler.get('stubs', {}),
    };

    this.output.clear();
    this.output.appendLine(`Building ${entry}`);
    const succeeded = await this.runCompiler(
      compiler.get('path', 'jack'),
      compilerArguments(options),
      folder.uri.fsPath,
    );
    if (!succeeded) {
      this.output.show(true);
      return false;
    }
    const cwd = this.expandWorkspace(debug.get('cwd', '${workspaceFolder}'), folder);
    return this.vscode.debug.startDebugging(folder, {
      type: 'lldb',
      request: 'launch',
      name: `Debug ${path.basename(entry)}`,
      program: output,
      args: debug.get('args', []),
      cwd,
      env: debug.get('env', {}),
      terminal: 'integrated',
    });
  }

  runCompiler(command, args, cwd) {
    return new Promise((resolve) => {
      let child;
      try {
        child = this.childProcess.spawn(command, args, { cwd, shell: false });
      } catch (error) {
        this.output.appendLine(`Cannot start Jack compiler "${command}": ${error.message}`);
        resolve(false);
        return;
      }
      child.stdout.on('data', (chunk) => this.output.append(chunk.toString()));
      child.stderr.on('data', (chunk) => this.output.append(chunk.toString()));
      child.on('error', (error) => {
        this.output.appendLine(`Cannot start Jack compiler "${command}": ${error.message}`);
        resolve(false);
      });
      child.on('close', (code) => {
        if (code !== 0) {
          this.output.appendLine(`Jack compiler exited with status ${code}.`);
        }
        resolve(code === 0);
      });
    });
  }

  expandWorkspace(value, folder) {
    return String(value).replaceAll('${workspaceFolder}', folder.uri.fsPath);
  }
}

module.exports = {
  JackDebugSupport,
  JackTaskProvider,
  compilerArguments,
  debugOutputPath,
};
