// The Phantom backend's life cycle, so users never start it by hand:
//   - start it when the extension needs it (python -m backend, from the bundled server/ folder),
//   - stop it when VS Code closes,
//   - "Phantom: Set Up": a private Python environment, its packages and the sandbox images, once.
// The AI key lives in VS Code's secret storage and reaches the backend as an
// environment variable; it is never written to a file.
import { ChildProcess, spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vscode from 'vscode';

const KEY_SECRET = 'phantom.anthropicApiKey';
const START_TIMEOUT_MS = 30_000;

export class Backend implements vscode.Disposable {
  private process?: ChildProcess;
  private starting?: Promise<boolean>;
  private failure?: string;

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly log: (message: string) => void,
    private readonly url: () => string,
  ) {}

  dispose(): void {
    this.stop();
  }

  /** Why the backend couldn't start, if it couldn't (shown in the status bar). */
  get problem(): string | undefined {
    return this.failure;
  }

  /** The folder with backend/, runner/ and phantom.py: bundled in the .vsix, or the repo in development. */
  serverDir(): string | undefined {
    const configured = vscode.workspace.getConfiguration('phantom').get<string>('serverPath', '');
    const candidates = [configured, path.join(this.context.extensionPath, 'server'), path.join(this.context.extensionPath, '..')];
    return candidates.find((dir) => dir && fs.existsSync(path.join(dir, 'backend', 'app.py')));
  }

  /** The Python that runs the backend: the setting, else the one Set Up created, else python3. */
  python(): string {
    const configured = vscode.workspace.getConfiguration('phantom').get<string>('pythonPath', '');
    if (configured) {
      return configured;
    }
    const private_ = path.join(this.context.globalStorageUri.fsPath, 'venv', 'bin', 'python');
    return fs.existsSync(private_) ? private_ : 'python3';
  }

  async healthy(): Promise<boolean> {
    try {
      const response = await fetch(this.url() + '/health', { signal: AbortSignal.timeout(2000) });
      return response.ok;
    } catch {
      return false;
    }
  }

  /** Make sure a backend answers at the configured address; start ours if it's on this machine. */
  ensure(): Promise<boolean> {
    this.starting ??= this.start().finally(() => { this.starting = undefined; });
    return this.starting;
  }

  private async start(): Promise<boolean> {
    if (await this.healthy()) {
      this.failure = undefined;
      return true;
    }
    const url = new URL(this.url());
    const local = ['127.0.0.1', 'localhost'].includes(url.hostname);
    const dir = this.serverDir();
    if (!local || !vscode.workspace.getConfiguration('phantom').get<boolean>('startBackend', true)) {
      this.failure = `no backend at ${this.url()}`;
      return false;
    }
    if (!dir) {
      this.failure = 'the Phantom server files are missing (reinstall the extension)';
      return false;
    }
    this.stop();
    const key = await this.context.secrets.get(KEY_SECRET);
    const env: NodeJS.ProcessEnv = { ...process.env, PHANTOM_PORT: url.port || '8765', PYTHONDONTWRITEBYTECODE: '1' };
    if (key) {
      env.ANTHROPIC_API_KEY = key;
    }
    this.log(`Starting the backend: ${this.python()} -m backend (in ${dir})`);
    const child = spawn(this.python(), ['-m', 'backend'], { cwd: dir, env, stdio: ['ignore', 'pipe', 'pipe'] });
    this.process = child;
    let output = '';
    const collect = (data: Buffer) => {
      output = (output + data.toString()).slice(-4000);
    };
    child.stdout?.on('data', collect);
    child.stderr?.on('data', collect);
    child.on('error', (error) => { output += String(error); });
    const deadline = Date.now() + START_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (child.exitCode !== null || child.signalCode !== null) {
        break;
      }
      if (await this.healthy()) {
        this.failure = undefined;
        this.log('Backend running.');
        return true;
      }
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    this.log(`The backend did not start:\n${output.trim()}`);
    this.failure = /No module named|ModuleNotFoundError|ENOENT/.test(output)
      ? 'Python packages missing: run "Phantom: Set Up"'
      : 'the backend did not start (see the Phantom output)';
    this.stop();
    return false;
  }

  stop(): void {
    if (this.process && this.process.exitCode === null) {
      this.process.kill();
    }
    this.process = undefined;
  }

  async restart(): Promise<boolean> {
    this.stop();
    await new Promise((resolve) => setTimeout(resolve, 500));
    return this.ensure();
  }

  /** One-time setup in a terminal the user can watch: private venv, packages, sandbox images. */
  async setUp(): Promise<boolean> {
    const dir = this.serverDir();
    if (!dir) {
      void vscode.window.showErrorMessage('Phantom: the server files are missing; reinstall the extension.');
      return false;
    }
    const venv = path.join(this.context.globalStorageUri.fsPath, 'venv');
    fs.mkdirSync(this.context.globalStorageUri.fsPath, { recursive: true });
    const q = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
    const python = path.join(venv, 'bin', 'python');
    const script = [
      `python3 -m venv ${q(venv)}`,
      `${q(python)} -m pip install --upgrade pip`,
      `${q(python)} -m pip install -r ${q(path.join(dir, 'requirements.txt'))}`,
      `cd ${q(dir)} && ${q(python)} phantom.py setup`,
    ].join(' && ');
    const task = new vscode.Task({ type: 'phantom-setup' }, vscode.TaskScope.Global, 'Set Up', 'Phantom',
      new vscode.ShellExecution(script));
    const execution = await vscode.tasks.executeTask(task);
    const code = await new Promise<number | undefined>((resolve) => {
      const done = vscode.tasks.onDidEndTaskProcess((e) => {
        if (e.execution === execution) {
          done.dispose();
          resolve(e.exitCode);
        }
      });
    });
    if (code !== 0) {
      void vscode.window.showErrorMessage('Phantom: setup failed; see the terminal. Python 3 and Docker are needed.');
      return false;
    }
    if (!(await this.context.secrets.get(KEY_SECRET))) {
      await this.askForKey();
    }
    await this.restart();
    void vscode.window.showInformationMessage('Phantom is set up. Open any supported file.');
    return true;
  }

  /** Store (or clear) the AI key in VS Code's secret storage, then restart the backend. */
  async askForKey(): Promise<void> {
    const key = await vscode.window.showInputBox({
      title: 'Phantom AI key',
      prompt: 'Lets Phantom\'s AI suggest extra inputs and rate risk. Press Enter with nothing typed to use Phantom\'s engine only.',
      password: true,
      ignoreFocusOut: true,
      placeHolder: 'paste your key, or leave empty',
    });
    if (key === undefined) {
      return;
    }
    if (key.trim()) {
      await this.context.secrets.store(KEY_SECRET, key.trim());
    } else {
      await this.context.secrets.delete(KEY_SECRET);
    }
    await this.restart();
  }
}
