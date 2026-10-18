# Phantom

Phantom finds bugs in Python functions and backs every report with evidence: a concrete input that
makes the function fail, replayed in a fresh sandboxed process to prove it reproduces.
**Repro or it didn't happen.** It does not claim code is bug-free: "no failing input found" means only
that none of the generated inputs failed.

- **Phase 1, the shadow runner:** point it at a file, and for every function it generates inputs from the
  type hints (Hypothesis), runs them in an isolated worker (Docker), and reports the smallest failing input.
- **Phase 2, in the editor:** a VS Code extension and a local backend. Pause typing and you get static
  hints within milliseconds, then red squiggles for confirmed bugs, with a one-click **Replay** of the failing input.

## Setup (once)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker build -f sandbox/Dockerfile -t phantom-sandbox .   # the sandbox; again after changing sandbox/requirements.txt
cd extension && npm install && cd ..                        # the VS Code extension (needs Node.js)
```

## In VS Code

Open this folder in VS Code, pick **Run and Debug > "Phantom: backend + extension"** and press **F5**.
That starts the backend and opens a second VS Code window with the extension loaded and `samples/` open.
Open `buggy.py` there:

- **red squiggle** = confirmed bug: the message names the failing input, e.g.
  `average([]) raises ZeroDivisionError: division by zero`; it's on the line that failed;
- **"▶ Replay average([])"** above the function = run that input again in a fresh sandbox against the code
  as it is now. After fixing the bug, it tells you the bug no longer reproduces;
- **yellow squiggle** = static hint (undefined name, mutable default argument, ...): not run, not confirmed;
- **status bar** = number of confirmed bugs in the file, and whether code runs sandboxed. Click it for the log.

Fix a bug and its red squiggle goes away a moment after you stop typing. Settings: `phantom.enabled`,
`phantom.backendUrl`, `phantom.debounceMs` (700), `phantom.shadowRun`.

## From the command line

```bash
python phantom.py samples/buggy.py
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

## The sandbox

With Docker, each run gets a throwaway container: **no network, 256 MB memory, read-only filesystem,
no privileges, user `nobody`**. `auto` (the default) uses Docker as soon as the `phantom-sandbox` image
exists. Without it, code runs in a separate local process that caps its own memory (1 GB) and CPU time,
and the status bar says "not sandboxed".

## How it works

```
VS Code extension ──HTTP──> backend/app.py ──> runner/static.py      (pyflakes: hints, milliseconds)
 (extension/src)    localhost               └─> runner/analyzer.py  (per function, cached by hash)
                                                 ├─ runner/context.py   cut out the function + what it needs
                                                 └─ runner/executor.py  start a worker in Docker
                                                      └─ worker.py      Hypothesis inputs until one fails,
phantom.py (CLI) ─────────────────────────────────> analyzer           shrink it; then replay it fresh
```

- **worker.py** - the only place analysed code runs. Two jobs: `fuzz` (search for a failing input) and
  `call` (run one input: the replay). Detects exceptions, infinite loops (per-call time limit) and return
  values that contradict the annotation (e.g. `None` from `-> int`).
- **runner/executor.py** - starts the worker (Docker or local), passes the job on stdin, kills it on timeout.
- **runner/analyzer.py** - fuzzes a function, replays the failing input; only a failure that replays the
  same way is a `bug`.
- **runner/context.py** - what goes into the sandbox: the function, the imports, and the top-level functions,
  classes and constants it uses. Other lines are blanked, so line numbers stay those of your file.
- **runner/static.py** - pyflakes, minus style-only messages, plus a mutable-default-argument check.
- **backend/app.py** - FastAPI: `/static`, `/shadow`, `/replay`, `/health`. Caches each function's result
  by a hash of its code and context, so typing in one function only re-runs that function (and its callers).
- **extension/** - debounces typing (700 ms), shows diagnostics, Replay links and the status bar.
- **samples/buggy.py** - 10 deliberately buggy functions; **samples/correct.py** - false-positive check.

The repro format (`--json`, and what `/shadow` returns), which later layers build on:

```json
{"function": "average", "def_line": 8, "status": "bug", "examples": 5,
 "input": {"args": [[]], "call": "average([])"},
 "failure": {"kind": "exception", "error": "ZeroDivisionError", "message": "division by zero",
             "line": 10, "traceback": "..."}}
```

## Limits (so far)

- Finds crashes, timeouts and return-type violations. A function that returns a wrong answer without
  crashing (`is_leap_year` in the samples) needs an intent check: the Analyst layer.
- Specter, Analyst and Oracle (the three AI layers) are being built; today the editor shows static checks and the shadow run.
- Only top-level functions whose parameters have type hints (or defaults) are run; others are `skipped`.
- Pure functions only: no I/O, network or databases (the sandbox blocks them anyway).
- The extension sends the whole file to the backend, which runs on the same machine; only each function and
  its context goes into the sandbox. Before the backend moves to a server, the extension must send only that.

## Tests

```bash
python -m unittest -v                              # Python: worker, sandbox, samples, backend
cd extension && npm test                           # extension wording (Node)
cd extension && npm run test:integration           # end to end in a real VS Code (downloads one, starts the backend)
```

The sandbox tests (hostile code against network, filesystem, memory) run when the Docker image is built.
