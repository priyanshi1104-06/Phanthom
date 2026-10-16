// Phanthom for VS Code: when you pause typing in a Python file, ask the backend for
//   1. static checks (milliseconds)  -> yellow squiggles, hints only
//   2. a shadow run (seconds)        -> red squiggles, only for failures reproduced in the sandbox
// and show a "Replay" link above each function with a confirmed bug.
import * as vscode from 'vscode';
import {
  bugLine, bugMessage, ConfirmedBug, confirmedBugs, describeReplay, lensTitle, ReplayResult,
  ShadowFinding, StaticFinding, statusText, toZeroBased,
} from './findings';

interface FileState {
  timer?: NodeJS.Timeout;
  controller?: AbortController;
  staticDiagnostics: vscode.Diagnostic[];
  shadowDiagnostics: vscode.Diagnostic[];
  bugs: ConfirmedBug[];
}

const RETRY_MS = 5000;

export function activate(context: vscode.ExtensionContext): void {
  const phanthom = new Phanthom();
  context.subscriptions.push(phanthom);
}

export function deactivate(): void {}

class Phanthom implements vscode.CodeLensProvider, vscode.Disposable {
  private readonly diagnostics = vscode.languages.createDiagnosticCollection('phanthom');
  private readonly output = vscode.window.createOutputChannel('Phanthom');
  private readonly status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left);
  private readonly lensesChanged = new vscode.EventEmitter<void>();
  private readonly files = new Map<string, FileState>();
  private readonly disposables: vscode.Disposable[] = [];
  private running = 0;
  private offline = false;
  private sandbox: string | undefined;

  readonly onDidChangeCodeLenses = this.lensesChanged.event;

  constructor() {
    this.status.command = 'phanthom.showOutput';
    this.disposables.push(
      this.diagnostics, this.output, this.status, this.lensesChanged,
      vscode.languages.registerCodeLensProvider({ language: 'python' }, this),
      vscode.workspace.onDidChangeTextDocument((e) => this.schedule(e.document)),
      vscode.workspace.onDidOpenTextDocument((doc) => this.schedule(doc, 0)),
      vscode.workspace.onDidCloseTextDocument((doc) => this.forget(doc)),
      vscode.window.onDidChangeActiveTextEditor(() => this.updateStatus()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('phanthom')) {
          this.restart();
        }
      }),
      vscode.commands.registerCommand('phanthom.analyze', () => {
        const doc = vscode.window.activeTextEditor?.document;
        if (doc) {
          this.schedule(doc, 0);
        }
      }),
      vscode.commands.registerCommand('phanthom.replay', (uri?: vscode.Uri, bug?: ConfirmedBug) => this.replay(uri, bug)),
      vscode.commands.registerCommand('phanthom.showOutput', () => this.output.show()),
    );
    this.restart();
  }

  dispose(): void {
    for (const state of this.files.values()) {
      clearTimeout(state.timer);
      state.controller?.abort();
    }
    this.disposables.forEach((d) => d.dispose());
  }

  // ---------- when to analyse ----------

  private config() {
    const c = vscode.workspace.getConfiguration('phanthom');
    return {
      enabled: c.get<boolean>('enabled', true),
      backendUrl: c.get<string>('backendUrl', 'http://127.0.0.1:8765').replace(/\/+$/, ''),
      debounceMs: c.get<number>('debounceMs', 700),
      shadowRun: c.get<boolean>('shadowRun', true),
    };
  }

  private restart(): void {
    for (const doc of vscode.workspace.textDocuments) {
      this.forget(doc);
      this.schedule(doc, 0);
    }
    this.updateStatus();
  }

  private schedule(doc: vscode.TextDocument, delay = this.config().debounceMs): void {
    if (doc.languageId !== 'python' || !['file', 'untitled'].includes(doc.uri.scheme) || !this.config().enabled) {
      return;
    }
    const state = this.state(doc);
    clearTimeout(state.timer);
    state.timer = setTimeout(() => void this.analyze(doc), delay);
  }

  private state(doc: vscode.TextDocument): FileState {
    const key = doc.uri.toString();
    let state = this.files.get(key);
    if (!state) {
      state = { staticDiagnostics: [], shadowDiagnostics: [], bugs: [] };
      this.files.set(key, state);
    }
    return state;
  }

  private forget(doc: vscode.TextDocument): void {
    const state = this.files.get(doc.uri.toString());
    if (state) {
      clearTimeout(state.timer);
      state.controller?.abort();
      this.files.delete(doc.uri.toString());
    }
    this.diagnostics.delete(doc.uri);
    this.lensesChanged.fire();
  }

  // ---------- talking to the backend ----------

  private async analyze(doc: vscode.TextDocument): Promise<void> {
    if (doc.isClosed) {
      return;
    }
    const state = this.state(doc);
    state.controller?.abort(); // a newer edit makes the running analysis pointless
    const controller = new AbortController();
    state.controller = controller;
    const version = doc.version;
    const source = doc.getText();
    const stale = () => controller.signal.aborted || doc.isClosed || doc.version !== version;

    this.running++;
    this.updateStatus();
    try {
      const fast = await this.post<{ parsed: boolean; findings: StaticFinding[] }>(
        '/static', { source }, controller.signal);
      if (stale()) {
        return;
      }
      this.offline = false;
      if (fast.parsed) { // unparsable = mid-typing: keep the previous squiggles
        state.staticDiagnostics = fast.findings.map((f) => this.staticDiagnostic(doc, f));
        this.publish(doc);
      }
      if (!this.config().shadowRun) {
        state.shadowDiagnostics = [];
        state.bugs = [];
        this.publish(doc);
        return;
      }
      const shadow = await this.post<{ parsed: boolean; sandbox?: string; findings: ShadowFinding[] }>(
        '/shadow', { source }, controller.signal);
      if (stale() || !shadow.parsed) {
        return;
      }
      this.sandbox = shadow.sandbox;
      state.bugs = confirmedBugs(shadow.findings);
      state.shadowDiagnostics = state.bugs.map((bug) => this.bugDiagnostic(doc, bug));
      this.publish(doc);
      this.log(`${vscode.workspace.asRelativePath(doc.uri)}: ${state.bugs.length} confirmed bug(s)`);
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      if (!this.offline) {
        this.log(`Backend not reachable at ${this.config().backendUrl} (${error}). ` +
          'Start it in a terminal: python -m backend');
      }
      this.offline = true;
      this.schedule(doc, RETRY_MS);
    } finally {
      this.running--;
      this.updateStatus();
    }
  }

  private async post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(this.config().backendUrl + path, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) {
      throw new Error(`${path} answered HTTP ${response.status}`);
    }
    return (await response.json()) as T;
  }

  // ---------- showing results ----------

  private staticDiagnostic(doc: vscode.TextDocument, f: StaticFinding): vscode.Diagnostic {
    const line = toZeroBased(f.line, doc.lineCount);
    const start = new vscode.Position(line, Math.min(f.col, doc.lineAt(line).text.length));
    const range = doc.getWordRangeAtPosition(start) ?? doc.lineAt(line).range;
    const diagnostic = new vscode.Diagnostic(range, f.message, vscode.DiagnosticSeverity.Warning);
    diagnostic.source = 'Phanthom (static)';
    diagnostic.code = f.rule;
    return diagnostic;
  }

  private bugDiagnostic(doc: vscode.TextDocument, bug: ConfirmedBug): vscode.Diagnostic {
    const line = doc.lineAt(bugLine(bug, doc.lineCount));
    const range = new vscode.Range(line.lineNumber, line.firstNonWhitespaceCharacterIndex, line.lineNumber, line.text.length);
    const diagnostic = new vscode.Diagnostic(range, bugMessage(bug), vscode.DiagnosticSeverity.Error);
    diagnostic.source = 'Phanthom';
    diagnostic.code = 'confirmed';
    return diagnostic;
  }

  private publish(doc: vscode.TextDocument): void {
    const state = this.state(doc);
    this.diagnostics.set(doc.uri, [...state.staticDiagnostics, ...state.shadowDiagnostics]);
    this.lensesChanged.fire();
    this.updateStatus();
  }

  provideCodeLenses(doc: vscode.TextDocument): vscode.CodeLens[] {
    const bugs = this.files.get(doc.uri.toString())?.bugs ?? [];
    return bugs.map((bug) => {
      const line = toZeroBased(bug.def_line ?? bug.failure.line ?? 1, doc.lineCount);
      return new vscode.CodeLens(doc.lineAt(line).range, {
        title: lensTitle(bug),
        tooltip: 'Run this failing input again in a fresh sandbox, against the code as it is now',
        command: 'phanthom.replay',
        arguments: [doc.uri, bug],
      });
    });
  }

  private updateStatus(): void {
    const doc = vscode.window.activeTextEditor?.document;
    if (!this.config().enabled || doc?.languageId !== 'python') {
      this.status.hide();
      return;
    }
    if (this.offline) {
      this.status.text = '$(debug-disconnect) Phanthom: backend offline';
      this.status.tooltip = 'Start the backend in a terminal: python -m backend';
    } else if (this.running > 0) {
      this.status.text = '$(sync~spin) Phanthom';
      this.status.tooltip = 'Analysing...';
    } else {
      this.status.text = statusText(this.files.get(doc.uri.toString())?.bugs.length ?? 0, this.sandbox);
      this.status.tooltip = this.sandbox === 'docker'
        ? 'Code runs in the Docker sandbox. Click for the log.'
        : 'Code runs in a local process, NOT the Docker sandbox. Click for the log.';
    }
    this.status.show();
  }

  // ---------- replay ----------

  private async replay(uri?: vscode.Uri, bug?: ConfirmedBug): Promise<void> {
    const doc = uri ? await vscode.workspace.openTextDocument(uri) : vscode.window.activeTextEditor?.document;
    if (!doc) {
      return;
    }
    if (!bug) { // from the command palette: pick one of this file's bugs
      const bugs = this.files.get(doc.uri.toString())?.bugs ?? [];
      const picked = await vscode.window.showQuickPick(
        bugs.map((b) => ({ label: b.input.call, description: `${b.failure.error}: ${b.failure.message}`, bug: b })),
        { placeHolder: bugs.length ? 'Failing input to replay' : 'No confirmed bugs in this file' });
      bug = picked?.bug;
      if (!bug) {
        return;
      }
    }
    const call = bug.input.call;
    try {
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Phanthom: replaying ${call} in a fresh sandbox...` },
        () => this.post<ReplayResult>('/replay', { source: doc.getText(), function: bug.function, args: bug.input.args }));
      const { summary, details } = describeReplay(call, result);
      this.log(`Replay: ${summary}${details ? '\n' + details : ''}`);
      if (result.ok === true) {
        void vscode.window.showInformationMessage(`Phanthom: ${summary}`);
      } else {
        const choice = await vscode.window.showWarningMessage(`Phanthom: ${summary}`, 'Show traceback');
        if (choice) {
          this.output.show(true);
        }
      }
    } catch (error) {
      void vscode.window.showErrorMessage(`Phanthom: replay failed: ${error}. Is the backend running (python -m backend)?`);
    }
    this.schedule(doc, 0);
  }

  private log(message: string): void {
    this.output.appendLine(`[${new Date().toLocaleTimeString()}] ${message}`);
  }
}
