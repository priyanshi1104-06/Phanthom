// Unit tests for the editor wording: npm test
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  bugLine, bugMessage, chosenTiers, comingIn, confirmedBugs, DEPTHS, depthText, panelView, tierLabel, describeReplay, diagnosticLevel, Finding, lensTitle, Risk, riskText,
  statusText, toZeroBased, wantsAnalyst,
} from '../findings';

const findings: Finding[] = [
  {
    function: 'average', def_line: 8, status: 'bug', severity: 'error', origin: 'edge', assumptions: [],
    input: { call: 'average([])', exprs: ['[]'] },
    failure: { kind: 'exception', error: 'ZeroDivisionError', message: 'division by zero', line: 10 },
  },
  {
    function: 'collatz_steps', def_line: 45, status: 'bug', severity: 'error', assumptions: [],
    input: { call: 'collatz_steps(0)', exprs: ['0'] },
    failure: { kind: 'timeout', error: 'Timeout', message: 'no result after 1 s', line: 48 },
  },
  {
    function: 'firstWord', def_line: 9, status: 'bug', severity: 'info', assumptions: ['text may be null'],
    input: { call: 'firstWord(null)', exprs: ['null'] },
    failure: { kind: 'exception', error: 'TypeError', message: "Cannot read properties of null (reading 'trim')", line: 10 },
  },
  {
    function: 'scale', def_line: 3, status: 'bug', severity: 'warning', assumptions: ['n may be as large as INT_MAX'],
    input: { call: 'scale(INT_MAX)', exprs: ['INT_MAX'] },
    failure: { kind: 'crash', error: 'signed integer overflow', message: '2147483647 * 2 cannot be represented', line: 4 },
  },
  { // Phase 2 format: tagged JSON arguments, no severity
    function: 'factorial', def_line: 55, status: 'bug', input: { call: 'factorial(-1)', args: [-1] },
    failure: { kind: 'exception', error: 'RecursionError', message: 'maximum recursion depth exceeded', line: 58 },
  },
  { function: 'is_leap_year', def_line: 61, status: 'no_bug_found', examples: 200 },
  { function: 'gen', def_line: 70, status: 'skipped', reason: 'generator functions are not run yet' },
];

test('only reproduced failures become bugs', () => {
  assert.deepEqual(confirmedBugs(findings).map((b) => b.function),
    ['average', 'collatz_steps', 'firstWord', 'scale', 'factorial']);
  assert.equal(confirmedBugs(findings)[4].severity, 'error'); // old findings count as confirmed
});

test('severity decides how loud the squiggle is', () => {
  assert.deepEqual(confirmedBugs(findings).map(diagnosticLevel), ['error', 'error', 'hint', 'warning', 'error']);
});

test('messages name the failing input and the assumption', () => {
  const [average, collatz, firstWord, scale] = confirmedBugs(findings);
  assert.equal(bugMessage(average), 'Confirmed bug (reproduced): average([]) raises ZeroDivisionError: division by zero');
  assert.match(bugMessage(collatz), /^Possible infinite loop: collatz_steps\(0\)/);
  assert.equal(bugMessage(firstWord),
    "Fails if text may be null (reproduced): firstWord(null) raises TypeError: Cannot read properties of null (reading 'trim')");
  assert.match(bugMessage(scale), /scale\(INT_MAX\) crashes with signed integer overflow/);
  assert.match(lensTitle(average), /^\u{1F534} Phantom: confirmed ZeroDivisionError {2}▶ Replay average\(\[\]\)/u);
  assert.match(lensTitle(scale), /^\u{1F7E0} Phantom: assumed/u);
});

test('squiggle goes on the failing line, clamped to the document', () => {
  const [average] = confirmedBugs(findings);
  assert.equal(bugLine(average, 100), 9);
  assert.equal(bugLine(average, 5), 4);
  assert.equal(toZeroBased(0, 10), 0);
});

