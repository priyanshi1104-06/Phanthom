// Runs inside the test VS Code (see run.ts): open the samples and check what the user would see.
import { strict as assert } from 'node:assert';
import * as path from 'node:path';
import * as vscode from 'vscode';

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitFor<T>(what: string, get: () => T | Promise<T>, done: (value: T) => boolean): Promise<T> {
  const deadline = Date.now() + 120_000;
  let value = await get();
  while (!done(value)) {
    if (Date.now() > deadline) {
      throw new Error(`timed out waiting for ${what}; last value: ${JSON.stringify(value)}`);
    }
    await sleep(250);
    value = await get();
  }
  return value;
}

const confirmed = (uri: vscode.Uri) => vscode.languages.getDiagnostics(uri)
  .filter((d) => d.source === 'Phantom' && d.severity === vscode.DiagnosticSeverity.Error);
const PYTHON_BUGS = ['average', 'split_bill', 'pairwise_diffs', 'find_index', 'display_name', 'parse_port', 'initials',
  'collatz_steps', 'factorial'];
const hasAll = (messages: string[], names: string[]) => names.every((n) => messages.some((m) => m.includes(`: ${n}(`)));

export async function run(): Promise<void> {
  await vscode.workspace.getConfiguration('phantom').update(
    'backendUrl', process.env.PHANTOM_TEST_BACKEND, vscode.ConfigurationTarget.Global);
  await vscode.workspace.getConfiguration('phantom').update('useModel', false, vscode.ConfigurationTarget.Global);
  if (process.env.PHANTOM_TEST_PYTHON) {  // the packaged extension starts its own backend with this Python
    await vscode.workspace.getConfiguration('phantom').update(
      'pythonPath', process.env.PHANTOM_TEST_PYTHON, vscode.ConfigurationTarget.Global);
  }

  // 1. Open the Python samples: every bug Specter reproduces shows up as a red squiggle.
  const uri = vscode.Uri.file(path.join(process.env.PHANTOM_REPO!, 'samples', 'crashes.py'));
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  const bugs = await waitFor('9 functions with confirmed bugs', () => confirmed(uri),
    (d) => hasAll(d.map((x) => x.message), PYTHON_BUGS));
  assert.ok(!bugs.some((d) => d.message.includes('is_leap_year(')), 'is_leap_year needs an intent check');
  const average = bugs.find((d) => d.message.includes('average([])'));
  assert.ok(average, 'a squiggle for average([])');
  assert.equal(average.range.start.line, 9); // line 10: return sum(values) / len(values)

  // The Phantom panel and its commands are there (the panel shows what Phantom found in this file).
  await vscode.commands.executeCommand('workbench.view.extension.phantom');
  const commands = await vscode.commands.getCommands(true);
  for (const command of ['phantom.setUp', 'phantom.chooseDepth', 'phantom.setApiKey', 'phantom.restartBackend']) {
    assert.ok(commands.includes(command), command);
  }
  await vscode.window.showTextDocument(doc);

  // 2. A "Replay" link for each reproduced bug.
  const lenses = await vscode.commands.executeCommand<vscode.CodeLens[]>('vscode.executeCodeLensProvider', uri);
  const replays = lenses.filter((lens) => lens.command?.command === 'phantom.replay');
  assert.equal(replays.length, bugs.length);
  assert.ok(replays.some((lens) => lens.command!.title.includes('Replay average([])')));

  // 3. Fix average while "typing" (never saved): its squiggle goes away, the others stay.
  const editor = vscode.window.activeTextEditor!;
  const buggyLine = doc.lineAt(9);
  await editor.edit((edit) => edit.replace(buggyLine.range, '    return sum(values) / len(values) if values else 0.0'));
  await waitFor('average to be fixed', () => confirmed(uri).map((d) => d.message),
    (messages) => !messages.some((m) => m.includes('average(')) && hasAll(messages, PYTHON_BUGS.slice(1)));
  await vscode.commands.executeCommand('workbench.action.files.revert');

  // 4. Static checks: an undefined name is a (yellow) hint, not a confirmed bug.
  const scratch = await vscode.workspace.openTextDocument({ language: 'python', content: 'def f():\n    return nope\n' });
  await vscode.window.showTextDocument(scratch);
  const hints = await waitFor('a static hint', () => vscode.languages.getDiagnostics(scratch.uri), (d) => d.length > 0);
  assert.equal(hints[0].source, 'Phantom (static)');
  assert.equal(hints[0].severity, vscode.DiagnosticSeverity.Warning);
  assert.match(hints[0].message, /undefined name 'nope'/);

  // 5. Another language: JavaScript. Red for inputs any caller could pass, faint hints for "fails on null".
  const js = vscode.Uri.file(path.join(process.env.PHANTOM_REPO!, 'samples', 'javascript', 'crashes.js'));
  await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(js));
  const jsBugs = await waitFor('JavaScript bugs', () => confirmed(js),
    (d) => hasAll(d.map((x) => x.message), ['average', 'firstWord', 'lastPrice', 'padLabel', 'parsePort', 'collatzSteps', 'factorial']));
  assert.equal(jsBugs.find((d) => d.message.includes('average([])'))!.range.start.line, 7);
  if (process.env.PHANTOM_TEST_SCREENSHOT) {  // for the docs: the Phantom panel next to crashes.js
    await vscode.commands.executeCommand('workbench.view.extension.phantom');
    await sleep(4000);
    require('node:child_process').execFileSync('import', ['-window', 'root', process.env.PHANTOM_TEST_SCREENSHOT]);
  }
  const nullHints = vscode.languages.getDiagnostics(js).filter((d) => d.severity === vscode.DiagnosticSeverity.Hint);
  assert.ok(nullHints.some((d) => d.message.startsWith('Fails if text may be null')), 'a null-only hint');

  // 6. Analyst ("Up to Analyst"): wrong results, from the code's own examples, with a Replay link each.
  await vscode.workspace.getConfiguration('phantom').update('depth', 'analyst', vscode.ConfigurationTarget.Global);
  const intent = vscode.Uri.file(path.join(process.env.PHANTOM_REPO!, 'samples', 'intent', 'intent.py'));
  await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(intent));
  const analyst = () => vscode.languages.getDiagnostics(intent).filter((d) => d.source === 'Phantom Analyst');
  const wrong = await waitFor('three wrong results', analyst, (d) => d.length === 3);
  assert.ok(wrong.some((d) => d.message.startsWith('Wrong result (reproduced): is_leap_year(1900) returned True, expected False')));
  assert.ok(wrong.every((d) => d.severity === vscode.DiagnosticSeverity.Error), 'the code\'s own examples are errors');
  const intentLenses = await vscode.commands.executeCommand<vscode.CodeLens[]>('vscode.executeCodeLensProvider', intent);
  assert.ok(intentLenses.some((lens) => lens.command?.title.includes('wrong result, expected False')));
  await vscode.workspace.getConfiguration('phantom').update('depth', undefined, vscode.ConfigurationTarget.Global);
}
