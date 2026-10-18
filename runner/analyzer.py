"""Shadow run: fuzz each function in a sandboxed worker, then replay the failing input to confirm it.

"Repro or it didn't happen": a finding is a confirmed bug only when a fresh worker, given just the
failing input, fails the same way again. Everything else is reported as what it is.

A finding (the repro format later layers build on):
    {"function": "average", "def_line": 8, "status": "bug", "examples": 3,
     "input": {"args": [[]], "call": "average([])"},
     "failure": {"kind": "exception", "error": "ZeroDivisionError", "message": "division by zero",
                 "line": 10, "traceback": "..."}}
status: "bug"          - failing input found and reproduced in a fresh worker
        "unconfirmed"  - something failed, but the replay did not fail the same way (or couldn't run)
        "no_bug_found" - every generated input passed (not proof that there is no bug)
        "skipped"      - inputs can't be generated (e.g. a parameter without a type hint)
        "error"        - the code couldn't be analysed (syntax error, worker crash, ...)
"""
import ast
from concurrent.futures import ThreadPoolExecutor

from runner.context import function_context, missing_context, public_functions, top_level_names
from runner.executor import run_job

FUZZ_TIMEOUT = 60.0  # seconds for a whole fuzz job
REPLAY_TIMEOUT = 5.0  # seconds for replaying one input


def analyze_function(source, func_name, sandbox="local", max_examples=200, call_timeout=1.0):
    """Fuzz func_name, the source being everything it needs to run (see runner.context)."""
    finding = {"function": func_name}
    result = run_job({"mode": "fuzz", "source": source, "func_name": func_name,
                      "max_examples": max_examples, "call_timeout": call_timeout},
                     timeout=FUZZ_TIMEOUT, sandbox=sandbox)

    if "status" not in result:  # the executor killed or lost the worker
        return {**finding, "status": "unconfirmed" if result["kind"] == "timeout" else "error",
                "failure": result}
    if result["status"] != "bug":
        return {**finding, **result}

    replay = replay_input(source, func_name, result["input"].get("args"), sandbox, call_timeout)
    failure = result["failure"]
    same = (replay.get("ok") is False and replay.get("kind") == failure["kind"]
            and replay.get("error") == failure["error"])
    finding.update(status="bug" if same else "unconfirmed", examples=result["examples"],
                   input=result["input"], failure=failure)
    if not same:
        finding["replay"] = replay
    return finding


def replay_input(source, func_name, args, sandbox, call_timeout=1.0):
    """Run the function once more, in a fresh worker, with just the failing input."""
    if args is None:
        return {"ok": None, "message": "input can't be sent to a worker exactly, so it wasn't replayed"}
    return run_job({"mode": "call", "source": source, "func_name": func_name,
                    "args": args, "call_timeout": call_timeout},
                   timeout=REPLAY_TIMEOUT, sandbox=sandbox)


def analyze_in_file(source, tree, func_name, analyze=analyze_function, **options):
    """Analyse one function of a file: cut out its context, fuzz it, sanity-check the result.
    `analyze` is analyze_function or a cached wrapper with the same signature."""
    finding = analyze(function_context(source, func_name, tree), func_name, **options)
    name = missing_context(finding, top_level_names(tree))
    if name:
        finding = {"function": func_name, "status": "skipped",
                   "reason": f"needs {name!r}, which the file defines in a way Phantom can't extract yet"}
    return {**finding, "def_line": public_functions(tree).get(func_name)}


def analyze_source(source, functions=None, sandbox="local", max_examples=200, jobs=4,
                   analyze=analyze_function):
    """Findings for every public function in `source` (or just `functions`), in source order."""
    tree = ast.parse(source)
    names = functions or list(public_functions(tree))
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(
            lambda name: analyze_in_file(source, tree, name, analyze,
                                         sandbox=sandbox, max_examples=max_examples),
            names))
