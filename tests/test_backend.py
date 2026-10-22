"""Context extraction, static checks and the HTTP backend the extension talks to."""
import ast
import json
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app
from runner import languages, llm, specter
from runner.context import function_context, missing_context, top_level_names
from runner.static import static_findings

PY = languages.get("python")
BUGGY = (Path(__file__).resolve().parent.parent / "samples" / "crashes.py").read_text()

FILE = '''import math
from collections import Counter

RATE = 2
UNUSED = 3


def helper(x):
    return x * RATE


def target(x: int) -> int:
    return helper(x) + math.floor(1.5)


def other():
    return 1


print("script code at the bottom")
'''


class ContextTest(unittest.TestCase):
    def test_keeps_what_the_function_needs_on_the_same_lines(self):
        snippet = function_context(FILE, "target")
        lines = snippet.splitlines()
        self.assertEqual(len(lines), len(FILE.splitlines()))  # line numbers unchanged
        for kept in ("import math", "from collections import Counter", "RATE = 2",
                     "def helper(x):", "def target(x: int) -> int:"):
            self.assertIn(kept, lines)
        for dropped in ("UNUSED = 3", "def other():", 'print("script code at the bottom")'):
            self.assertNotIn(dropped, snippet)
        target = "def target(x: int) -> int:"
        self.assertEqual(lines.index(target), FILE.splitlines().index(target))

    def test_decorators_are_kept(self):
        source = "import functools\n\n@functools.lru_cache\ndef f(x: int) -> int:\n    return x\n"
        self.assertIn("@functools.lru_cache", function_context(source, "f"))

    def test_missing_context_only_excuses_names_the_file_defines(self):
        names = top_level_names(ast.parse("try:\n    import json\nexcept ImportError:\n    json = None\nX = 1\n"))
        finding = {"status": "bug", "failure": {"error": "NameError", "message": "name 'X' is not defined"}}
        self.assertEqual(missing_context(finding, names), "X")
        finding["failure"]["message"] = "name 'typo' is not defined"
        self.assertIsNone(missing_context(finding, names))  # a real bug


class StaticTest(unittest.TestCase):
    def test_reports_bugs_not_style(self):
        source = "import os\n\ndef f(items=[]):\n    return undefined_name + len(items)\n"
        rules = [(f["line"], f["rule"]) for f in static_findings(source)]
        self.assertEqual(rules, [(3, "MutableDefault"), (4, "UndefinedName")])  # no UnusedImport


