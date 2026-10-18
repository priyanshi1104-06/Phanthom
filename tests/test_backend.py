"""Context extraction, static checks and the HTTP backend the extension talks to."""
import ast
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import app
from runner.context import function_context, missing_context, top_level_names
from runner.static import static_findings

BUGGY = (Path(__file__).resolve().parent.parent / "samples" / "buggy.py").read_text()

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
        first = self.client.post("/specter", json={"source": BUGGY, "path": "/w/buggy.py", "model": False}).json()
        cold = time.monotonic() - start
        findings = {f["function"]: f for f in first["findings"]}
        self.assertEqual(findings["average"]["status"], "bug")
        self.assertEqual(findings["average"]["def_line"], 8)
        self.assertEqual(findings["average"]["input"]["call"], "average([])")
        self.assertEqual(findings["average"]["severity"], "error")

        # Edit one function: only that one runs again, and triage sees which function changed.
        edited = BUGGY.replace("return sum(values) / len(values)",
                               "return sum(values) / len(values) if values else 0.0")
        triage = self.client.post("/triage", json={"source": edited, "path": "/w/buggy.py"}).json()
        self.assertEqual(triage["risk"]["changed"], ["average"])
        start = time.monotonic()
        second = self.client.post("/specter", json={"source": edited, "path": "/w/buggy.py", "model": False}).json()
        second = {f["function"]: f for f in second["findings"]}
        self.assertEqual(second["average"]["status"], "no_bug_found")
        self.assertEqual(second["factorial"], findings["factorial"])
        self.assertLess(time.monotonic() - start, cold)

    def test_other_languages_by_language_id_or_path(self):
        source = "function average(values) {\n  return values.reduce((a, b) => a + b) / values.length;\n}\n"
        for request in ({"language": "javascript"}, {"path": "/w/stats.js"}):
            answer = self.client.post("/specter", json={"source": source, "model": False, **request}).json()
            if answer.get("setup"):
                self.skipTest("no JavaScript sandbox")
            bug = next(f for f in answer["findings"] if f["status"] == "bug" and f["severity"] == "error")
            self.assertEqual((bug["input"]["exprs"], bug["failure"]["error"], bug["failure"]["line"]),
                             (["[]"], "TypeError", 2))
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

    def test_browser_requests_are_refused(self):
        response = self.client.post("/static", json={"source": "x = 1"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