test('replay verdicts', () => {
  assert.match(describeReplay('average([])', { ok: true, result: 3 }).summary, /returned 3: the bug no longer reproduces/);
  assert.match(describeReplay('average([2])', { ok: true, result: '2' }).summary, /returned 2:/); // other languages: text
  const still = describeReplay('average([])', { ok: false, kind: 'exception', error: 'ZeroDivisionError',
    message: 'division by zero', line: 10, traceback: 'Traceback...' });
  assert.equal(still.summary, 'average([]) still fails: ZeroDivisionError: division by zero (line 10)');
  assert.match(describeReplay('f()', { ok: false, kind: 'error', message: 'SyntaxError' }).summary, /^Couldn't replay/);
  assert.match(describeReplay('f()', { ok: false, kind: 'build_error', error: 'BuildError', message: 'x' }).summary,
    /^Couldn't replay/);
});

test('status bar: counts, risk and escalation', () => {
  const bugs = confirmedBugs(findings);
  const risk: Risk = { level: 'high', edit_level: 'high', changed: ['pay'], escalate: 'oracle',
    reasons: [{ function: 'pay', category: 'payments', evidence: ['amount', 'balance'] }] };
  assert.equal(statusText(bugs, 'docker', risk), '\u{1F534} Phantom: 3 confirmed · 1 assumed · risk high → Oracle');
  assert.equal(statusText([], 'local'), '$(check) Phantom: not sandboxed');
  assert.equal(statusText([], 'docker', { ...risk, level: 'low', escalate: null }), '$(check) Phantom');
  assert.equal(riskText({ ...risk, escalate: null, level: 'medium' }), 'risk medium');
});

test('depth and tier pickers never name a model or supplier', () => {
  const tiers = chosenTiers({ specter: 'balanced', analyst: 'claude-opus-5-5' });
  assert.deepEqual(tiers, { specter: 'balanced', analyst: 'balanced', oracle: 'deepest' });
  assert.equal(tierLabel('deep'), 'Deep');
  assert.equal(depthText('auto', tiers, true), '$(layers) Auto · Balanced / Balanced');
  assert.equal(depthText('specter', tiers, true), '$(layers) Specter only · Balanced');
  assert.equal(depthText('specter', tiers, false), '$(layers) Specter only · engine only');
  assert.deepEqual(DEPTHS.map(comingIn), [undefined, undefined, undefined, 'coming in 0.3']);
});

test('the panel talks about the file and says how each bug was found', () => {
  const view = panelView('samples/crashes.js', { findings });
  assert.equal(view.message, 'I checked 7 functions. 3 inputs the types allow make the code fail; 1 more fails under an assumption; 1 only when handed null.');
  assert.equal(view.cards[0].severity, 'error');
  assert.equal(view.cards[0].how, "Found by Phantom's boundary inputs, proven by replaying it in a fresh sandbox.");
  assert.equal(view.cards[0].why, 'Raises ZeroDivisionError: division by zero');  // the call is shown above it
  assert.deepEqual(view.cards.map((c) => c.severity), ['error', 'error', 'error', 'warning', 'info']);
  assert.match(panelView('a.py', { findings: [findings[5]] }).message, /no failing input.*isn't proof/);
  assert.match(panelView('a.py', { offline: true, problem: 'Python packages missing' }).message, /can't reach my backend/);
  assert.match(panelView('a.py', { running: true }).message, /^Checking/);
  assert.equal(panelView('a.py', { findings: [] }).message, 'There are no functions in this file for me to check.');
});

const wrong: Finding[] = [
  {
    function: 'is_leap_year', def_line: 8, status: 'bug', severity: 'error', origin: 'example', layer: 'analyst',
    assumptions: [], input: { call: 'is_leap_year(1900)', exprs: ['1900'] },
    failure: { kind: 'wrong_result', error: 'WrongResult', message: 'returned True, expected False', line: 8 },
    actual: 'True', expected: { value: 'False', basis: 'example', quote: '>>> is_leap_year(1900)', why: '' },
  },
  {
    function: 'count_vowels', def_line: 30, status: 'bug', severity: 'warning', origin: 'intent', layer: 'analyst',
    assumptions: [], input: { call: 'count_vowels("AEIOU")', exprs: ['"AEIOU"'] },
    failure: { kind: 'wrong_result', error: 'WrongResult', message: 'returned 0, expected 5', line: 30 },
    actual: '0', expected: { value: '5', basis: 'docstring', quote: 'upper or lower case', why: 'upper-case vowels count' },
  },
];

test('Analyst: wrong results say what was expected and why', () => {
  const [leap, vowels] = confirmedBugs(wrong);
  assert.equal(bugMessage(leap), 'Wrong result (reproduced): is_leap_year(1900) returned True, expected False: ' +
    'the code\'s own example says so: ">>> is_leap_year(1900)"');
  assert.equal(bugMessage(vowels), 'Wrong result (reproduced): count_vowels("AEIOU") returned 0, expected 5: ' +
    'upper-case vowels count (from its docstring: "upper or lower case")');
  assert.equal(lensTitle(vowels), '\u{1F7E0} Phantom: wrong result, expected 5  ▶ Replay count_vowels("AEIOU")');
  assert.equal(statusText(confirmedBugs([findings[0], ...wrong]), 'docker'), '\u{1F534} Phantom: 1 confirmed · 2 wrong results');
  const view = panelView('intent.py', { findings: [findings[0], ...wrong] });
  assert.equal(view.message, 'I checked 3 functions. 1 input the types allow makes the code fail; 2 calls give a wrong result.');
  const card = view.cards.find((c) => c.function === 'count_vowels')!;
  assert.equal(card.headline, 'Wrong result');
  assert.equal(card.why, 'Returned 0, expected 5.');
  assert.equal(card.because, 'Upper-case vowels count (from its docstring: "upper or lower case")');
  assert.match(panelView('a.py', { findings: [], analysing: true, running: true }).message, /Analyst is checking/);
  assert.equal(describeReplay('is_leap_year(1900)', { ok: false, kind: 'wrong_result', error: 'WrongResult',
    message: 'returned True, expected False' }).summary, 'is_leap_year(1900) still returned True, expected False.');
});

test('when Analyst runs', () => {
  const risky: Risk = { level: 'medium', edit_level: 'medium', changed: ['q'], escalate: 'analyst', reasons: [] };
  assert.equal(wantsAnalyst('specter', risky, true), false);
  assert.equal(wantsAnalyst('auto', undefined, false), false);
  assert.equal(wantsAnalyst('auto', undefined, true), true);  // opened, saved or asked for
  assert.equal(wantsAnalyst('auto', risky, false), true);
  assert.equal(wantsAnalyst('analyst', undefined, false), true);
});
