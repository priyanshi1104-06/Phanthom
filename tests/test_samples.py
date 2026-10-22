"""Phase 1 "done when": a reproducible failing input for at least 8 of the 10 buggy samples,
and nothing reported for the correct ones."""
import unittest
from pathlib import Path

from runner.analyzer import analyze_source

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

EXPECTED = {  # function -> error of its confirmed repro
    "average": "ZeroDivisionError",
    "split_bill": "ZeroDivisionError",
    "pairwise_diffs": "IndexError",
    "find_index": "ReturnTypeError",
    "display_name": "KeyError",
    "parse_port": "IndexError",
    "initials": "IndexError",
    "collatz_steps": "Timeout",
    "factorial": "RecursionError",
}


class SamplesTest(unittest.TestCase):
    def test_buggy_samples(self):
        findings = {f["function"]: f for f in analyze_source((SAMPLES / "crashes.py").read_text())}
        self.assertEqual(len(findings), 10)
        for name, error in EXPECTED.items():
            with self.subTest(function=name):
                self.assertEqual(findings[name]["status"], "bug")
                self.assertEqual(findings[name]["failure"]["error"], error)
        # A wrong answer without a crash needs an intent check (Analyst), not a crash search.
        self.assertEqual(findings["is_leap_year"]["status"], "no_bug_found")
        self.assertGreaterEqual(sum(f["status"] == "bug" for f in findings.values()), 8)

    def test_correct_samples_have_no_findings(self):
        findings = analyze_source((SAMPLES / "correct.py").read_text())
        self.assertEqual([f["status"] for f in findings], ["no_bug_found"] * 3)


if __name__ == "__main__":
    unittest.main()
