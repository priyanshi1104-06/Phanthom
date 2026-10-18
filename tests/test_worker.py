"""The worker, driven through the executor exactly as Phantom uses it (a separate local process)."""
import math
import os
import unittest
from unittest import mock

import worker  # only for the pure JSON helpers: no analysed code runs in this process
from runner.executor import run_job


def call(source, func_name, args, timeout=5.0, **job):
    return run_job({"mode": "call", "source": source, "func_name": func_name, "args": args, **job},
                   timeout=timeout)


def fuzz(source, func_name, **job):
    return run_job({"mode": "fuzz", "source": source, "func_name": func_name, **job}, timeout=60)


class CallTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(call("def div(a, b): return a / b", "div", [6, 3]), {"ok": True, "result": 2.0})

    def test_exception_points_at_the_failing_line(self):
        source = "def div(a, b):\n    x = 1\n    return a / b\n"
        result = call(source, "div", [1, 0])
        self.assertFalse(result["ok"])
        self.assertEqual((result["kind"], result["error"], result["message"], result["line"]),
                         ("exception", "ZeroDivisionError", "division by zero", 3))
        self.assertIn("return a / b", result["traceback"])
        self.assertNotIn("worker.py", result["traceback"])  # traceback starts at the analysed code

    def test_prints_do_not_break_the_json(self):
        source = "def noisy(x):\n    print('debug', x)\n    return x * 2\n"
        self.assertEqual(call(source, "noisy", [5]), {"ok": True, "result": 10})

    def test_infinite_loop_is_a_timeout_with_its_line(self):
        source = "def spin(n):\n    while True:\n        n += 1\n"
        result = call(source, "spin", [0], call_timeout=0.3)
        self.assertEqual(result["kind"], "timeout")
        self.assertIn(result["line"], (2, 3))  # stopped somewhere inside the loop

    def test_timeout_survives_except_everything(self):
        source = ("def stubborn():\n    while True:\n        try:\n            pass\n"
                  "        except BaseException:\n            pass\n")
        result = call(source, "stubborn", [], call_timeout=0.3)
        self.assertEqual(result["kind"], "timeout")

    def test_wrong_return_type(self):
        source = ("def find(xs: list[int], t: int) -> int:\n"
                  "    for i, x in enumerate(xs):\n        if x == t:\n            return i\n")
        result = call(source, "find", [[], 0])
        self.assertEqual((result["kind"], result["error"], result["line"]), ("return_type", "ReturnTypeError", 1))

    def test_optional_return_is_allowed_to_be_none(self):
        source = "def find(xs: list[int]) -> int | None:\n    return None\n"
        self.assertTrue(call(source, "find", [[]])["ok"])

    def test_sys_exit_is_a_failure_not_a_crash(self):
        result = call("import sys\ndef quit_(x):\n    sys.exit(3)\n", "quit_", [1])
        self.assertEqual((result["kind"], result["error"]), ("exception", "SystemExit"))

    def test_syntax_error_is_reported(self):
        result = call("def broken(:\n    pass\n", "broken", [])
        self.assertEqual((result["kind"], result["error"]), ("error", "SyntaxError"))

    def test_missing_function(self):
        result = call("def f(): pass", "g", [])
        self.assertEqual((result["kind"], result["error"]), ("error", "LookupError"))

    def test_hard_exit_is_a_crash(self):
        result = call("import os\ndef die():\n    os._exit(3)\n", "die", [])
        self.assertEqual(result["kind"], "crash")
        self.assertIn("code 3", result["message"])

    def test_executor_kills_code_the_worker_cannot_interrupt(self):
        result = call("while True:\n    pass\ndef f(): pass\n", "f", [], timeout=1)  # loops while loading
        self.assertEqual(result["kind"], "timeout")

    def test_memory_is_capped(self):
        result = call("def hog():\n    return len(bytearray(4 * 1024 ** 3))\n", "hog", [])
        self.assertEqual(result["error"], "MemoryError")

    def test_secrets_in_the_backend_environment_do_not_reach_the_code(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-must-not-leak"}):
            result = call("import os\ndef peek():\n    return os.environ.get('ANTHROPIC_API_KEY')\n", "peek", [])
        self.assertEqual(result, {"ok": True, "result": None})

    def test_tagged_arguments_arrive_exactly(self):
        source = "def kinds(t, s, b, f):\n    return [type(t).__name__, type(s).__name__, b, f != f]\n"
        args = [{"$tuple": [1, 2]}, {"$set": [3]}, {"$bytes": "aGk="}, {"$float": "nan"}]
        self.assertEqual(call(source, "kinds", args)["result"], ["tuple", "set", {"$bytes": "aGk="}, True])


class FuzzTest(unittest.TestCase):
    def test_finds_the_minimal_failing_input(self):
        result = fuzz("def average(xs: list[int]) -> float:\n    return sum(xs) / len(xs)\n", "average")
        self.assertEqual(result["status"], "bug")
        self.assertEqual(result["input"], {"args": [[]], "call": "average([])"})
        self.assertEqual(result["failure"]["error"], "ZeroDivisionError")

    def test_input_is_recorded_before_the_function_mutates_it(self):
        source = "def drain(xs: list[int]) -> int:\n    xs.clear()\n    return 1 // 0 if len(xs) == 0 else 1\n"
        result = fuzz(source, "drain")
        self.assertEqual(result["input"]["args"], [[]])  # the generated input, not the emptied list

    def test_correct_function(self):
        result = fuzz("def double(x: int) -> int:\n    return 2 * x\n", "double", max_examples=50)
        self.assertEqual(result, {"status": "no_bug_found", "examples": 50})

    def test_missing_type_hint_is_skipped(self):
        result = fuzz("def f(x):\n    return x\n", "f")
        self.assertEqual(result["status"], "skipped")
        self.assertIn("'x'", result["reason"])

    def test_untyped_parameter_with_default_uses_the_default(self):
        result = fuzz("def f(x: int, scale=0):\n    return x // scale\n", "f")
        self.assertEqual(result["failure"]["error"], "ZeroDivisionError")


class EncodingTest(unittest.TestCase):
    def test_round_trip(self):
        values = [None, True, 3, 2 ** 80, -0.0, "s", [1, (2, 3)], (), {1, 2}, frozenset({"a"}),
                  b"\x00\xff", 1 + 2j, {"a": 1}, {1: "x", (2, 3): None}, {"$tuple": 1}]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(worker.decode(worker.encode(value)), value)

    def test_non_finite_floats(self):
        for value in (math.inf, -math.inf):
            self.assertEqual(worker.decode(worker.encode(value)), value)
        self.assertTrue(math.isnan(worker.decode(worker.encode(math.nan))))

    def test_unencodable_value(self):
        self.assertIsNone(worker.encode_or_none(object()))


if __name__ == "__main__":
    unittest.main()
