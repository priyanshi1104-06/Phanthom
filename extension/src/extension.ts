// Phantom for VS Code: when you pause typing in a supported file, ask the backend for
//   1. static checks (Python only, milliseconds)     -> yellow squiggles, hints only
//   2. Specter's triage (milliseconds)               -> risk of the edit in the status bar
//   3. Specter's run (seconds)                       -> squiggles only for failures reproduced in the sandbox:
//        red = fails on an input its types allow, yellow = needs an assumption, faint dots = only on null
// and show a "Replay" link above each function with a confirmed or assumed bug.
// The extension starts its own backend (server.ts); a second status item picks the depth and the models.
import * as vscode from 'vscode';
import {
  bugLine, bugMessage, chosenTiers, comingIn, ConfirmedBug, confirmedBugs, DEPTHS, depthText, describeReplay,
  diagnosticLevel, escalationReason, Finding, Layer, LAYER_NAMES, lensTitle, panelView, ReplayResult,
  Risk, StaticFinding, statusText, SUPPORTED_LANGUAGES, TIERS, tierLabel, toZeroBased,
} from './findings';
import { PanelAction, PhantomPanel } from './panel';
import { Backend } from './server';

interface FileState {
  timer?: NodeJS.Timeout;
  controller?: AbortController;
  staticDiagnostics: vscode.Diagnostic[];
  bugDiagnostics: vscode.Diagnostic[];
  bugs: ConfirmedBug[];
  findings?: Finding[];  // everything the last run said, for the side panel
  risk?: Risk;
  sandbox?: string;
  setup?: string;
  escalated?: string;
}

interface SpecterAnswer {
  parsed: boolean;
  supported?: boolean;
  sandbox?: string;
  setup?: string;
  risk?: Risk;
  findings: Finding[];
}

const RETRY_MS = 5000;
const SEVERITY = {
  error: vscode.DiagnosticSeverity.Error,
  warning: vscode.DiagnosticSeverity.Warning,
  hint: vscode.DiagnosticSeverity.Hint,
};

export function activate(context: vscode.ExtensionContext): void {
  const phantom = new Phantom(context);
  context.subscriptions.push(phantom);
}

export function deactivate(): void {}

