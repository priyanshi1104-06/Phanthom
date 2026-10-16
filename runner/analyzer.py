"""Shadow run: fuzz each function in a sandboxed worker, then replay the failing input to confirm it.

"Repro or it didn't happen": a finding is a confirmed bug only when a fresh worker, given just the
failing input, fails the same way again. Everything else is reported as what it is.

A finding (the repro format later layers build on):
    {"function": "average", "status": "bug", "examples": 3,
     "input": {"args": [[]], "call": "average([])"},
     "failure": {"kind": "exception", "error": "ZeroDivisionError", "message": "division by zero",
                 "line": 12, "traceback": "..."}}
status: "bug"          - failing input found and reproduced in a fresh worker
        "unconfirmed"  - something failed, but the replay did not fail the same way (or couldn't run)
        "no_bug_found" - every generated input passed (not proof that there is no bug)
        "skipped"      - inputs can't be generated (e.g. a parameter without a type hint)
        "error"        - the code couldn't be analysed (syntax error, worker crash, ...)
"""
import ast
from concurrent.futures import ThreadPoolExecutor

from runner.executor import run_job

FUZZ_TIMEOUT = 60.0  # seconds for a whole fuzz job
REPLAY_TIMEOUT = 5.0  # seconds for replaying one input


def find_functions(source):
    """Names of the public top-level functions, found by parsing (not running) the source."""
    tree = ast.parse(source)
    return [node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")]


def analyze_function(source, func_name, sandbox="local", max_examples=200, call_timeout=1.0):
    finding = {"function": func_name}
    result = run_job({"mode": "fuzz", "source": source, "func_name": func_name,
                      "max_examples": max_examples, "call_timeout": call_timeout},
                     timeout=FUZZ_TIMEOUT, sandbox=sandbox)

    if "status" not in result:  # the executor killed or lost the worker
        return {**finding, "status": "unconfirmed" if result["kind"] == "timeout" else "error",
                "failure": result}
    if result["status"] != "bug":
        return {**finding, **result}

    replay = replay_input(source, func_name, result["input"], sandbox, call_timeout)
    failure = result["failure"]
    same = (replay.get("ok") is False and replay.get("kind") == failure["kind"]
            and replay.get("error") == failure["error"])
    finding.update(status="bug" if same else "unconfirmed", examples=result["examples"],
                   input=result["input"], failure=failure)
    if not same:
        finding["replay"] = replay
    return finding


def replay_input(source, func_name, failing_input, sandbox, call_timeout=1.0):
    """Run the function once more, in a fresh worker, with just the failing input."""
    if failing_input.get("args") is None:
        return {"ok": None, "message": "input can't be sent to a worker exactly, so it wasn't replayed"}
    return run_job({"mode": "call", "source": source, "func_name": func_name,
                    "args": failing_input["args"], "call_timeout": call_timeout},
                   timeout=REPLAY_TIMEOUT, sandbox=sandbox)


def analyze_source(source, functions=None, sandbox="local", max_examples=200, jobs=4):
    """Findings for every public function in `source` (or just `functions`), in source order."""
    names = functions or find_functions(source)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(
            lambda name: analyze_function(source, name, sandbox=sandbox, max_examples=max_examples),
            names))
