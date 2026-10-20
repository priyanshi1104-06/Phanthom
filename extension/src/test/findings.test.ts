// Unit tests for the editor wording: npm test
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  bugLine, bugMessage, chosenModels, comingIn, confirmedBugs, DEPTHS, depthText, modelLabel, describeReplay, diagnosticLevel, Finding, lensTitle, Risk, riskText,
  statusText, toZeroBased,
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
  assert.match(lensTitle(average), /confirmed ZeroDivisionError {2}▶ Replay average\(\[\]\)/);
  assert.match(lensTitle(scale), /^\$\(warning\) Phantom: assumed/);
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
  assert.equal(statusText(bugs, 'docker', risk), '$(bug) Phantom: 3 confirmed · 1 assumed · risk high → Oracle');
  assert.equal(statusText([], 'local'), '$(check) Phantom: not sandboxed');
  assert.equal(statusText([], 'docker', { ...risk, level: 'low', escalate: null }), '$(check) Phantom');
  assert.equal(riskText({ ...risk, escalate: null, level: 'medium' }), 'risk medium');
});

test('depth and model pickers', () => {
  const models = chosenModels({ specter: 'claude-sonnet-5-5', analyst: 'not a model' });
  assert.deepEqual(models, { specter: 'claude-sonnet-5-5', analyst: 'claude-sonnet-5-5', oracle: 'claude-fable-5-1' });
  assert.equal(modelLabel('claude-opus-5-5'), 'Opus 5.5');
  assert.equal(depthText('auto', models, true), '$(layers) Auto · Specter: Sonnet 5.5');
  assert.equal(depthText('specter', models, false), '$(layers) Specter only · models off');
  assert.deepEqual(DEPTHS.map(comingIn), [undefined, undefined, 'coming in 0.2', 'coming in 0.3']);
});
