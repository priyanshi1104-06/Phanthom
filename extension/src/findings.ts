// What the backend returns, and how it is worded in the editor. No `vscode` import here,
// so this file can be tested with plain Node.

/** VS Code languageIds Specter handles (the backend's /health lists the same). */
export const SUPPORTED_LANGUAGES = [
  'python', 'javascript', 'typescript', 'go', 'java', 'c', 'cpp', 'rust', 'ruby', 'php',
];

export interface Failure {
  kind: 'exception' | 'timeout' | 'crash' | 'return_type' | string;
  error: string;
  message: string;
  line: number | null;
  traceback?: string;
}

export interface Finding {
  function: string;
  def_line: number | null;
  language?: string;
  status: 'bug' | 'no_bug_found' | 'skipped' | 'error' | string;
  severity?: 'error' | 'warning' | 'info';
  origin?: 'edge' | 'model' | 'fuzz' | string;
  assumptions?: string[];
  examples?: number;
  input?: { call: string; exprs?: string[]; args?: unknown[] | null };
  failure?: Failure;
  reason?: string;
}

/** Phase 2 name, kept for older code. */
export type ShadowFinding = Finding;

export interface StaticFinding {
  line: number;
  col: number;
  rule: string;
  message: string;
}

export interface ConfirmedBug extends Finding {
  status: 'bug';
  severity: 'error' | 'warning' | 'info';
  assumptions: string[];
  input: { call: string; exprs?: string[]; args?: unknown[] };
  failure: Failure;
}

export interface Risk {
  level: 'low' | 'medium' | 'high' | 'critical';
  edit_level: 'low' | 'medium' | 'high' | 'critical';
  changed: string[];
  escalate: 'analyst' | 'oracle' | null;
  reasons: { function?: string; category: string; evidence: string[] }[];
}

/** Only reproduced failures are bugs. Everything else stays out of the editor. */
export function confirmedBugs(findings: Finding[]): ConfirmedBug[] {
  return findings
    .filter((f) => f.status === 'bug' && !!f.failure && !!f.input && typeof f.input.call === 'string')
    .map((f) => ({ ...f, severity: f.severity ?? 'error', assumptions: f.assumptions ?? [] }) as ConfirmedBug);
}

/** error = fails on an input its types allow; warning = needs an assumption; info = only on null. */
export function diagnosticLevel(bug: ConfirmedBug): 'error' | 'warning' | 'hint' {
  return bug.severity === 'info' ? 'hint' : bug.severity;
}

export function bugMessage(bug: ConfirmedBug): string {
  const { failure, input } = bug;
  const label = bug.severity === 'error'
    ? 'Confirmed bug (reproduced)'
    : `Fails if ${bug.assumptions.join(' and ')} (reproduced)`;
  if (failure.kind === 'timeout') {
    return `Possible infinite loop: ${input.call} gave no result within the time limit. ${label}.`;
  }
  if (failure.kind === 'return_type') {
    return `${label}: ${input.call} ${failure.message}.`;
  }
  const verb = failure.kind === 'crash' ? 'crashes with' : 'raises';
  return `${label}: ${input.call} ${verb} ${failure.error}: ${failure.message}`;
}

export function lensTitle(bug: ConfirmedBug): string {
  const icon = bug.severity === 'error' ? '$(bug)' : '$(warning)';
  const what = bug.severity === 'error' ? 'confirmed' : 'assumed';
  return `${icon} Phantom: ${what} ${bug.failure.error}  ▶ Replay ${bug.input.call}`;
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
      summary: `${call} returned ${typeof result.result === 'string' ? result.result : JSON.stringify(result.result)}: ` +
        'the bug no longer reproduces.',
      details: '',
    };
  }
  if (result.ok === false && !['error', 'build_error'].includes(result.kind ?? '') && result.error !== 'SandboxError') {
    const where = result.line ? ` (line ${result.line})` : '';
    return {
      summary: `${call} still fails: ${result.error}: ${result.message}${where}`,
      details: result.traceback ?? '',
    };
  }
  return { summary: `Couldn't replay ${call}: ${result.message ?? 'unknown error'}`, details: result.traceback ?? '' };
}

const LAYER = { analyst: 'Analyst', oracle: 'Oracle' };

/** "risk high → Oracle" for the status bar ("" when low and nothing to escalate). */
export function riskText(risk: Risk | undefined): string {
  if (!risk) {
    return '';
  }
  const escalate = risk.escalate ? ` → ${LAYER[risk.escalate]}` : '';
  return risk.level === 'low' && !escalate ? '' : `risk ${risk.level}${escalate}`;
}

/** Why the edit escalates, for the log: "pay: payments (amount, balance)". */
export function escalationReason(risk: Risk): string {
  return risk.reasons.map((r) => `${r.function ? r.function + ': ' : ''}${r.category} (${r.evidence.join(', ')})`)
    .join('; ');
}

export function statusText(bugs: ConfirmedBug[], sandbox: string | undefined, risk?: Risk): string {
  const errors = bugs.filter((b) => b.severity === 'error').length;
  const warnings = bugs.filter((b) => b.severity === 'warning').length;
  const parts: string[] = [];
  if (errors) {
    parts.push(`${errors} confirmed`);
  }
  if (warnings) {
    parts.push(`${warnings} assumed`);
  }
  const riskPart = riskText(risk);
  if (riskPart) {
    parts.push(riskPart);
  }
  if (sandbox && sandbox !== 'docker') {
    parts.push('not sandboxed');
  }
  const icon = errors ? '$(bug)' : warnings ? '$(warning)' : '$(check)';
  return `${icon} Phantom${parts.length ? ': ' + parts.join(' · ') : ''}`;
}