class BackendTest(unittest.TestCase):
    client = TestClient(app)

    def test_health_lists_every_language(self):
        health = self.client.get("/health").json()
        self.assertTrue(health["ok"])
        self.assertIn("python", health["languages"])
        self.assertIn("typescript", health["languages"]["typescript"]["vscode_ids"])
        self.assertIn("available", health["model"])
        self.assertTrue(health["layers"]["specter"]["built"])
        self.assertTrue(health["layers"]["analyst"]["built"])
        self.assertFalse(health["layers"]["oracle"]["built"])
        self.assertEqual([t["id"] for t in health["tiers"]], ["swift", "balanced", "deep", "deepest"])
        self.assertNotIn("claude", json.dumps(health["tiers"]))  # the editor never sees a supplier

    def test_the_users_model_choice_reaches_the_model(self):
        from runner import llm
        provider = llm.ScriptedProvider([{"match": "Function: pick", "answer": {"risk": "low", "risk_reason": "", "hypotheses": []}}])
        llm.set_provider(provider)
        self.addCleanup(llm.set_provider, None)
        source = "def pick(a: int) -> int:\n    return a\n"
        self.client.post("/specter", json={"source": source, "models": {"specter": "deep"}})
        self.client.post("/specter", json={"source": source.replace("a\n", "a + 0\n"), "models": {"specter": "rm -rf"}})
        self.assertEqual([c["settings"]["model"] for c in provider.calls], ["claude-opus-5-5", "claude-haiku-5-5"])

    def test_static(self):
        response = self.client.post("/static", json={"source": "def f():\n    return nope\n"}).json()
        self.assertEqual(response["findings"][0]["rule"], "UndefinedName")
        js = self.client.post("/static", json={"source": "let x = 1;", "language": "javascript"}).json()
        self.assertEqual(js, {"parsed": True, "findings": []})

    def test_unparsable_source_is_not_an_error(self):
        for path in ("/static", "/specter"):
            response = self.client.post(path, json={"source": "def f(:\n"}).json()
            self.assertEqual((response["parsed"], response["findings"]), (False, []))
        self.assertFalse(self.client.post("/triage", json={"source": "function f( {", "language": "javascript"})
                         .json()["parsed"])

    def test_unknown_languages_are_not_supported(self):
        response = self.client.post("/specter", json={"source": "x", "language": "cobol"}).json()
        self.assertEqual(response["supported"], False)

    def test_specter_finds_and_caches(self):
        start = time.monotonic()
        first = self.client.post("/specter", json={"source": BUGGY, "path": "/w/crashes.py", "model": False}).json()
        cold = time.monotonic() - start
        findings = {f["function"]: f for f in first["findings"]}
        self.assertEqual(findings["average"]["status"], "bug")
        self.assertEqual(findings["average"]["def_line"], 8)
        self.assertEqual(findings["average"]["input"]["call"], "average([])")
        self.assertEqual(findings["average"]["severity"], "error")

        # Edit one function: only that one runs again, and triage sees which function changed.
        edited = BUGGY.replace("return sum(values) / len(values)",
                               "return sum(values) / len(values) if values else 0.0")
        triage = self.client.post("/triage", json={"source": edited, "path": "/w/crashes.py"}).json()
        self.assertEqual(triage["risk"]["changed"], ["average"])
        start = time.monotonic()
        second = self.client.post("/specter", json={"source": edited, "path": "/w/crashes.py", "model": False}).json()
        second = {f["function"]: f for f in second["findings"]}
        self.assertEqual(second["average"]["status"], "no_bug_found")
        self.assertEqual(second["factorial"], findings["factorial"])
        self.assertLess(time.monotonic() - start, cold)

    def test_other_languages_by_language_id_or_path(self):
        source = ("/** @param {number[]} values */\nfunction average(values) {\n"
                  "  return values.reduce((a, b) => a + b) / values.length;\n}\n")
        for request in ({"language": "javascript"}, {"path": "/w/stats.js"}):
            answer = self.client.post("/specter", json={"source": source, "model": False, **request}).json()
            if answer.get("setup"):
                self.skipTest("no JavaScript sandbox")
            bug = next(f for f in answer["findings"] if f["status"] == "bug" and f["severity"] == "error")
            self.assertEqual((bug["input"]["exprs"], bug["failure"]["error"], bug["failure"]["line"]),
                             (["[]"], "TypeError", 3))
        replayed = self.client.post("/replay", json={"source": source, "language": "javascript",
                                                     "function": "average", "input": {"exprs": ["[]"]}}).json()
        self.assertEqual((replayed["ok"], replayed["error"]), (False, "TypeError"))

    def test_replay(self):
        result = self.client.post("/replay", json={"source": BUGGY, "function": "average",
                                                   "input": {"exprs": ["[]"]}}).json()
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "ZeroDivisionError", 10))
        # the Phase 2 extension's format: tagged JSON arguments
        fixed = self.client.post("/replay", json={"source": BUGGY, "function": "average", "args": [[2, 4]]}).json()
        self.assertEqual(fixed, {"ok": True, "result": 3.0})
        gone = self.client.post("/replay", json={"source": BUGGY, "function": "nope", "args": []}).json()
        self.assertIsNone(gone["ok"])

    def test_analyst_and_replay_with_the_expected_value(self):
        source = (Path(__file__).resolve().parent.parent / "samples" / "intent" / "intent.py").read_text()
        answer = self.client.post("/analyst", json={"source": source, "path": "intent.py", "model": False}).json()
        leap = next(f for f in answer["findings"] if f["function"] == "is_leap_year")
        self.assertEqual((leap["layer"], leap["failure"]["kind"], leap["input"]["call"]),
                         ("analyst", "wrong_result", "is_leap_year(1900)"))
        body = {"source": source, "path": "intent.py", "function": "is_leap_year", "input": leap["input"],
                "expected": leap["expected"]["value"]}
        self.assertEqual(self.client.post("/replay", json=body).json()["message"], "returned True, expected False")
        fixed = source.replace("return year % 4 == 0", "return year % 4 == 0 and year % 100 != 0")
        self.assertTrue(self.client.post("/replay", json={**body, "source": fixed}).json()["ok"])

    def test_browser_requests_are_refused(self):
        response = self.client.post("/static", json={"source": "x = 1"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)


class PythonPluginTest(unittest.TestCase):
    """The Python plugin's inputs and Specter on Python files (the worker runs them in its sandbox)."""

    def setUp(self):
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def test_literals(self):
        for expr in ("-1", "[-1.5]", "+2", "(1, 2)", "None", "[None]", "'#'", "{'a': (1,)}"):
            self.assertTrue(PY.literal_ok(expr), expr)
        for expr in ("1, 2", "1,", "...", "1 #", "{[1]}", "{[]: 1}", "{(1, [2])}", "{**{}}", "-'a'", "--1", "x",
                     "f()", "1 + 1", "1\n"):
            self.assertFalse(PY.literal_ok(expr), expr)

    def test_entry_points_and_never_returning_functions_are_skipped(self):
        source = ("import typing\nfrom typing import NoReturn\n\ndef main() -> None:\n    pass\n\n"
                  "def fail(m: str) -> NoReturn:\n    raise RuntimeError(m)\n\n"
                  "def stop(m: str) -> typing.Never:\n    raise RuntimeError(m)\n\ndef ok(m: str) -> str:\n    return m\n")
        skips = {f.name: f.skip for f in PY.functions(source)}
        self.assertIn("entry point", skips["main"])
        self.assertIn("NoReturn", skips["fail"])
        self.assertIn("Never", skips["stop"])
        self.assertIsNone(skips["ok"])

    def test_a_literal_default_says_what_an_untyped_parameter_takes(self):
        fn = PY.functions('def join_words(words: list[str], sep=" ") -> str:\n    return sep.join(words)\n')[0]
        self.assertEqual(fn.params[1].type, "default:' '")
        seps = {inp.args[1]: inp.assumptions for inp in PY.edge_inputs(fn)}
        self.assertEqual(seps["' '"], [])
        self.assertEqual(seps["''"], [])
        self.assertEqual(seps["None"], ["sep may be None"])
        self.assertNotIn("1", seps)

    def test_model_inputs_of_the_wrong_type_are_dropped(self):
        fn = PY.functions("def shout(text: str) -> str:\n    return text.upper()\n")[0]
        self.assertIsNone(PY.model_input(fn, ["123"], None))
        self.assertIsNone(PY.model_input(fn, ["['a']"], None))
        self.assertEqual(PY.model_input(fn, ["'a'"], None).assumptions, [])
        self.assertEqual(PY.model_input(fn, ["None"], None).assumptions, ["text may be None"])
        add = PY.functions("def add(a: int, b: int) -> int:\n    return a + b\n")[0]
        self.assertIsNone(PY.model_input(add, ["1, 2", "3"], None))  # would be three arguments

    def test_correct_files_have_no_findings(self):
        cases = {
            "lookup table": "SQUARES = []\nfor i in range(10):\n    SQUARES.append(i * i)\nUNITS = {}\n"
                            "UNITS.update(km=1000, m=1)\n\n\ndef square(digit: int) -> int:\n"
                            "    if digit < 0 or digit > 9:\n        return -1\n    return SQUARES[digit]\n\n\n"
                            "def to_m(n: int, unit: str) -> int:\n    if unit not in ('km', 'm'):\n        return -1\n"
                            "    return n * UNITS[unit]\n",
            "typed dict": "from typing import TypedDict\n\n\nclass Point(TypedDict):\n    x: int\n    y: int\n\n\n"
                          "def make_point(x: int, y: int) -> Point:\n    return {'x': x, 'y': y}\n",
            "protocol": "from typing import Protocol\n\n\nclass Named(Protocol):\n    name: str\n\n\nclass User:\n"
                        "    def __init__(self, name: str) -> None:\n        self.name = name\n\n\n"
                        "def make_user(name: str) -> Named:\n    return User(name)\n",
            "exit": "import sys\n\n\ndef run_cli(args: list[str]) -> int:\n    if not args:\n        sys.exit(2)\n"
                    "    return len(args)\n",
            "raise guard": "def percent(part: int, whole: int) -> int:\n    if whole == 0:\n"
                           "        raise ValueError('whole must not be zero')\n    return part * 100 // whole\n",
            "never": "from typing import NoReturn\n\n\ndef fail(message: str) -> NoReturn:\n    raise RuntimeError(message)\n",
            "main": "import sys\n\n\ndef main() -> None:\n    path = sys.argv[1]\n    with open(path) as f:\n"
                    "        print(len(f.read().split()))\n\n\nif __name__ == '__main__':\n    main()\n",
            "default": 'def join_words(words: list[str], sep=" ") -> str:\n    return sep.join(words)\n',
        }
        for label, source in cases.items():
            with self.subTest(label):
                answer = specter.run(source, "python", use_model=False)
                problems = [f for f in answer["findings"] if f["status"] == "error"
                            or f["status"] == "bug" and f["severity"] != "info"]
                self.assertEqual(problems, [])  # at most "fails when handed None"

    def test_a_file_that_does_not_load_is_an_error_not_a_clean_bill(self):
        source = "from helpers import round2\n\n\ndef average(values: list[int]) -> float:\n" \
                 "    return round2(sum(values) / len(values))\n"
        finding = specter.run(source, "python", use_model=False)["findings"][0]
        self.assertEqual(finding["status"], "error")
        self.assertIn("helpers", finding["failure"]["message"])

    def test_one_input_stuck_in_c_code_does_not_hide_the_others(self):
        source = "def ratio(n):\n    if n == -1:\n        return sum(range(10**12))\n    return 10 // n\n"
        found = {(f["input"]["call"], f["failure"]["error"]) for f in specter.run(source, "python", use_model=False)
                 ["findings"] if f["status"] == "bug"}
        self.assertEqual({("ratio(-1)", "Timeout"), ("ratio(0)", "ZeroDivisionError"), ("ratio('')", "TypeError")}, found)

    def test_odd_model_inputs_never_break_the_run_or_fake_a_bug(self):
        source = "def add(a: int, b: int) -> int:\n    return a + b\n\n\ndef other(x: int) -> int:\n    return 10 // x\n"
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 1, "bug": "x", "trigger_condition": "x", "confidence": 0.9,
             "inputs": [["...", "1"], ["1 #", "2"], ["1, 2", "3"], ["{[1]}", "1"], ["'a'", "1"]]}]}
        llm.set_provider(llm.ScriptedProvider([{"match": "Function: add", "answer": answer}]))
        findings = {f["function"]: f for f in specter.run(source, "python")["findings"]}
        self.assertEqual(findings["add"]["status"], "no_bug_found")
        self.assertEqual((findings["other"]["status"], findings["other"]["failure"]["error"]), ("bug", "ZeroDivisionError"))
        refused = {"ok": None, "message": "only plain literals can be replayed"}
        self.assertEqual(specter.replay(source, "python", "add", {"exprs": ["1, 2", "3"]}), refused)
        self.assertEqual(specter.replay(source, "python", "add", {"exprs": ["1 #", "2"]}), refused)

    def test_replay_of_negative_numbers_and_none(self):
        source = "def factorial(n: int) -> int:\n    if n == 0:\n        return 1\n    return n * factorial(n - 1)\n"
        result = specter.replay(source, "python", "factorial", {"exprs": ["-1"]})
        self.assertEqual((result["ok"], result["error"]), (False, "RecursionError"))
        result = specter.replay(source, "python", "factorial", {"exprs": ["None"]})
        self.assertEqual((result["ok"], result["error"]), (False, "TypeError"))


if __name__ == "__main__":
    unittest.main()
