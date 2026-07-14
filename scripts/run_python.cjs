const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const localPython = process.platform === 'win32'
  ? path.join(root, '.venv', 'Scripts', 'python.exe')
  : path.join(root, '.venv', 'bin', 'python');
const candidates = [process.env.PYTHON, localPython, 'python3', 'python'].filter(Boolean);

let python = null;
for (const candidate of candidates) {
  if (candidate.includes(path.sep) && !fs.existsSync(candidate)) continue;
  const probe = spawnSync(candidate, ['--version'], { stdio: 'ignore' });
  if (!probe.error && probe.status === 0) {
    python = candidate;
    break;
  }
}

if (!python) {
  console.error('Python 3 not found. Set PYTHON or create .venv before building.');
  process.exit(1);
}

const result = spawnSync(python, process.argv.slice(2), { cwd: root, stdio: 'inherit' });
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
