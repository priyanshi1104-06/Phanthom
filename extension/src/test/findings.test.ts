// Unit tests for the editor wording: node --test out/test/
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { bugLine, bugMessage, confirmedBugs, describeReplay, ShadowFinding, toZeroBased } from '../findings';

const findings: ShadowFinding[] = [
  {
    function: 'average', def_line: 8, status: 'bug', examples: 5,
    input: { args: [[]], call: 'average([])' },
    failure: { kind: 'exception', error: 'ZeroDivisionError', message: 'division by zero', line: 10 },
  },
  {
    function: 'collatz_steps', def_line: 45, status: 'bug',
    input: { args: [0], call: 'collatz_steps(0)' },
    failure: { kind: 'timeout', error: 'Timeout', message: 'no result after 1 s', line: 48 },
  },
  { function: 'flaky', def_line: 50, status: 'unconfirmed', input: { args: [1], call: 'flaky(1)' },
    failure: { kind: 'exception', error: 'ValueError', message: 'x', line: 51 } },
  { function: 'is_leap_year', def_line: 61, status: 'no_bug_found', examples: 200 },
];

test('only reproduced failures become bugs', () => {
  assert.deepEqual(confirmedBugs(findings).map((b) => b.function), ['average', 'collatz_steps']);
});

test('messages name the failing input', () => {
  const [average, collatz] = confirmedBugs(findings);
  assert.equal(bugMessage(average), 'Confirmed bug (reproduced): average([]) raises ZeroDivisionError: division by zero');
  assert.match(bugMessage(collatz), /^Possible infinite loop \(reproduced\): collatz_steps\(0\)/);
});

test('squiggle goes on the failing line, clamped to the document', () => {
  const [average] = confirmedBugs(findings);
  assert.equal(bugLine(average, 100), 9);
  assert.equal(bugLine(average, 5), 4);
  assert.equal(toZeroBased(0, 10), 0);
});

test('replay verdicts', () => {
  assert.match(describeReplay('average([])', { ok: true, result: 3 }).summary, /no longer reproduces/);
  const still = describeReplay('average([])', { ok: false, kind: 'exception', error: 'ZeroDivisionError',
    message: 'division by zero', line: 10, traceback: 'Traceback...' });
  assert.equal(still.summary, 'average([]) still fails: ZeroDivisionError: division by zero (line 10)');
  assert.match(describeReplay('f()', { ok: false, kind: 'error', message: 'SyntaxError' }).summary, /^Couldn't replay/);
});
