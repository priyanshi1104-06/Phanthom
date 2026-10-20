// Copies Phantom's backend into extension/server/ so the .vsix carries everything the extension
// starts (python -m backend). Only source files: never .env (secrets), caches or tests.
const fs = require('node:fs');
const path = require('node:path');

const repo = path.resolve(__dirname, '..', '..');
const target = path.resolve(__dirname, '..', 'server');
const ITEMS = ['backend', 'runner', 'sandbox', 'worker.py', 'phantom.py', 'requirements.txt', '.env.example'];
const SKIP = new Set(['__pycache__', '.env', '.hypothesis', 'tests']);

function copy(from, to) {
  if (SKIP.has(path.basename(from)) || from.endsWith('.pyc')) {
    return;
  }
  if (fs.statSync(from).isDirectory()) {
    fs.mkdirSync(to, { recursive: true });
    for (const name of fs.readdirSync(from)) {
      copy(path.join(from, name), path.join(to, name));
    }
  } else {
    fs.copyFileSync(from, to);
  }
}

fs.rmSync(target, { recursive: true, force: true });
for (const item of ITEMS) {
  copy(path.join(repo, item), path.join(target, item));
}
console.log(`bundled the backend into ${target}`);
