# Phantom

Phantom finds bugs while you write code, before you run it, and backs every report with evidence: a
concrete input that makes the function fail, replayed in a fresh sandbox to prove it reproduces.
**Repro or it didn't happen.** It never claims code is bug-free: "no failing input found" means only that
none of the inputs it tried failed.

Phantom has three AI layers of increasing depth:

| Layer | Speed | What it does | State |
|---|---|---|---|
| **Specter** | always on, ~1 s | boundary inputs from the types + a fast model's suggestions, run in a sandbox; risk triage of every edit | **built, 10 languages** |
| **Analyst** | seconds | a large model + a code graph: callers broken by an edit, intent bugs (wrong answers) | next |
| **Oracle** | tens of seconds | a frontier agent in the sandbox: writes tests, fuzzes, proves fixes | after Analyst |

Specter escalates on its own: an edit touching databases, memory handling, dangerous calls or deletion asks
for the Analyst; concurrency, auth, payments or schema migrations ask for the Oracle.

## Languages

Python, JavaScript, TypeScript, Go, Java, C, C++, Rust, Ruby, PHP. Each runs in its own Docker image:

```bash
python phantom.py languages     # what is supported and ready
python phantom.py setup         # pull/build every language's sandbox image (or: setup go rust ...)
```

## Install (as a user)

1. Build the extension once: `cd extension && npm install && npm run package` gives `phantom-0.1.0.vsix`.
2. In VS Code: **Extensions → ⋯ → Install from VSIX…** and pick that file.
3. Command Palette (Ctrl+Shift+P) → **Phantom: Set Up**. It creates Phantom's own Python environment, pulls the
   sandbox images (needs Docker) and asks for an optional Anthropic API key, stored in VS Code's secret storage.

The extension starts and stops its own backend. Click **$(layers) Auto · Specter: Haiku 5.5** in the status bar
to choose the depth (Auto, Specter only; Analyst and Oracle arrive as updates) and the Claude model for each layer.

## Setup (for development)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python phantom.py setup                   # Docker images (Python's is built from sandbox/Dockerfile)
cd extension && npm install && cd ..      # the VS Code extension (needs Node.js)
cp .env.example .env                      # optional: put ANTHROPIC_API_KEY in .env for Specter's model
```

Without an API key Specter still works: it uses boundary inputs only, and the status bar says so.

## In VS Code

Open this folder in VS Code, pick **Run and Debug > "Phantom: backend + extension"** and press **F5**.
That starts the backend and opens a second VS Code window with the extension loaded and `samples/` open.
Open any file in `samples/` there:

- **red squiggle** = fails on an input its types allow, e.g. `average([]) raises ZeroDivisionError`;
  it's on the line that failed;
- **yellow squiggle** = fails only under a named assumption (untyped code: "values may be {}", or extreme
  numbers: "n may be as large as INT_MAX"); also static hints for Python (undefined names, ...);
- **faint dots** = fails only when handed null/None/nil ("text may be null"): hover to see it;
- **"▶ Replay average([])"** above the function = run that input again in a fresh sandbox against the
  code as it is now; after a fix it tells you the bug no longer reproduces;
- **status bar** = confirmed/assumed counts, the edit's risk and which layer should look
  (`risk high → Oracle`), and a warning if code isn't sandboxed. Click it for the log.

Settings: `phantom.enabled`, `phantom.backendUrl`, `phantom.debounceMs` (700), `phantom.shadowRun`,
`phantom.useModel`.

## From the command line

```bash
python phantom.py samples/buggy.py               # any supported file; the extension picks the language
python phantom.py samples/go/buggy.go -f Average # one function
python phantom.py samples/javascript/buggy.js --json --no-model
```

```
samples/javascript/buggy.js  (javascript, runs in: Docker sandbox, risk: low)

  BUG  average       line 5: TypeError: Reduce of empty array with no initial value
                     repro: average([])   (reproduced in a fresh sandbox; boundary input)
  null firstWord     line 10: TypeError: Cannot read properties of null (reading 'trim')
                     repro: firstWord(null)   (reproduced in a fresh sandbox; boundary input)
                     assumes: text may be null
  BUG  collatzSteps  line 28: Timeout: no result after 2 s (possible infinite loop)
                     repro: collatzSteps(0)   (reproduced in a fresh sandbox; boundary input)
  ...
  ok   isLeapYear    no failing input in 6 inputs
```

Exit code 1 when a function fails on an input its types allow.

## The sandbox

Every run gets a throwaway container: **no network, a memory cap, read-only filesystem with a small
scratch `/tmp`, all capabilities dropped, no new privileges, user `nobody`**. The code is built once and
each input runs in a fresh process with a time limit; each failure is then replayed alone in a brand-new
container before it is reported. Without Docker, a language falls back to a local process if its
toolchain is installed (the status bar says "not sandboxed"); `PHANTOM_SANDBOX=docker` forbids that.

The model's inputs are untrusted: each must parse as a plain literal of the language (no calls,
variables, arithmetic or string interpolation) before it is compiled into the test harness, and it only
counts if it reproduces in the sandbox.

## How it works

```
VS Code extension ──HTTP──> backend/app.py ──> runner/specter.py        per function, cached by hash
 (extension/src)    localhost  /triage            ├─ runner/triage.py     risk from the words in the code
                               /specter           ├─ runner/llm.py        the model's hypotheses (optional)
phantom.py (CLI) ────────────> /replay            ├─ runner/languages/    one plugin per language:
                                                  │    functions, literal check, boundary inputs, harness
                                                  └─ runner/executor.py   the Docker sandbox
```

- **runner/specter.py** - for each function: boundary inputs + model inputs, all run in one sandbox,
  failures grouped and replayed in a fresh one; Python functions are also fuzzed with Hypothesis.
- **runner/languages/base.py** - the plugin contract; one file per language next to it.
- **runner/triage.py** - low / medium / high / critical per function and per edit, and who to escalate to.
- **runner/llm.py** - Claude API access per layer (Specter: Haiku; Analyst: Sonnet; Oracle: Fable);
  settings in `.env`.
- **worker.py**, **runner/analyzer.py**, **runner/context.py** - the Python engine from Phase 1.
- **backend/app.py** - FastAPI on 127.0.0.1:8765; refuses browser requests.
- **extension/** - debounces typing, shows diagnostics, Replay links and the status bar.
- **samples/** - deliberately buggy and correct code for every language.

A finding (`--json`, and what `/specter` returns):

```json
{"function": "average", "def_line": 4, "language": "javascript", "status": "bug",
 "severity": "error", "origin": "edge", "assumptions": [],
 "input": {"call": "average([])", "exprs": ["[]"]},
 "failure": {"kind": "exception", "error": "TypeError",
             "message": "Reduce of empty array with no initial value", "line": 5, "traceback": "..."}}
```

## Limits (so far)

- Finds crashes, hangs, sanitizer errors and return-type violations. A wrong answer without a crash
  (`is_leap_year` in the samples) needs an intent check: the Analyst layer.
- Top-level functions with parameters Phantom can build values for; methods, generics, struct/class
  parameters are listed as `skipped`.
- Pure functions only: no I/O, network or databases (the sandbox blocks them anyway).
- The extension sends the whole file to the backend, which runs on the same machine. Before the backend
  moves to a server, the extension must send only what each function needs.

## Tests

```bash
python -m unittest -v                              # Python side: plugins, Specter, sandbox, backend
cd extension && npm test                           # extension wording (Node)
cd extension && npm run test:integration           # end to end in a real VS Code (downloads one, starts the backend)
```

The sandbox tests run for each language whose Docker image is present.
