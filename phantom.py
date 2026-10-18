"""Phantom shadow run from the command line.

    python phantom.py samples/buggy.py            # every public function in the file
    python phantom.py samples/buggy.py -f average # just one
    python phantom.py samples/buggy.py --json     # findings as JSON (the repro format)

Exit code: 0 = no confirmed bug, 1 = at least one confirmed bug, 2 = usage or analysis error.
"""
import argparse
import json
import sys
from pathlib import Path

from runner.analyzer import analyze_source
from runner.executor import IMAGE, choose_sandbox

LABELS = {"bug": "BUG", "unconfirmed": "???", "no_bug_found": "ok", "skipped": "skip", "error": "ERR"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Find reproducible failing inputs for Python functions.")
    parser.add_argument("files", nargs="+", type=Path, help="Python files to analyse")
    parser.add_argument("-f", "--function", action="append", help="only this function (repeatable)")
    parser.add_argument("--sandbox", choices=["auto", "docker", "local"], default="auto",
                        help="where the code runs (auto: Docker if the image is built)")
    parser.add_argument("--examples", type=int, default=200, help="generated inputs per function")
    parser.add_argument("--jobs", type=int, default=4, help="functions analysed in parallel")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)

    try:
        sandbox = choose_sandbox(args.sandbox)
    except RuntimeError as exc:
        parser.error(str(exc))

    exit_code = 0
    all_findings = {}
    for path in args.files:
        source = path.read_text()
        try:
            findings = analyze_source(source, args.function, sandbox=sandbox,
                                      max_examples=args.examples, jobs=args.jobs)
        except SyntaxError as exc:
            print(f"{path}: can't parse: {exc}", file=sys.stderr)
            exit_code = 2
            continue
        all_findings[str(path)] = findings
        if any(f["status"] == "bug" for f in findings):
            exit_code = max(exit_code, 1)
        if not args.json:
            print_report(path, findings, sandbox)

    if args.json:
        print(json.dumps(all_findings, indent=2))
    return exit_code


def print_report(path, findings, sandbox):
    where = "Docker sandbox" if sandbox == "docker" else \
        f"local process (no Docker sandbox: build the '{IMAGE}' image to enable it)"
    print(f"\n{path}  -  runs in: {where}\n")
    width = max(len(f["function"]) for f in findings)
    for f in findings:
        status = f["status"]
        print(f"  {LABELS[status]:<4} {f['function']:<{width}}  {summary(f)}")
        if status in ("bug", "unconfirmed") and "input" in f:
            note = "reproduced in a fresh worker" if status == "bug" else "NOT reproduced on replay"
            print(f"  {'':<4} {'':<{width}}  repro: {f['input']['call']}   ({note})")
    bugs = sum(f["status"] == "bug" for f in findings)
    print(f"\n  {bugs} of {len(findings)} functions have a confirmed failing input.\n")


def summary(f):
    status = f["status"]
    if status == "no_bug_found":
        return f"no failing input in {f['examples']} generated inputs"
    if status == "skipped":
        return f"skipped: {f['reason']}"
    failure = f.get("failure", f)
    line = f"line {failure['line']}: " if failure.get("line") else ""
    return f"{line}{failure.get('error', 'Error')}: {failure.get('message', '')}"


if __name__ == "__main__":
    sys.exit(main())
