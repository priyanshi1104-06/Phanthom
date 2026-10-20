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

// ---------- depth and models (the pickers in the status bar) ----------

export type Layer = 'specter' | 'analyst' | 'oracle';
export const LAYER_NAMES: Record<Layer, string> = { specter: 'Specter', analyst: 'Analyst', oracle: 'Oracle' };
export const BUILT_LAYERS: Layer[] = ['specter'];  // the others arrive in updates of this same extension
/** Phantom's tiers; which model sits behind each one is decided on the Phantom server. */
export const TIERS = [
  { id: 'swift', label: 'Swift', detail: 'fastest' },
  { id: 'balanced', label: 'Balanced', detail: 'deeper, a little slower' },
  { id: 'deep', label: 'Deep', detail: 'for tricky code' },
  { id: 'deepest', label: 'Deepest', detail: 'slowest, most thorough' },
];
export const DEFAULT_TIERS: Record<Layer, string> = { specter: 'swift', analyst: 'balanced', oracle: 'deepest' };

export interface Depth { id: 'auto' | Layer; label: string; detail: string; needs: Layer[] }
export const DEPTHS: Depth[] = [
  { id: 'auto', label: 'Auto', detail: 'Specter always; deeper layers only when an edit is risky', needs: ['specter'] },
  { id: 'specter', label: 'Specter only', detail: 'fast checks on every pause in typing', needs: ['specter'] },
  { id: 'analyst', label: 'Up to Analyst', detail: 'also checks intent: wrong answers, broken callers', needs: ['analyst'] },
  { id: 'oracle', label: 'Up to Oracle', detail: 'the deepest: writes tests, proves fixes', needs: ['oracle'] },
];

/** "coming in 0.2" for a depth whose layer isn't built yet, else undefined. */
export function comingIn(depth: Depth): string | undefined {
  const missing = depth.needs.find((layer) => !BUILT_LAYERS.includes(layer));
  return missing ? `coming in ${missing === 'analyst' ? '0.2' : '0.3'}` : undefined;
}

export function tierLabel(id: string | undefined): string {
  return TIERS.find((t) => t.id === id)?.label ?? DEFAULT_TIERS.specter;
}

/** Tier per layer from the user's settings, defaults filled in for anything unknown. */
export function chosenTiers(settings: Partial<Record<Layer, string>>): Record<Layer, string> {
  const pick = (layer: Layer) => TIERS.some((t) => t.id === settings[layer]) ? settings[layer]! : DEFAULT_TIERS[layer];
  return { specter: pick('specter'), analyst: pick('analyst'), oracle: pick('oracle') };
}

/** The depth/tier status item: "$(layers) Auto · Specter Swift" (no AI: "engine only"). */
export function depthText(depthId: string, tiers: Record<Layer, string>, hasModels: boolean): string {
  const depth = DEPTHS.find((d) => d.id === depthId) ?? DEPTHS[0];
  return `$(layers) ${depth.label} · ${hasModels ? `Specter ${tierLabel(tiers.specter)}` : 'engine only'}`;
}

// ---------- the side panel: Phantom talking about the active file ----------

export const ORIGINS: Record<string, string> = {
  edge: "Found by Phantom's boundary inputs",
  fuzz: "Found by Phantom's fuzzer",
  model: "Suggested by Phantom's AI",
};

export interface PanelCard {
  index: number; function: string; call: string; line: number | null; severity: 'error' | 'warning' | 'info';
  headline: string; why: string; how: string; assumptions: string[];
}

export interface PanelView { title: string; message: string; cards: PanelCard[]; risk: string }

const SEVERITY_HEADLINE = { error: 'Confirmed bug', warning: 'Fails under an assumption', info: 'Only fails on null' };

/** What the panel says about one file: a sentence in Phantom's voice plus one card per reproduced failure. */
export function panelView(file: string, state: {
  findings?: Finding[]; running?: boolean; offline?: boolean; setup?: string; problem?: string; risk?: Risk;
}): PanelView {
  const title = file || 'Phantom';
  const risk = riskText(state.risk);
  if (state.offline) {
    return { title, risk, cards: [], message: `I can't reach my backend. ${state.problem ?? 'Run "Phantom: Set Up" once.'}` };
  }
  if (state.setup) {
    return { title, risk, cards: [], message: `I need a sandbox for this language first: run "Phantom: Set Up".` };
  }
  if (!state.findings) {
    return { title, risk, cards: [], message: state.running ? 'Checking the functions you changed…' : 'Pause typing and I\'ll check this file.' };
  }
  const bugs = confirmedBugs(state.findings);
  const functions = new Set(state.findings.map((f) => f.function)).size;
  const errors = bugs.filter((b) => b.severity === 'error').length;
  const assumed = bugs.filter((b) => b.severity === 'warning').length;
  const nulls = bugs.filter((b) => b.severity === 'info').length;
  const cards = bugs
    .map((bug, index) => ({
      index, function: bug.function, call: bug.input.call, line: bug.failure.line, severity: bug.severity,
      headline: SEVERITY_HEADLINE[bug.severity],
      why: capitalize(bugMessage(bug).replace(/^[^:]*\(reproduced\)[.:]\s*/, '').replace(bug.input.call + ' ', '')),
      how: `${ORIGINS[bug.origin ?? 'edge'] ?? ORIGINS.edge}, proven by replaying it in a fresh sandbox.`,
      assumptions: bug.assumptions,
    }))
    .sort((a, b) => ['error', 'warning', 'info'].indexOf(a.severity) - ['error', 'warning', 'info'].indexOf(b.severity));
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
  let message: string;
  if (bugs.length === 0) {
    message = `I checked ${plural(functions, 'function')} and found no failing input. That isn't proof there are no bugs.`;
  } else {
    message = `I checked ${plural(functions, 'function')}. ` +
      (errors ? `${plural(errors, 'input')} the types allow make the code fail` : 'Nothing fails on inputs the types allow') +
      (assumed ? `; ${assumed} more ${assumed === 1 ? 'fails' : 'fail'} under an assumption` : '') +
      (nulls ? `; ${nulls} only when handed null` : '') + '.';
  }
  return { title, risk, cards, message: state.running ? `${message} Checking again…` : message };
}

function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
