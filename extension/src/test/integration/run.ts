// End-to-end test in a real VS Code: npm run test:integration
// Starts the backend, downloads a VS Code build (once, into .vscode-test/), loads the extension
// into it and runs suite.ts there. Set PHANTOM_PYTHON if `python3` isn't your venv's Python, and
// VSCODE_EXECUTABLE to test with a VS Code you already have instead of downloading one.
import { ChildProcess, spawn } from 'node:child_process';
import * as path from 'node:path';
import { runTests } from '@vscode/test-electron';

const extensionRoot = path.resolve(__dirname, '../../..');
const repoRoot = path.resolve(extensionRoot, '..');

async function waitForBackend(url: string, backend: ChildProcess): Promise<void> {
  for (let i = 0; i < 60; i++) {
    if (backend.exitCode !== null) {
      throw new Error('the backend exited; is the venv active (pip install -r requirements.txt)?');
    }
    try {
      if ((await fetch(url + '/health')).ok) {
        return;
      }
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error('the backend did not start');
}

async function main(): Promise<void> {
  const port = process.env.PHANTOM_PORT ?? '8799';
  const backend = spawn(process.env.PHANTOM_PYTHON ?? 'python3', ['-m', 'backend'], {
    cwd: repoRoot, env: { ...process.env, PHANTOM_PORT: port, PYTHONDONTWRITEBYTECODE: '1' }, stdio: 'inherit',
  });
  try {
    await waitForBackend(`http://127.0.0.1:${port}`, backend);
    const asRoot = process.getuid?.() === 0; // e.g. in a container: Chromium needs --no-sandbox then
    await runTests({
      vscodeExecutablePath: process.env.VSCODE_EXECUTABLE,
      extensionDevelopmentPath: extensionRoot,
      extensionTestsPath: path.join(__dirname, 'suite'),
      extensionTestsEnv: { PHANTOM_TEST_BACKEND: `http://127.0.0.1:${port}`, PHANTOM_REPO: repoRoot },
      launchArgs: [path.join(repoRoot, 'samples'), '--disable-extensions', ...(asRoot ? ['--no-sandbox'] : [])],
    });
  } finally {
    backend.kill();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
