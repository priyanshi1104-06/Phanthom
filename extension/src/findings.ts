// What the backend returns, and how it is worded in the editor. No `vscode` import here,
// so this file can be tested with plain Node.

export interface Failure {
  kind: 'exception' | 'timeout' | 'return_type' | string;
  error: string;
  message: string;
  line: number | null;
  traceback?: string;
}

export interface ShadowFinding {
  function: string;
  def_line: number | null;
  status: 'bug' | 'unconfirmed' | 'no_bug_found' | 'skipped' | 'error' | string;
  examples?: number;
  input?: { args: unknown[] | null; call: string };
  failure?: Failure;
  reason?: string;
}

export interface StaticFinding {
  line: number;
  col: number;
  rule: string;
  message: string;
}

export interface ConfirmedBug extends ShadowFinding {
  status: 'bug';
  input: { args: unknown[]; call: string };
  failure: Failure;
}

/** Only reproduced failures are bugs. Everything else stays out of the editor. */
export function confirmedBugs(findings: ShadowFinding[]): ConfirmedBug[] {
  return findings.filter(
    (f): f is ConfirmedBug => f.status === 'bug' && !!f.failure && !!f.input && Array.isArray(f.input.args),
  );
}

export function bugMessage(bug: ConfirmedBug): string {
  const { failure, input } = bug;
  if (failure.kind === 'timeout') {
    return `Possible infinite loop (reproduced): ${input.call} gave no result within the time limit.`;
  }
  if (failure.kind === 'return_type') {
    return `Confirmed bug (reproduced): ${input.call} ${failure.message}.`;
  }
  return `Confirmed bug (reproduced): ${input.call} raises ${failure.error}: ${failure.message}`;
}

export function lensTitle(bug: ConfirmedBug): string {
  return `$(bug) Phantom: confirmed ${bug.failure.error}  ▶ Replay ${bug.input.call}`;
}

/** Where to put the squiggle: the failing line, else the def line; 0-based, inside the document. */
export function bugLine(bug: ConfirmedBug, lineCount: number): number {
  return toZeroBased(bug.failure.line ?? bug.def_line ?? 1, lineCount);
}

export function toZeroBased(line: number, lineCount: number): number {
  return Math.min(Math.max(line - 1, 0), Math.max(lineCount - 1, 0));
}

export interface ReplayResult {
  ok: boolean | null;
  result?: unknown;
  kind?: string;
  error?: string;
  message?: string;
  line?: number | null;
  traceback?: string;
}

/** One-line verdict for a replay, plus details for the output panel. */
export function describeReplay(call: string, result: ReplayResult): { summary: string; details: string } {
  if (result.ok === true) {
    return {
      summary: `${call} returned ${JSON.stringify(result.result)}: the bug no longer reproduces.`,
      details: '',
    };
  }
  if (result.ok === false && result.kind !== 'error' && result.kind !== 'crash') {
    const where = result.line ? ` (line ${result.line})` : '';
    return {
      summary: `${call} still fails: ${result.error}: ${result.message}${where}`,
      details: result.traceback ?? '',
    };
  }
  return { summary: `Couldn't replay ${call}: ${result.message ?? 'unknown error'}`, details: result.traceback ?? '' };
}

export function statusText(bugs: number, sandbox: string | undefined): string {
  const where = sandbox === 'docker' ? 'sandboxed' : 'not sandboxed';
  if (bugs === 0) {
    return `$(check) Phantom (${where})`;
  }
  return `$(bug) Phantom: ${bugs} confirmed (${where})`;
}
