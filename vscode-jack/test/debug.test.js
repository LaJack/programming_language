const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const test = require('node:test');

const {
  JackDebugSupport,
  JackTaskProvider,
  compilerArguments,
  debugOutputPath,
} = require('../client/debug');

function configuration(values) {
  return { get: (name, fallback) => (name in values ? values[name] : fallback) };
}

function mockVscode(log, exitCode = 0) {
  const document = {
    languageId: 'jack',
    uri: { scheme: 'file', fsPath: '/workspace/src/main.jack' },
    save: async () => {
      log.push('save');
      return true;
    },
  };
  const output = {
    clear: () => log.push('clear'),
    append: (value) => log.push(['output', value]),
    appendLine: (value) => log.push(['output', `${value}\n`]),
    show: () => log.push('show-output'),
    dispose: () => {},
  };
  class ProcessExecution {
    constructor(command, args, options) {
      Object.assign(this, { command, args, options });
    }
  }
  class Task {
    constructor(definition, scope, name, source, execution, problemMatchers) {
      Object.assign(this, { definition, scope, name, source, execution, problemMatchers });
    }
  }
  const values = {
    'jack.compiler': {
      path: '/tools/jack',
      clangPath: '/tools/clang',
      moduleRoots: ['/workspace/modules'],
      stubs: { 'hw.spi': 'tests.spi' },
    },
    'jack.debug': {
      cwd: '${workspaceFolder}/run',
      args: ['one', 'two'],
      env: { MODE: 'test' },
    },
  };
  const vscode = {
    window: {
      activeTextEditor: { document },
      createOutputChannel: () => output,
      showErrorMessage: (message) => log.push(['error', message]),
    },
    workspace: {
      getWorkspaceFolder: () => ({ name: 'workspace', uri: { fsPath: '/workspace' } }),
      getConfiguration: (section) => configuration(values[section] || {}),
    },
    extensions: { getExtension: () => ({ id: 'vadimcn.vscode-lldb' }) },
    debug: {
      startDebugging: async (_folder, config) => {
        log.push(['debug', config]);
        return true;
      },
    },
    commands: { registerCommand: (_name, callback) => ({ callback, dispose() {} }) },
    tasks: { registerTaskProvider: () => ({ dispose() {} }) },
    ProcessExecution,
    Task,
    TaskScope: { Workspace: 1 },
  };
  const childProcess = {
    spawn: (command, args, options) => {
      log.push(['spawn', command, args, options]);
      const child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      process.nextTick(() => child.emit('close', exitCode));
      return child;
    },
  };
  return { vscode, childProcess, document };
}

test('compiler arguments are deterministic and never require a shell', () => {
  assert.deepEqual(compilerArguments({
    entry: '/w/main.jack',
    output: '/w/program',
    backend: 'c',
    optimization: 2,
    clang: '/bin/clang',
    moduleRoots: ['/one', '/two'],
    stubs: { zed: 'stub.zed', alpha: 'stub.alpha' },
  }), [
    '--backend', 'c', '-g', '-O2', '--cc', '/bin/clang',
    '--module-root', '/one', '--module-root', '/two',
    '--stub', 'alpha=stub.alpha', '--stub', 'zed=stub.zed',
    '/w/main.jack', '-o', '/w/program',
  ]);
});

test('debug output mirrors the entry path below the workspace', () => {
  assert.equal(
    debugOutputPath('/workspace', '/workspace/src/tools/main.jack'),
    path.join('/workspace', '.jack', 'debug', 'src', 'tools', 'main'),
  );
});

test('F5 saves, builds with debug LLVM, and launches CodeLLDB', async () => {
  const log = [];
  const { vscode, childProcess } = mockVscode(log);
  const support = new JackDebugSupport(
    vscode,
    { subscriptions: [] },
    childProcess,
    { platform: 'linux', arch: 'x64' },
  );

  assert.equal(await support.debugCurrentFile(), true);
  assert.equal(log[0], 'save');
  const spawn = log.find((item) => Array.isArray(item) && item[0] === 'spawn');
  assert.equal(spawn[1], '/tools/jack');
  assert.equal(spawn[3].shell, false);
  assert.deepEqual(spawn[2].slice(0, 6), [
    '--backend', 'llvm', '-g', '-O0', '--cc', '/tools/clang',
  ]);
  assert.ok(spawn[2].includes('hw.spi=tests.spi'));
  const launch = log.find((item) => Array.isArray(item) && item[0] === 'debug')[1];
  assert.equal(launch.type, 'lldb');
  assert.equal(launch.program, '/workspace/.jack/debug/src/main');
  assert.equal(launch.cwd, '/workspace/run');
  assert.deepEqual(launch.args, ['one', 'two']);
  assert.deepEqual(launch.env, { MODE: 'test' });
});

test('a failed build reveals diagnostics and does not launch LLDB', async () => {
  const log = [];
  const { vscode, childProcess } = mockVscode(log, 1);
  const support = new JackDebugSupport(
    vscode,
    { subscriptions: [] },
    childProcess,
    { platform: 'linux', arch: 'x64' },
  );

  assert.equal(await support.debugCurrentFile(), false);
  assert.ok(log.includes('show-output'));
  assert.equal(log.some((item) => Array.isArray(item) && item[0] === 'debug'), false);
});

test('task provider maps all public fields to a ProcessExecution', () => {
  const log = [];
  const { vscode } = mockVscode(log);
  const provider = new JackTaskProvider(vscode);
  const task = provider.taskForDefinition({
    type: 'jack',
    entry: '/workspace/main.jack',
    output: '/workspace/build/main',
    backend: 'c',
    optimization: 3,
    moduleRoots: ['/mods'],
    stubs: { api: 'test.api' },
    clang: '/custom/clang',
  }, { uri: { fsPath: '/workspace' } });

  assert.equal(task.execution.command, '/tools/jack');
  assert.deepEqual(task.problemMatchers, ['$jack']);
  assert.deepEqual(task.execution.args, [
    '--backend', 'c', '-g', '-O3', '--cc', '/custom/clang',
    '--module-root', '/mods', '--stub', 'api=test.api',
    '/workspace/main.jack', '-o', '/workspace/build/main',
  ]);
});

test('unsupported hosts are rejected before saving or compiling', async () => {
  const log = [];
  const { vscode, childProcess } = mockVscode(log);
  const support = new JackDebugSupport(
    vscode,
    { subscriptions: [] },
    childProcess,
    { platform: 'darwin', arch: 'arm64' },
  );

  assert.equal(await support.debugCurrentFile(), false);
  assert.equal(log.some((item) => Array.isArray(item) && item[0] === 'spawn'), false);
  assert.match(log[0][1], /Linux x86-64/);
});
