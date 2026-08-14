const { spawnSync } = require('child_process');
const path = require('path');

const preload = path.join(__dirname, 'node18-file-polyfill.cjs');
const existingNodeOptions = process.env.NODE_OPTIONS || '';
const nodeOptions = `${existingNodeOptions} --require=${preload}`.trim();

const executable = process.platform === 'win32' ? 'npx.cmd' : 'npx';
const result = spawnSync(
  executable,
  ['--yes', '@vscode/vsce', 'package', '--allow-missing-repository'],
  {
    cwd: path.join(__dirname, '..'),
    env: {
      ...process.env,
      NODE_OPTIONS: nodeOptions,
    },
    stdio: 'inherit',
  },
);

process.exit(result.status === null ? 1 : result.status);
