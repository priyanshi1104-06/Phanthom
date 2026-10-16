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

    def test_health(self):
        self.assertTrue(self.client.get("/health").json()["ok"])

    def test_static(self):
        response = self.client.post("/static", json={"source": "def f():\n    return nope\n"}).json()
        self.assertEqual(response["findings"][0]["rule"], "UndefinedName")

    def test_unparsable_source_is_not_an_error(self):
        for path in ("/static", "/shadow"):
            self.assertEqual(self.client.post(path, json={"source": "def f(:\n"}).json(),
                             {"parsed": False, "findings": []})

    def test_shadow_finds_and_caches(self):
        start = time.monotonic()
        first = self.client.post("/shadow", json={"source": BUGGY}).json()
        cold = time.monotonic() - start
        findings = {f["function"]: f for f in first["findings"]}
        self.assertEqual(findings["average"]["status"], "bug")
        self.assertEqual(findings["average"]["def_line"], 8)
        self.assertEqual(findings["average"]["input"]["call"], "average([])")

        # Edit one function: only that one is analysed again.
        edited = BUGGY.replace("return sum(values) / len(values)",
                               "return sum(values) / len(values) if values else 0.0")
        start = time.monotonic()
        second = {f["function"]: f for f in self.client.post("/shadow", json={"source": edited}).json()["findings"]}
        self.assertEqual(second["average"]["status"], "no_bug_found")
        self.assertEqual(second["factorial"], findings["factorial"])
        self.assertLess(time.monotonic() - start, cold)

    def test_replay(self):
        result = self.client.post("/replay", json={"source": BUGGY, "function": "average", "args": [[]]}).json()
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "ZeroDivisionError", 10))
        fixed = self.client.post("/replay", json={"source": BUGGY, "function": "average", "args": [[2, 4]]}).json()
        self.assertEqual(fixed, {"ok": True, "result": 3.0})

    def test_browser_requests_are_refused(self):
        response = self.client.post("/static", json={"source": "x = 1"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
