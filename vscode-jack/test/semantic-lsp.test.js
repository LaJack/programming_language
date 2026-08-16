const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.join(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const client = fs.readFileSync(path.join(root, 'client', 'extension.js'), 'utf8');

test('semantic analysis delay is configurable', () => {
  const property = manifest.contributes.configuration.properties['jack.lsp.analysisDelay'];
  assert.equal(property.default, 200);
  assert.equal(property.minimum, 0);
});

test('client initializes the server with project compiler settings', () => {
  assert.match(client, /initializationOptions: this\.initializationOptions\(\)/);
  assert.match(client, /moduleRoots: compiler\.get\('moduleRoots', \[\]\)/);
  assert.match(client, /stubs: compiler\.get\('stubs', \{\}\)/);
  assert.match(client, /analysisDelay: lsp\.get\('analysisDelay', 200\)/);
});

test('client registers semantic completion references and rename providers', () => {
  assert.match(client, /registerCompletionItemProvider/);
  assert.match(client, /registerReferenceProvider/);
  assert.match(client, /registerRenameProvider/);
  assert.match(client, /textDocument\/prepareRename/);
  assert.match(client, /textDocument\/rename/);
});

test('client registers semantic tokens signature help and code actions', () => {
  assert.match(client, /registerDocumentSemanticTokensProvider/);
  assert.match(client, /registerSignatureHelpProvider/);
  assert.match(client, /registerCodeActionsProvider/);
  assert.match(client, /textDocument\/semanticTokens\/full/);
  assert.match(client, /textDocument\/signatureHelp/);
  assert.match(client, /textDocument\/codeAction/);
});

test('client watches Jack files in workspaces and module roots', () => {
  assert.match(client, /createFileSystemWatcher/);
  assert.match(client, /\*\*\/\*\.\{jack,jk\}/);
  assert.match(client, /workspace\/didChangeWatchedFiles/);
});
