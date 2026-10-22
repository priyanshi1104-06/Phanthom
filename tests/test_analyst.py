"""Analyst: expectations from the code's own documentation and from the model, run and compared in the
sandbox, mismatches replayed. The Python sample runs for real (local process or Docker)."""
import unittest
from pathlib import Path

from runner import analyst, languages, llm
from runner.languages.base import Function, Param

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "intent"
PY = languages.get("python")


def fn(name, text, params=("x",), line=1):
    lines = text.count("\n") + 1
    return Function(name, line, line + lines - 1, [Param(p, "int") for p in params], "int", text)


class DocumentationTest(unittest.TestCase):
    def examples(self, language, source, name):
        lang = languages.get(language)
        target = next(f for f in lang.functions(source) if f.name.endswith(name))
        return [(e["args"], e["expected"]) for e in analyst.documented_examples(lang, source, target)]

    def test_python_doctest_and_inline_forms(self):
        source = ('def double(x: int) -> int:\n    """Twice x: double(2) == 4, double(-1) -> -2.\n\n'
                  '    >>> double(0)\n    0\n    >>> double(3)\n    6\n    """\n    return x * 2\n')
        self.assertEqual(self.examples("python", source, "double"),
                         [(["2"], "4"), (["0"], "0"), (["3"], "6")])  # the first example of a line: not double(-1)

    def test_comment_styles_of_other_languages(self):
        js = "// clamp(15, 0, 10) => 10\nfunction clamp(v, lo, hi) { return v; }\n"
        self.assertEqual(self.examples("javascript", js, "clamp"), [(["15", "0", "10"], "10")])
        rust = "/// assert_eq!(double(2), 4);\nfn double(x: i32) -> i32 { x * 2 }\n"
        self.assertEqual(self.examples("rust", rust, "double"), [(["2"], "4")])
        ruby = "# double(2) # => 4\ndef double(x)\n  x * 2\nend\n"
        self.assertEqual(self.examples("ruby", ruby, "double"), [(["2"], "4")])
        go = "package main\n\n// Double(2) returns 4.\nfunc Double(x int) int { return x * 2 }\n"
        self.assertEqual(self.examples("go", go, "Double"), [(["2"], "4")])

    def test_code_is_not_documentation(self):
        source = ('@cache\ndef double(x: int) -> int:\n    """Twice x.\n\n    double(2) == 4\n    """\n'
                  '    if x == 99:\n        return int(double(1) == 3)  # double(5) == 10\n    return x * 2\n')
        self.assertEqual(self.examples("python", source, "double"), [(["2"], "4")])

    def test_prose_and_non_literals_are_not_examples(self):
        source = ('def double(x: int) -> int:\n    """double(n) is twice n; double(2) returns the answer;\n'
                  '    double(len("ab")) == 4"""\n    return x * 2\n')
        self.assertEqual(self.examples("python", source, "double"), [])

    def test_splitting_arguments(self):
        self.assertEqual(analyst.split_args('1, "a, b", [2, 3], {"k": (4, 5)}'),
                         ["1", '"a, b"', "[2, 3]", '{"k": (4, 5)}'])
        self.assertEqual(analyst.split_args(""), [])
        self.assertIsNone(analyst.split_args("[1, 2"))


class ModelTest(unittest.TestCase):
    SOURCE = 'def vowels(text: str) -> int:\n    """Vowels, upper or lower case."""\n    return 0\n'

    def expectations(self, *items):
        target = PY.functions(self.SOURCE)[0]
        return analyst.model_expectations(PY, self.SOURCE, target, {"expectations": list(items)})

    def item(self, **changes):
        return {"args": ['"AE"'], "expected": "2", "basis": "docstring", "quote": "upper or lower case",
                "why": "both count", "confidence": 0.9, **changes}

    def test_kept_only_with_exact_evidence_confidence_and_literals(self):
        self.assertEqual(len(self.expectations(self.item())), 1)
        self.assertEqual(self.expectations(self.item(quote="counts accented vowels too")), [])
        self.assertEqual(self.expectations(self.item(confidence=0.5)), [])
        self.assertEqual(self.expectations(self.item(args=["input()"])), [])
        self.assertEqual(self.expectations(self.item(expected="len('ae')")), [])
        self.assertEqual(self.expectations(self.item(args=['"a"', '"b"'])), [])
        self.assertEqual(self.expectations(self.item(quote="upper   or\nlower case")), [self.item(
            quote="upper or lower case", confidence=0.9)])  # whitespace doesn't matter

    def test_no_answer_is_not_cached(self):
        with self.assertRaises(analyst.NoAnswer):
            analyst.answered(None)


class CompareTest(unittest.TestCase):
    def test_same(self):
        self.assertTrue(analyst.same("212.0", "212"))
        self.assertTrue(analyst.same("0.30000000000000004", "0.3"))
        self.assertTrue(analyst.same("1.5f", "1.5"))
        self.assertFalse(analyst.same("203.0", "212.0"))
        self.assertFalse(analyst.same("'1'", "1"))
        self.assertIsNone(analyst.shown({"ok": True, "result": "[1, 2, ..."[:8] + "..."}))
        self.assertIsNone(analyst.shown({"ok": False, "kind": "exception"}))


