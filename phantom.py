"""Phantom from the command line: Specter (and Analyst) on any supported file, and sandbox setup.

    python phantom.py samples/crashes.py             # every function in the file (language from the extension)
    python phantom.py samples/javascript/crashes.js -f average # just one function
    python phantom.py samples/go/crashes.go --json   # findings as JSON (the repro format)
    python phantom.py samples/intent/intent.py --analyst  # also Analyst: wrong answers, not just crashes
    python phantom.py setup                          # pull/build the sandbox image of every language
    python phantom.py setup go rust                  # just these
    python phantom.py languages                      # what is supported and ready

Exit code: 0 = no confirmed bug, 1 = at least one confirmed bug, 2 = usage or analysis error.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILT_HERE = {  # images Phantom builds itself (the rest are pulled from Docker Hub)
    "phantom-sandbox": "sandbox/Dockerfile",
    "phantom-go": "sandbox/go.Dockerfile",
}
OLD_NAMES = {"phantom-sandbox": "phanthom-sandbox"}  # built before the rename
LABELS = {"bug": "BUG", "no_bug_found": "ok", "skipped": "skip", "error": "ERR", "unconfirmed": "???"}
SEVERITY = {"error": "BUG", "warning": "WARN", "info": "null"}  # a bug's label: see specter.severity


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["setup"]:
        return setup(argv[1:])
    if argv[:1] == ["languages"]:
        return show_languages()
    return check(argv)


# ---------- checking files ----------

def check(argv):
    parser = argparse.ArgumentParser(description="Find reproducible failing inputs, in any supported language.")
    parser.add_argument("files", nargs="+", type=Path, help="source files to check")
    parser.add_argument("-f", "--function", action="append", help="only this function (repeatable)")
    parser.add_argument("--language", help="override the language (default: from the file extension)")
    parser.add_argument("--sandbox", choices=["auto", "docker", "local"], default="auto",
                        help="where the code runs (auto: Docker if the language's image is there)")
    parser.add_argument("--no-model", action="store_true", help="no AI: edge inputs, and Analyst only reads examples")
    parser.add_argument("--analyst", action="store_true",
                        help="also run Analyst: expected results from the code's examples and the AI's reading")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    args = parser.parse_args(argv)
    os.environ["PHANTOM_SANDBOX"] = args.sandbox

    from runner import analyst, specter

    exit_code, results = 0, {}
    for path in args.files:
        answer = specter.run(path.read_text(), args.language, str(path), use_model=not args.no_model)
        if args.analyst and answer.get("parsed") and not answer.get("setup"):
            deeper = analyst.run(path.read_text(), args.language, str(path), use_model=not args.no_model)
            answer["findings"] += deeper["findings"]
            answer["analyst_pending"] = deeper["pending"]
        if args.function:
            answer["findings"] = [f for f in answer["findings"] if f["function"] in args.function]
        results[str(path)] = answer
        if not answer.get("supported"):
            print(f"{path}: this language isn't supported (see: python phantom.py languages)", file=sys.stderr)
            exit_code = 2
        elif not answer.get("parsed"):
            print(f"{path}: doesn't parse", file=sys.stderr)
            exit_code = 2
        elif answer.get("setup"):
            print(f"{path}: no sandbox for {answer['language']} yet: run  {answer['setup']}", file=sys.stderr)
            exit_code = 2
        elif any(f["status"] == "bug" and f.get("severity") == "error" for f in answer["findings"]):
            exit_code = max(exit_code, 1)
        if not args.json and answer.get("parsed") and not answer.get("setup"):
            print_report(path, answer)
    if args.json:
        print(json.dumps(results, indent=2))
    return exit_code


def print_report(path, answer):
    where = "Docker sandbox" if answer["sandbox"] == "docker" else "local process (NOT the Docker sandbox)"
    risk = answer["risk"]
    print(f"\n{path}  ({answer['language']}, runs in: {where}, risk: {risk['level']})\n")
    findings = answer["findings"]
    width = max((len(f["function"]) for f in findings), default=0)
    for f in findings:
        label = SEVERITY.get(f.get("severity")) if f["status"] == "bug" else LABELS.get(f["status"], f["status"])
        print(f"  {label:<4} {f['function']:<{width}}  {summary(f)}")
        if f["status"] == "bug":
            origin = {"edge": "boundary input", "model": "model's input", "fuzz": "fuzzed",
                      "example": "the code's own example", "intent": "Analyst's reading of the intent"
                      }.get(f.get("origin"), "")
            print(f"  {'':<4} {'':<{width}}  repro: {f['input']['call']}   (reproduced in a fresh sandbox; {origin})")
            if f.get("expected"):
                print(f"  {'':<4} {'':<{width}}  expected because: {f['expected']['quote']}")
            for assumption in f.get("assumptions", []):
                print(f"  {'':<4} {'':<{width}}  assumes: {assumption}")
    functions = len({f["function"] for f in findings})
    crashes = [f for f in findings if f["status"] == "bug" and f["failure"].get("kind") != "wrong_result"]
    bugs = len({f["function"] for f in crashes if f.get("severity") == "error"})
    assumed = len({f["function"] for f in crashes}) - bugs
    wrong = len({f["function"] for f in findings if f.get("failure", {}).get("kind") == "wrong_result"})
    print(f"\n  {bugs} of {functions} functions fail on an input their types allow"
          + (f"; {assumed} more only under an assumption (WARN/null)" if assumed else "")
          + (f"; {wrong} give a wrong result (Analyst)" if wrong else "") + ".")
    if answer.get("analyst_pending"):
        print(f"  Analyst's AI hasn't read {answer['analyst_pending']} functions yet: run again for those.")
    print()


def summary(f):
    status = f["status"]
    if status == "no_bug_found" and f.get("layer") == "analyst":
        return f"Analyst: {f.get('examples', 0)} expected results match"
    if status == "no_bug_found":
        return f"no failing input in {f.get('examples', 0)} inputs"
    if status == "skipped":
        return f"skipped: {f['reason']}"
    failure = f.get("failure", {})
    line = f"line {failure['line']}: " if failure.get("line") else ""
    return f"{line}{failure.get('error', 'Error')}: {str(failure.get('message', ''))[:200]}"


# ---------- sandbox images ----------

def setup(names):
    """Pull or build the Docker image of each language (all of them by default)."""
    from runner import languages

    if shutil.which("docker") is None:
        print("Docker isn't installed: see the README (Ubuntu: sudo apt install docker.io).", file=sys.stderr)
        return 2
    wanted = [languages.resolve(n) for n in names] if names else list(languages.LANGUAGES.values())
    if None in wanted:
        print(f"unknown language; known: {', '.join(languages.LANGUAGES)}", file=sys.stderr)
        return 2
    failed = []
    for image in dict.fromkeys(lang.image for lang in wanted):
        if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0:
            print(f"ok      {image}")
            continue
        old = OLD_NAMES.get(image)
        if old and subprocess.run(["docker", "image", "inspect", old], capture_output=True).returncode == 0:
            command = ["docker", "tag", old, image]
        elif image in BUILT_HERE:
            command = ["docker", "build", "-f", BUILT_HERE[image], "-t", image, "."]
        else:
            command = ["docker", "pull", image]
        print(f"...     {' '.join(command)}")
        if subprocess.run(command, cwd=ROOT).returncode != 0:
            failed.append(image)
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
        return 2
    return 0


def show_languages():
    from runner import languages

    for lang in languages.LANGUAGES.values():
        ready = lang.sandbox()
        state = {"docker": "ready (Docker sandbox)", "local": "local process only (no Docker image)"}.get(
            ready, f"not set up: python phantom.py setup {lang.name}")
        print(f"  {lang.title:<11} {' '.join(lang.extensions):<24} {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
