# Phantom

Phantom finds bugs while you write code, before you run it, and backs every report with evidence: a
concrete input that makes the function fail, replayed in a fresh sandbox to prove it reproduces.
**Repro or it didn't happen.** It never claims code is bug-free: "no failing input found" means only that
none of the inputs it tried failed.

Phantom has three AI layers of increasing depth:

| Layer | Speed | What it does | State |
|---|---|---|---|
| **Specter** | always on, ~1 s | boundary inputs from the types + a fast model's suggestions, run in a sandbox; risk triage of every edit | **built, 10 languages** |
| **Analyst** | seconds | wrong answers: the code's own examples, and the intent its model reads; callers an edit breaks | **built, 10 languages** |
| **Oracle** | tens of seconds | a frontier agent in the sandbox: writes tests, fuzzes, proves fixes | after Analyst |

Specter escalates on its own: an edit touching databases, memory handling, dangerous calls or deletion asks
for the Analyst; concurrency, auth, payments or schema migrations ask for the Oracle.

### The Analyst

Specter looks for inputs that make a function crash. The Analyst looks for inputs where it runs fine and
**answers wrong**. It needs an expected value, and takes it from two places:

1. **Examples written in the code**, any language, any comment style: `>>> is_leap_year(1900)` + `False` in a
   docstring, `clamp(15, 0, 10) == 10`, `-> 212.0`, `// => 10`, `# => 10`, `assert_eq!(f(2), 4)`. No AI needed.
   A failing example is red: the code and its own documentation disagree.
2. **The intent its model reads** (with an AI key): from the docstring, comments, the name, how the file calls
   the function, or a well-known rule (leap years, unit conversions). Every expectation must quote the words in
   the code it relies on, exactly, or it is dropped. A failed one is yellow, with the quote and the reason.

The call runs in the sandbox; so does the expected value, through an identity function with the same return
type, so both are printed by the same harness and compared as text (numbers with a tolerance). A mismatch is
replayed in a fresh sandbox and reported only if it returns the same wrong value again.

**Broken callers:** each function's result is cached by a hash of the code it depends on (itself, the
functions it calls, transitively, and the rest of the file). Edit `double()` and the examples of
`quadruple()`, which calls it, run again: the break shows up on the caller.

In VS Code, depth **Auto** runs the Analyst when you open or save a file, when you click **Check intent** in
the Phantom panel, or when an edit is risky; **Up to Analyst** runs it
on every pause. Its model reads 4 functions per run (the edited ones and their callers first); the rest follow.

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

Without an API key Specter still works: it uses boundary inputs only, and the status bar says so. The
Analyst then checks the examples written in the code only.

## In VS Code

Open this folder in VS Code, pick **Run and Debug > "Phantom: backend + extension"** and press **F5**.
That starts the backend and opens a second VS Code window with the extension loaded and `samples/` open.
Open any file in `samples/` there:

- **red squiggle** = fails on an input its types allow, e.g. `average([]) raises ZeroDivisionError`;
  it's on the line that failed;
- **yellow squiggle** = fails only under a named assumption (untyped code: "values may be {}", or extreme
  numbers: "n may be as large as INT_MAX"); also static hints for Python (undefined names, ...);
- **faint dots** = fails only when handed null/None/nil ("text may be null"): hover to see it;
- **"Wrong result"** (the Analyst) = the call runs but returns something else than expected: red when the
  code's own example says so, yellow when it's the AI's reading of the intent (quoted);
- **"▶ Replay average([])"** above the function = run that input again in a fresh sandbox against the
  code as it is now; after a fix it tells you the bug no longer reproduces;
- **status bar** = confirmed/assumed counts, the edit's risk and which layer should look
  (`risk high → Oracle`), and a warning if code isn't sandboxed. Click it for the log.

Settings: `phantom.enabled`, `phantom.backendUrl`, `phantom.debounceMs` (700), `phantom.shadowRun`,
`phantom.useModel`.

## From the command line

```bash
python phantom.py samples/crashes.py             # any supported file; the extension picks the language
python phantom.py samples/go/crashes.go -f Average # one function
python phantom.py samples/javascript/crashes.js --json --no-model
python phantom.py samples/intent/intent.py --analyst   # also wrong answers (the Analyst)
```

```
samples/javascript/crashes.js  (javascript, runs in: Docker sandbox, risk: low)

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
 (extension/src)    localhost  /triage            ├─ runner/analyst.py    wrong answers: examples + intent
                               /specter           ├─ runner/triage.py     risk from the words in the code
                               /analyst           ├─ runner/llm.py        the model's hypotheses (optional)
phantom.py (CLI) ────────────> /replay            ├─ runner/languages/    one plugin per language:
                                                  │    functions, literal check, boundary inputs, harness
                                                  └─ runner/executor.py   the Docker sandbox
```

- **runner/specter.py** - for each function: boundary inputs + model inputs, all run in one sandbox,
  failures grouped and replayed in a fresh one; Python functions are also fuzzed with Hypothesis.
- **runner/analyst.py** - expectations (the code's examples, the model's reading of the intent), run and
  compared in the sandbox, mismatches replayed; a call graph decides what an edit re-checks.
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

- Specter finds crashes, hangs, sanitizer errors and return-type violations; the Analyst finds wrong answers
  when there is an expected value (an example in the code, or a clear intent its model can quote). Its
  model's expected values are a reading of the intent, not facts: they are warnings that show their source.
- The Analyst's call graph is read from the text (a name followed by "("), within one file.
- Top-level functions with parameters Phantom can build values for; methods, generics, struct/class
  parameters are listed as `skipped`.
- Pure functions only: no I/O, network or databases (the sandbox blocks them anyway).
- The extension sends the whole file to the backend, which runs on the same machine. Before the backend
  moves to a server, the extension must send only what each function needs.

## Tests

```bash
python -m unittest -v                              # Python side: plugins, Specter, Analyst, sandbox, backend
cd extension && npm test                           # extension wording (Node)
cd extension && npm run test:integration           # end to end in a real VS Code (downloads one, starts the backend)
```

The sandbox tests run for each language whose Docker image is present.
