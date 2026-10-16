# Phanthom

Phanthom finds bugs in Python functions and backs every report with evidence: a concrete input that
makes the function fail, replayed in a fresh sandboxed process to prove it reproduces.
**Repro or it didn't happen.** It does not claim code is bug-free: "no failing input found" means only
that none of the generated inputs failed.

This is **Phase 1, the shadow runner**: point it at a file, and for every function it generates inputs
from the type hints (Hypothesis), runs them in an isolated worker (Docker), and reports the smallest
failing input it finds.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python phanthom.py samples/buggy.py
```

```
  BUG  average         line 10: ZeroDivisionError: division by zero
                       repro: average([])   (reproduced in a fresh worker)
  BUG  find_index      line 23: ReturnTypeError: returned None (NoneType), but the annotation says -> int
                       repro: find_index([], 0)   (reproduced in a fresh worker)
  BUG  collatz_steps   line 50: Timeout: no result after 1 s (possible infinite loop)
                       repro: collatz_steps(0)   (reproduced in a fresh worker)
  ...
  ok   is_leap_year    no failing input in 200 generated inputs

  9 of 10 functions have a confirmed failing input.
```

Options: `-f NAME` (one function, repeatable), `--json` (findings in the repro format),
`--examples N` (inputs per function, default 200), `--sandbox auto|docker|local`.

## The Docker sandbox

Without Docker, the code runs in a separate local process that caps its own memory (1 GB) and CPU time.
With Docker, each run gets a throwaway container: **no network, 256 MB memory, read-only filesystem,
no privileges, user `nobody`**. `--sandbox auto` (the default) uses Docker as soon as the image exists:

```bash
docker build -f sandbox/Dockerfile -t phanthom-sandbox .   # again after changing requirements.txt
```

## How it works

```
phanthom.py ──> runner/analyzer.py ──> runner/executor.py ──> worker.py (in Docker or a child process)
   CLI           per function:          starts the worker,      exec the source, read the type hints,
                 1. fuzz job            sends the job as JSON,  Hypothesis generates inputs until one
                 2. replay the input    kills it on timeout     fails, shrinks it, prints one JSON line
                    in a fresh worker
```

- **worker.py** - the only place analysed code runs. Two jobs: `fuzz` (search for a failing input) and
  `call` (run one input, used for the replay). Detects exceptions, infinite loops (per-call time limit)
  and return values that contradict the annotation (e.g. `None` from `-> int`).
- **runner/executor.py** - starts the worker, passes the job on stdin, reads the JSON result,
  kills it on timeout.
- **runner/analyzer.py** - finds the functions (by parsing, not running), fuzzes each one, replays
  the failing input; only a failure that replays the same way is reported as `bug`.
- **samples/buggy.py** - 10 deliberately buggy functions; **samples/correct.py** - false-positive check.

The repro format (`--json`), which later layers build on:

```json
{"function": "average", "status": "bug", "examples": 5,
 "input": {"args": [[]], "call": "average([])"},
 "failure": {"kind": "exception", "error": "ZeroDivisionError", "message": "division by zero",
             "line": 10, "traceback": "..."}}
```

## Limits (Phase 1)

- Finds crashes, timeouts and return-type violations. A function that returns a wrong answer
  without crashing (`is_leap_year` in the samples) needs an intent check: Balanced tier, Phase 3.
- Only functions whose parameters have type hints (or defaults) can be fuzzed; others are `skipped`.
- Pure functions only: no I/O, network or databases (the sandbox blocks them anyway).

## Tests

```bash
python -m unittest -v
```

The sandbox tests (hostile code against network, filesystem, memory) run when the Docker image is built.
