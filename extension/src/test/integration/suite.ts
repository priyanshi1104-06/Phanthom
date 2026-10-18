// Runs inside the test VS Code (see run.ts): open samples/buggy.py and check what the user would see.
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

const confirmed = (uri: vscode.Uri) => vscode.languages.getDiagnostics(uri).filter((d) => d.source === 'Phantom');

export async function run(): Promise<void> {
  await vscode.workspace.getConfiguration('phantom').update(
    'backendUrl', process.env.PHANTOM_TEST_BACKEND, vscode.ConfigurationTarget.Global);

  // 1. Open the samples: every bug Phase 1 finds shows up as a confirmed squiggle.
  const uri = vscode.Uri.file(path.join(process.env.PHANTOM_REPO!, 'samples', 'buggy.py'));
  const doc = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(doc);
  const bugs = await waitFor('9 confirmed bugs', () => confirmed(uri), (d) => d.length === 9);
  const average = bugs.find((d) => d.message.includes('average([])'));
  assert.ok(average, 'a squiggle for average([])');
  assert.equal(average.range.start.line, 9); // line 10: return sum(values) / len(values)
  assert.equal(average.severity, vscode.DiagnosticSeverity.Error);

  // 2. A "Replay" link above each function with a confirmed bug.
  const lenses = await vscode.commands.executeCommand<vscode.CodeLens[]>('vscode.executeCodeLensProvider', uri);
  const replays = lenses.filter((lens) => lens.command?.command === 'phantom.replay');
  assert.equal(replays.length, 9);
  assert.ok(replays.some((lens) => lens.command!.title.includes('Replay average([])')));

  // 3. Fix average while "typing" (never saved): its squiggle goes away, the others stay.
  const editor = vscode.window.activeTextEditor!;
  const buggyLine = doc.lineAt(9);
  await editor.edit((edit) => edit.replace(buggyLine.range, '    return sum(values) / len(values) if values else 0.0'));
  await waitFor('average to be fixed', () => confirmed(uri).map((d) => d.message),
    (messages) => messages.length === 8 && !messages.some((m) => m.includes('average(')));
  await vscode.commands.executeCommand('workbench.action.files.revert');

  // 4. Static checks: an undefined name is a (yellow) hint, not a confirmed bug.
  const scratch = await vscode.workspace.openTextDocument({ language: 'python', content: 'def f():\n    return nope\n' });
  await vscode.window.showTextDocument(scratch);
  const hints = await waitFor('a static hint', () => vscode.languages.getDiagnostics(scratch.uri), (d) => d.length > 0);
  assert.equal(hints[0].source, 'Phantom (static)');
  assert.equal(hints[0].severity, vscode.DiagnosticSeverity.Warning);
  assert.match(hints[0].message, /undefined name 'nope'/);
}