class GraphTest(unittest.TestCase):
    def test_callers_and_dependencies(self):
        a = fn("a", "def a(x):\n    return b(x) + 1")
        b = fn("b", "def b(x):\n    return c(x)")
        c = fn("c", "def c(x):\n    return x")
        d = fn("d", "def d(x):\n    return x  # not b (x)")
        graph = analyst.call_graph([a, b, c, d])
        self.assertEqual(graph, {"a": {"b"}, "b": {"c"}, "c": set(), "d": {"b"}})  # "b (x)" in a comment counts
        self.assertEqual(analyst.depends_on("a", graph), {"a", "b", "c"})
        self.assertEqual(analyst.callers_of(["c"], graph), {"a", "b", "d"})
        self.assertEqual(analyst.short_name("Stats.average"), "average")
        self.assertEqual(analyst.short_name("App\\Util\\clamp"), "clamp")


@unittest.skipUnless(PY.sandbox(), "no Python sandbox")
class PythonTest(unittest.TestCase):
    def tearDown(self):
        llm.set_provider(None)

    def run_file(self, source, **options):
        return analyst.run(source, "python", "x.py", **options)

    def bugs(self, answer):
        return {f["function"]: f for f in answer["findings"] if f["status"] == "bug"}

    def test_documented_examples_need_no_model(self):
        answer = self.run_file((SAMPLES / "intent.py").read_text(), use_model=False)
        bugs = self.bugs(answer)
        self.assertEqual(sorted(bugs), ["celsius_to_fahrenheit", "clamp", "is_leap_year"])
        leap = bugs["is_leap_year"]
        self.assertEqual((leap["input"]["call"], leap["actual"], leap["expected"]["value"], leap["severity"]),
                         ("is_leap_year(1900)", "True", "False", "error"))
        self.assertEqual(leap["failure"]["message"], "returned True, expected False")
        self.assertEqual(leap["expected"]["quote"], ">>> is_leap_year(1900)")
        self.assertEqual(bugs["celsius_to_fahrenheit"]["actual"], "203.0")

    def test_model_expectations(self):
        rules = [
            {"layer": "analyst", "match": "Function: count_vowels", "answer": {"expectations": [
                {"args": ['"AEIOU"'], "expected": "5", "basis": "docstring", "quote": "upper or lower case",
                 "why": "upper-case vowels count", "confidence": 0.95},
                {"args": ['"sky"'], "expected": "1", "basis": "docstring", "quote": "y counts as a vowel",
                 "why": "made up", "confidence": 0.95},  # not in the code: dropped
                {"args": ['"banana"'], "expected": "3", "basis": "name", "quote": "count_vowels",
                 "why": "three a's", "confidence": 0.9},
            ]}},
            {"layer": "analyst", "match": "Function: median", "answer": {"expectations": [
                {"args": ["[1, 2, 3, 4]"], "expected": "2.5", "basis": "docstring",
                 "quote": "the mean of the two middle values", "why": "(2 + 3) / 2", "confidence": 0.9},
            ]}},
            {"layer": "analyst", "match": "", "answer": {"expectations": []}},  # nothing to expect elsewhere
        ]
        llm.set_provider(llm.ScriptedProvider(rules))
        first = self.run_file((SAMPLES / "intent.py").read_text())
        self.assertEqual(first["pending"], 1)  # the model reads 4 functions per run: median waits
        self.assertNotIn("median", self.bugs(first))
        bugs = self.bugs(self.run_file((SAMPLES / "intent.py").read_text()))
        vowels = bugs["count_vowels"]
        self.assertEqual((vowels["input"]["call"], vowels["actual"], vowels["severity"], vowels["origin"]),
                         ('count_vowels("AEIOU")', "0", "warning", "intent"))
        self.assertEqual(vowels["expected"]["why"], "upper-case vowels count")
        self.assertEqual(bugs["median"]["failure"]["message"], "returned 3.0, expected 2.5")

    def test_an_edit_that_breaks_a_caller_shows_on_the_caller(self):
        def source(factor):
            return (f"def double(x: int) -> int:\n    return x * {factor}\n\n\n"
                    "def quadruple(x: int) -> int:\n    \"\"\"quadruple(2) == 8\"\"\"\n    return double(double(x))\n")
        self.assertEqual(self.bugs(self.run_file(source(2), use_model=False)), {})
        broken = self.bugs(self.run_file(source(3), use_model=False))
        self.assertEqual(list(broken), ["quadruple"])
        self.assertEqual(broken["quadruple"]["actual"], "18")

    def test_a_different_answer_each_time_is_not_reported(self):
        source = ('import random\n\n\ndef roll(sides: int) -> int:\n    """roll(1) == 2"""\n'
                  '    return random.randint(1, 10 ** 9)\n')
        self.assertEqual(self.bugs(self.run_file(source, use_model=False)), {})

    def test_replay_compares_with_the_expected_value(self):
        source = (SAMPLES / "intent.py").read_text()
        still = analyst.replay(source, "python", "is_leap_year", {"exprs": ["1900"]}, "False")
        self.assertEqual((still["ok"], still["kind"], still["message"]),
                         (False, "wrong_result", "returned True, expected False"))
        fixed = source.replace("return year % 4 == 0", "return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)")
        self.assertEqual(analyst.replay(fixed, "python", "is_leap_year", {"exprs": ["1900"]}, "False")["ok"], True)


if __name__ == "__main__":
    unittest.main()