class Phantom implements vscode.CodeLensProvider, vscode.Disposable {
  private readonly diagnostics = vscode.languages.createDiagnosticCollection('phantom');
  private readonly output = vscode.window.createOutputChannel('Phantom');
  private readonly status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left);
  private readonly depthStatus = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left);
  private readonly server: Backend;
  private readonly panel = new PhantomPanel((action) => void this.onPanelAction(action));
  private hasModels = false;
  private readonly lensesChanged = new vscode.EventEmitter<void>();
  private readonly files = new Map<string, FileState>();
  private readonly disposables: vscode.Disposable[] = [];
  private running = 0;
  private offline = false;

  readonly onDidChangeCodeLenses = this.lensesChanged.event;

  constructor(context: vscode.ExtensionContext) {
    this.status.command = 'phantom.showOutput';
    this.depthStatus.command = 'phantom.chooseDepth';
    this.server = new Backend(context, (message) => this.log(message), () => this.config().backendUrl);
    this.disposables.push(
      this.diagnostics, this.output, this.status, this.depthStatus, this.lensesChanged, this.server, this.panel,
      vscode.window.registerWebviewViewProvider(PhantomPanel.id, this.panel),
      vscode.commands.registerCommand('phantom.setUp', () => this.server.setUp().then(() => this.afterStart())),
      vscode.commands.registerCommand('phantom.setApiKey', () => this.server.askForKey().then(() => this.afterStart())),
      vscode.commands.registerCommand('phantom.restartBackend', () => this.server.restart().then(() => this.afterStart())),
      vscode.commands.registerCommand('phantom.chooseDepth', () => this.chooseDepth()),
      vscode.commands.registerCommand('phantom.chooseModel', (layer?: Layer) => this.chooseTier(layer)),
      vscode.languages.registerCodeLensProvider(SUPPORTED_LANGUAGES.map((language) => ({ language })), this),
      vscode.workspace.onDidChangeTextDocument((e) => this.schedule(e.document)),
      vscode.workspace.onDidOpenTextDocument((doc) => this.schedule(doc, 0)),
      vscode.workspace.onDidCloseTextDocument((doc) => this.forget(doc)),
      vscode.window.onDidChangeActiveTextEditor(() => this.updateStatus()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration('phantom')) {
          this.restart();
        }
      }),
      vscode.commands.registerCommand('phantom.analyze', () => {
        const doc = vscode.window.activeTextEditor?.document;
        if (doc) {
          this.schedule(doc, 0);
        }
      }),
      vscode.commands.registerCommand('phantom.replay', (uri?: vscode.Uri, bug?: ConfirmedBug) => this.replay(uri, bug)),
      vscode.commands.registerCommand('phantom.showOutput', () => this.output.show()),
    );
    this.restart();
    void this.server.ensure().then(() => this.afterStart());
  }

  /** After the backend (re)started: learn whether models can be used, then analyse open files again. */
  private async afterStart(): Promise<void> {
    try {
      const health = await (await fetch(this.config().backendUrl + '/health')).json() as { model?: { available?: boolean } };
      this.hasModels = !!health.model?.available;
      this.offline = false;
    } catch {
      this.hasModels = false;
    }
    this.restart();
  }

  // ---------- depth and models ----------

  private async chooseDepth(): Promise<void> {
    const config = this.config();
    type Item = vscode.QuickPickItem & { run: () => Thenable<unknown> | void };
    const items: Item[] = DEPTHS.map((depth) => ({
      label: `${depth.id === config.depth ? '$(check) ' : ''}${depth.label}`,
      description: comingIn(depth) ?? '',
      detail: depth.detail,
      run: () => comingIn(depth)
        ? vscode.window.showInformationMessage(`Phantom: ${depth.label} is ${comingIn(depth)}, an update of this extension.`)
        : vscode.workspace.getConfiguration('phantom').update('depth', depth.id, vscode.ConfigurationTarget.Global),
    }));
    items.push({ label: '', kind: vscode.QuickPickItemKind.Separator, run: () => undefined });
    for (const layer of Object.keys(LAYER_NAMES) as Layer[]) {
      items.push({
        label: `$(sparkle) ${LAYER_NAMES[layer]}: ${tierLabel(config.tiers[layer])}`,
        description: layer === 'specter' ? '' : `used from ${layer === 'analyst' ? '0.2' : '0.3'}`,
        run: () => this.chooseTier(layer),
      });
    }
    items.push({ label: this.hasModels ? '$(key) Change the AI key' : '$(key) Add an AI key (now: Phantom\'s engine only)',
      run: () => vscode.commands.executeCommand('phantom.setApiKey') });
    const picked = await vscode.window.showQuickPick(items, { title: 'Phantom: depth and tiers' });
    await picked?.run();
  }

  private async chooseTier(layer?: Layer): Promise<void> {
    const chosen = layer ?? (await vscode.window.showQuickPick(
      (Object.keys(LAYER_NAMES) as Layer[]).map((l) => ({ label: LAYER_NAMES[l], layer: l })),
      { title: 'Phantom: tier for which layer?' }))?.layer;
    if (!chosen) {
      return;
    }
    const current = this.config().tiers[chosen];
    const picked = await vscode.window.showQuickPick(
      TIERS.map((t) => ({ label: `${t.id === current ? '$(check) ' : ''}${t.label}`, description: t.detail, id: t.id })),
      { title: `Phantom: tier for ${LAYER_NAMES[chosen]}` });
    if (picked) {
      await vscode.workspace.getConfiguration('phantom').update(`tiers.${chosen}`, picked.id, vscode.ConfigurationTarget.Global);
    }
  }

  // ---------- the side panel ----------

  private refreshPanel(): void {
    const doc = vscode.window.activeTextEditor?.document;
    const config = this.config();
    const state = doc && this.supported(doc) ? this.files.get(doc.uri.toString()) : undefined;
    const file = doc && this.supported(doc) ? vscode.workspace.asRelativePath(doc.uri) : '';
    const view = doc && this.supported(doc)
      ? panelView(file, { findings: state?.findings, running: this.running > 0, offline: this.offline, setup: state?.setup,
        problem: this.server.problem, risk: state?.risk })
      : { title: 'Phantom', risk: '', cards: [], message: 'Open a file in a language I know and I\'ll check it as you type.' };
    const depth = DEPTHS.find((d) => d.id === config.depth)?.label ?? 'Auto';
    this.panel.show(view, depth, config.tiers.specter);
  }

  private async onPanelAction(action: PanelAction): Promise<void> {
    if (action.type === 'depth') {
      return this.chooseDepth();
    }
    if (action.type === 'setUp') {
      return void vscode.commands.executeCommand('phantom.setUp');
    }
    const editor = vscode.window.activeTextEditor;
    const state = editor ? this.files.get(editor.document.uri.toString()) : undefined;
    const view = editor && state ? panelView('', { findings: state.findings }) : undefined;
    if (!('index' in action)) {
      return;
    }
    const index = action.index;
    const card = view?.cards.find((c) => c.index === index);
    const bug = card ? state!.bugs.find((b) => b.function === card.function && b.input.call === card.call) : undefined;
    if (!editor || !bug) {
      return;
    }
    if (action.type === 'reveal') {
      const line = editor.document.lineAt(bugLine(bug, editor.document.lineCount));
      editor.selection = new vscode.Selection(line.range.start, line.range.start);
      editor.revealRange(line.range, vscode.TextEditorRevealType.InCenter);
      await vscode.window.showTextDocument(editor.document, editor.viewColumn);
    } else {
      await this.replay(editor.document.uri, bug);
    }
  }

  private updateDepthStatus(): void {
    const config = this.config();
    this.depthStatus.text = depthText(config.depth, config.tiers, this.hasModels);
    this.depthStatus.tooltip = 'Phantom: choose the depth and the tier of each layer';
    this.depthStatus.show();
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
    const c = vscode.workspace.getConfiguration('phantom');
    return {
      enabled: c.get<boolean>('enabled', true),
      backendUrl: c.get<string>('backendUrl', 'http://127.0.0.1:8765').replace(/\/+$/, ''),
      debounceMs: c.get<number>('debounceMs', 700),
      shadowRun: c.get<boolean>('shadowRun', true),
      useModel: c.get<boolean>('useModel', true),
      depth: c.get<string>('depth', 'auto'),
      tiers: chosenTiers({
        specter: c.get<string>('tiers.specter'), analyst: c.get<string>('tiers.analyst'), oracle: c.get<string>('tiers.oracle'),
      }),
    };
  }

  private supported(doc: vscode.TextDocument): boolean {
    return SUPPORTED_LANGUAGES.includes(doc.languageId) && ['file', 'untitled'].includes(doc.uri.scheme);
  }

  private restart(): void {
    for (const doc of vscode.workspace.textDocuments) {
      this.forget(doc);
      this.schedule(doc, 0);
    }
    this.updateStatus();
  }

  private schedule(doc: vscode.TextDocument, delay = this.config().debounceMs): void {
    if (!this.supported(doc) || !this.config().enabled) {
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
      state = { staticDiagnostics: [], bugDiagnostics: [], bugs: [] };
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

  private request(doc: vscode.TextDocument) {
    return {
      source: doc.getText(),
      language: doc.languageId,
      path: doc.uri.scheme === 'file' ? doc.uri.fsPath : undefined,
    };
  }

  private async analyze(doc: vscode.TextDocument): Promise<void> {
    if (doc.isClosed) {
      return;
    }
    const state = this.state(doc);
    state.controller?.abort(); // a newer edit makes the running analysis pointless
    const controller = new AbortController();
    state.controller = controller;
    const version = doc.version;
    const body = this.request(doc);
    const stale = () => controller.signal.aborted || doc.isClosed || doc.version !== version;

    this.running++;
    this.updateStatus();
    try {
      if (doc.languageId === 'python') {
        const fast = await this.post<{ parsed: boolean; findings: StaticFinding[] }>('/static', body, controller.signal);
        if (stale()) {
          return;
        }
        if (fast.parsed) { // unparsable = mid-typing: keep the previous squiggles
          state.staticDiagnostics = fast.findings.map((f) => this.staticDiagnostic(doc, f));
          this.publish(doc);
        }
      }
      const triage = await this.post<{ parsed: boolean; risk?: Risk }>('/triage', body, controller.signal);
      if (stale()) {
        return;
      }
      this.offline = false;
      if (triage.parsed && triage.risk) {
        state.risk = triage.risk;
        this.noteEscalation(doc, state);
        this.updateStatus();
      }
      if (!this.config().shadowRun) {
        state.bugDiagnostics = [];
        state.bugs = [];
        this.publish(doc);
        return;
      }
      const answer = await this.post<SpecterAnswer>(
        '/specter', { ...body, model: this.config().useModel, models: this.config().tiers }, controller.signal);
      if (stale() || !answer.parsed) {
        return;
      }
      state.sandbox = answer.sandbox;
      state.setup = answer.setup;
      state.risk = answer.risk ?? state.risk;
      state.findings = answer.findings;
      state.bugs = confirmedBugs(answer.findings);
      state.bugDiagnostics = state.bugs.map((bug) => this.bugDiagnostic(doc, bug));
      this.publish(doc);
      const errors = state.bugs.filter((b) => b.severity === 'error').length;
      this.log(`${vscode.workspace.asRelativePath(doc.uri)}: ${errors} confirmed, ` +
        `${state.bugs.length - errors} under an assumption` +
        (answer.setup ? ` (no ${doc.languageId} sandbox yet: run  ${answer.setup})` : ''));
    } catch (error) {
      if (controller.signal.aborted) {
        return;
      }
      if (!this.offline && await this.server.ensure()) {
        this.schedule(doc, 0);  // it was just (re)started: try again at once
        return;
      }
      if (!this.offline) {
        this.log(`Backend not reachable at ${this.config().backendUrl} (${error}). ${this.server.problem ?? ''}`);
      }
      this.offline = true;
      this.schedule(doc, RETRY_MS);
    } finally {
      this.running--;
      this.updateStatus();
    }
  }

  /** Log once per edit that needs a deeper layer (Analyst and Oracle are the next layers to build). */
  private noteEscalation(doc: vscode.TextDocument, state: FileState): void {
    const risk = state.risk;
    const key = risk?.escalate ? `${risk.escalate}:${risk.changed.join(',')}` : undefined;
    if (risk?.escalate && key !== state.escalated) {
      this.log(`${vscode.workspace.asRelativePath(doc.uri)}: this edit is ${risk.edit_level} risk ` +
        `(${escalationReason(risk)}) → ${risk.escalate === 'oracle' ? 'Oracle' : 'Analyst'} should look`);
    }
    state.escalated = key;
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
    diagnostic.source = 'Phantom (static)';
    diagnostic.code = f.rule;
    return diagnostic;
  }

  private bugDiagnostic(doc: vscode.TextDocument, bug: ConfirmedBug): vscode.Diagnostic {
    const line = doc.lineAt(bugLine(bug, doc.lineCount));
    const range = new vscode.Range(line.lineNumber, line.firstNonWhitespaceCharacterIndex, line.lineNumber, line.text.length);
    const level = diagnosticLevel(bug);
    const diagnostic = new vscode.Diagnostic(range, bugMessage(bug), SEVERITY[level]);
    diagnostic.source = 'Phantom';
    diagnostic.code = level === 'error' ? 'confirmed' : 'assumed';
    return diagnostic;
  }

  private publish(doc: vscode.TextDocument): void {
    const state = this.state(doc);
    this.diagnostics.set(doc.uri, [...state.staticDiagnostics, ...state.bugDiagnostics]);
    this.lensesChanged.fire();
    this.updateStatus();
  }

  provideCodeLenses(doc: vscode.TextDocument): vscode.CodeLens[] {
    const bugs = (this.files.get(doc.uri.toString())?.bugs ?? []).filter((bug) => bug.severity !== 'info');
    return bugs.map((bug) => {
      const line = toZeroBased(bug.def_line ?? bug.failure.line ?? 1, doc.lineCount);
      return new vscode.CodeLens(doc.lineAt(line).range, {
        title: lensTitle(bug),
        tooltip: 'Run this failing input again in a fresh sandbox, against the code as it is now',
        command: 'phantom.replay',
        arguments: [doc.uri, bug],
      });
    });
  }

  private updateStatus(): void {
    this.refreshPanel();
    const doc = vscode.window.activeTextEditor?.document;
    if (!this.config().enabled || !doc || !this.supported(doc)) {
      this.status.hide();
      this.depthStatus.hide();
      return;
    }
    this.updateDepthStatus();
    const state = this.files.get(doc.uri.toString());
    if (this.offline) {
      this.status.text = '$(debug-disconnect) Phantom: backend offline';
      this.status.tooltip = `${this.server.problem ?? 'The backend is not running.'} Click for the log; ` +
        'run "Phantom: Set Up" once, or "Phantom: Restart Backend".';
    } else if (state?.setup) {
      this.status.text = `$(tools) Phantom: ${doc.languageId} not set up`;
      this.status.tooltip = `No sandbox for this language yet. In a terminal: ${state.setup}`;
    } else if (this.running > 0) {
      this.status.text = '$(sync~spin) Phantom';
      this.status.tooltip = 'Analysing...';
    } else {
      this.status.text = statusText(state?.bugs ?? [], state?.sandbox, state?.risk);
      const where = !state?.sandbox || state.sandbox === 'docker'
        ? 'Code runs in the Docker sandbox.'
        : 'Code runs in a local process, NOT the Docker sandbox.';
      const risk = state?.risk?.reasons.length ? `\nRisk: ${escalationReason(state.risk)}` : '';
      this.status.tooltip = `${where}${risk}\nClick for the log.`;
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
        { placeHolder: bugs.length ? 'Failing input to replay' : 'No reproduced bugs in this file' });
      bug = picked?.bug;
      if (!bug) {
        return;
      }
    }
    const call = bug.input.call;
    try {
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: `Phantom: replaying ${call} in a fresh sandbox...` },
        () => this.post<ReplayResult>('/replay', { ...this.request(doc), function: bug.function, input: bug.input }));
      const { summary, details } = describeReplay(call, result);
      this.log(`Replay: ${summary}${details ? '\n' + details : ''}`);
      if (result.ok === true) {
        void vscode.window.showInformationMessage(`Phantom: ${summary}`);
      } else {
        const choice = await vscode.window.showWarningMessage(`Phantom: ${summary}`, 'Show traceback');
        if (choice) {
          this.output.show(true);
        }
      }
    } catch (error) {
      void vscode.window.showErrorMessage(`Phantom: replay failed: ${error}. Is the backend running (python -m backend)?`);
    }
    this.schedule(doc, 0);
  }

  private log(message: string): void {
    this.output.appendLine(`[${new Date().toLocaleTimeString()}] ${message}`);
  }
}
