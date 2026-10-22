"""Analyst in every language: the examples in samples/intent/ are read from each language's own comment
style, run in its sandbox and compared with the expected values (no model). Skips a language whose
sandbox isn't set up (python phantom.py setup)."""
import unittest
from pathlib import Path

from runner import analyst, languages

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "intent"
CASES = {  # language: (file, the three wrong functions, the correct one, true, rendering of 203)
    "javascript": ("intent.js", ("isLeapYear", "clamp", "toFahrenheit"), "maxOf", "true", "203"),
    "typescript": ("intent.ts", ("isLeapYear", "clamp", "toFahrenheit"), "maxOf", "true", "203"),
    "go": ("intent.go", ("IsLeapYear", "Clamp", "ToFahrenheit"), "MaxOf", "true", "203"),
    "java": ("Intent.java", ("Intent.isLeapYear", "Intent.clamp", "Intent.toFahrenheit"), "Intent.maxOf", "true",
             "203.0"),
    "c": ("intent.c", ("is_leap_year", "clamp", "to_fahrenheit"), "max_of", "true", "203"),
    "cpp": ("intent.cpp", ("is_leap_year", "clamp", "to_fahrenheit"), "max_of", "true", "203"),
    "rust": ("intent.rs", ("is_leap_year", "clamp", "to_fahrenheit"), "max_of", "true", "203.0"),
    "ruby": ("intent.rb", ("is_leap_year", "clamp", "to_fahrenheit"), "max_of", "true", "203.0"),
    "php": ("intent.php", ("is_leap_year", "clamp", "to_fahrenheit"), "max_of", "true", "203.0"),
    "python": ("intent.py", ("is_leap_year", "clamp", "celsius_to_fahrenheit"), None, "True", "203.0"),
}


class LanguagesTest(unittest.TestCase):
    def test_wrong_results_in_every_language(self):
        for language, (file, wrong, correct, true, fahrenheit) in CASES.items():
            lang = languages.get(language)
            with self.subTest(language=language):
                if lang.sandbox() is None:
                    self.skipTest(f"no {language} sandbox")
                answer = analyst.run((SAMPLES / file).read_text(), language, file, use_model=False)
                bugs = {f["function"]: f for f in answer["findings"] if f["status"] == "bug"}
                self.assertEqual(sorted(bugs), sorted(wrong))
                leap, clamp, convert = (bugs[name] for name in wrong)
                self.assertTrue(leap["input"]["call"].endswith("(1900)"))
                self.assertEqual((leap["actual"], clamp["actual"], convert["actual"]), (true, "0", fahrenheit))
                self.assertTrue(all(b["severity"] == "error" and b["origin"] == "example" for b in bugs.values()))
                if correct:
                    ok = [f for f in answer["findings"] if f["function"] == correct]
                    self.assertEqual([f["status"] for f in ok], ["no_bug_found"])

    def test_php_namespace(self):
        php = languages.get("php")
        if php.sandbox() is None:
            self.skipTest("no php sandbox")
        source = "<?php\nnamespace App\\Calc;\n\n/** twice(2) === 4 */\nfunction twice(int $x): int { return $x * 3; }\n"
        bugs = [f for f in analyst.run(source, "php", "x.php", use_model=False)["findings"] if f["status"] == "bug"]
        self.assertEqual([b["failure"]["message"] for b in bugs], ["returned 6, expected 4"])

    def test_a_broken_caller_in_a_compiled_language(self):
        go = languages.get("go")
        if go.sandbox() is None:
            self.skipTest("no go sandbox")

        def source(factor):
            return (f"package calc\n\nfunc Double(x int) int {{ return x * {factor} }}\n\n"
                    "// Quadruple(2) == 8\nfunc Quadruple(x int) int { return Double(Double(x)) }\n")
        self.assertEqual([f["status"] for f in analyst.run(source(2), "go", "x.go", use_model=False)["findings"]],
                         ["no_bug_found"])
        broken = analyst.run(source(3), "go", "x.go", use_model=False)["findings"]
        self.assertEqual([(f["function"], f.get("actual")) for f in broken], [("Quadruple", "18")])


if __name__ == "__main__":
    unittest.main()
