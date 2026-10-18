"""Ruby plugin: parsing, literal checking, inputs, and Specter end to end in the ruby:3.4 sandbox."""
import unittest
from pathlib import Path

from runner import languages, llm, specter

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "ruby"
RB = languages.get("ruby")


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


class ParsingTest(unittest.TestCase):
    SOURCE = """require "json"
LIMIT = 10
def average(values, n = 3, key: 1)
  values.sum / values.size
end
def kw(a, key:); end
def rest(*args); end
def with_block(&blk); end
def each_twice(x)
  yield x
end
def valid?(x) = x > 0
private def hidden(s)
  s
end
class Stats
  def self.mean(xs) = xs.sum
  def total(y); end
end
module Util
  module_function
  def clean(s) = s.strip
end
def +(other); end
puts average([1])
main
"""

    def test_functions_and_parameters(self):
        found = {f.name: f for f in RB.functions(self.SOURCE)}
        self.assertEqual(list(found), ["average", "kw", "rest", "with_block", "each_twice", "valid?", "hidden",
                                       "Stats.mean", "Stats#total", "Util.clean"])
        average = found["average"]
        self.assertEqual((average.line, average.end_line, average.returns), (3, 5, None))
        self.assertEqual([(p.name, p.type) for p in average.params], [("values", None), ("n", "default:3")])
        self.assertIsNone(average.skip)  # a keyword parameter with a default is left to its default
        for name in ("kw", "rest", "with_block", "each_twice", "Stats#total"):
            self.assertIsNotNone(found[name].skip, name)
        for name in ("valid?", "hidden", "Stats.mean", "Util.clean"):
            self.assertIsNone(found[name].skip, name)
        self.assertEqual((found["hidden"].line, found["Stats.mean"].line), (13, 17))

    def test_only_definitions_run_at_load(self):
        context = RB.context(self.SOURCE, None)
        self.assertEqual(context.count("\n"), self.SOURCE.count("\n"))
        self.assertNotIn("puts", context)
        self.assertNotIn("main", context)
        for kept in ('require "json"', "LIMIT = 10", "private def hidden(s)", "module_function"):
            self.assertIn(kept, context)


class LiteralTest(unittest.TestCase):
    def test_plain_literals_pass(self):
        for expr in ("1", "-1", "+3", "0.5", "-1.5", "1_000", "0x1f", "3r", "2i", '"a"', "'b'", '"a\\nb"', "?a",
                     ":sym", ':"a b"', ":empty?", "%w[a b]", "%i[a b]", "%q(x)", "nil", "true", "false", "[]",
                     "[1, [2, nil]]", "{}", '{a: 1, "b" => 2, 3 => nil}', '{"a": [1]}', "1..3", '"a".."c"', "1..",
                     "Float::INFINITY", "-Float::INFINITY", "Float::NAN", "'a' \"b\""):
            self.assertTrue(RB.literal_ok(expr), expr)

    def test_anything_that_runs_code_is_rejected(self):
        for expr in ("x", "foo()", "1 + 1", '"#{x}"', '"#{`id`}"', '"#$0"', '"#@x"', ':"#{x}"', "%W[#{x}]",
                     "%{#{x}}", "'#{x}'", "`id`", "%x(id)", "system('id')", "exit", "Kernel.exit!", "-> { 1 }",
                     "lambda { 1 }", "proc { 1 }", "__FILE__", "__method__", "$0", "@x", "self", "!true", "not true",
                     "defined?(x)", "-x", "+-1", "-2.abs", "1, 2", "1], [2", "1]; system('id'); [1", "a: 1",
                     "{a => 1}", "{**x}", "[*x]", "Set[1]", "Object.new", "Float::INFINITY.to_s", "::Float::INFINITY",
                     "<<~EOS", "/ab/", "1 # comment", "1\n", "1\x04", "1\x00", "(1)", "1 if true", "1; 2",
                     "ENV['HOME']", "File.read('/etc/passwd')", '{a: system("id")}', "[1, foo]", "1..x",
                     "__END__", "BEGIN { }"):
            self.assertFalse(RB.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_names_and_defaults_decide_the_values(self):
        values = dict(RB.edge_values(None, "values"))
        self.assertIsNone(values["[]"])  # an empty array is an array: no assumption
        self.assertEqual(values["nil"], "values may be nil")
        self.assertIn('[""]', dict(RB.edge_values(None, "words")))  # a list of strings
        self.assertEqual(RB.edge_values(None, "count")[0], ("3", None))
        self.assertTrue(all(assumption for _, assumption in RB.edge_values(None, "thing")))  # unknown kind
        self.assertTrue(all(assumption for _, assumption in RB.edge_values(None, "user")))  # unknown keys
        self.assertEqual(RB.edge_values("default:nil", "name")[-1], ("nil", None))  # the default allows nil
        self.assertEqual(RB.edge_values("default:[]", "thing")[0], ("[1, 2, 3]", None))
        self.assertEqual(RB.edge_values("default::asc", "order")[0], (":asc", None))
        self.assertTrue(all(RB.literal_ok(v) for kind in ("values", "text", "user", "thing", "is_on")
                            for v, _ in RB.edge_values(None, kind)))

    def test_an_index_stays_inside_its_array(self):
        fn = RB.functions("def nth(values, index)\n  values.fetch(index)\nend\n")[0]
        inputs = {tuple(i.args): i.assumptions for i in RB.edge_inputs(fn)}
        self.assertEqual(inputs[("[1, 2, 3]", "1")], [])
        self.assertEqual(inputs[("[1, 2, 3]", "-1")], [])  # -1 is the last element in Ruby
        self.assertEqual(inputs[("[]", "1")], ["index may be out of range for values"])

    def test_model_inputs_carry_the_untyped_assumption(self):
        fn = RB.functions("def f(name = nil, n)\n  n\nend\n")[0]
        made = RB.model_input(fn, ["nil", "nil"], {})
        self.assertEqual(made.assumptions, ["n may be nil",
                                            "the parameters have no types, so this input is assumed to be allowed"])
        self.assertIsNone(RB.model_input(fn, ['"#{exit}"', "1"], {}))


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if RB.sandbox() != "docker":
            self.skipTest("no Ruby sandbox: python phantom.py setup ruby")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def test_samples(self):
        answer = specter.run((SAMPLES / "buggy.rb").read_text(), "ruby", use_model=False)
        found = bugs(answer)
        expected = {"average": ("ZeroDivisionError", 7), "median": ("IndexError", 12),
                    "first_word": ("NoMethodError", 16), "month_number": ("KeyError", 20),
                    "badge": ("TypeError", 24), "percent": ("FloatDomainError", 28), "chunks": ("ArgumentError", 32),
                    "collatz_steps": ("Timeout", 35), "digit_sum": ("SystemStackError", 45)}
        for name, (error, line) in expected.items():
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[name])
        self.assertNotIn("leap_year?", found)  # a wrong answer: needs an intent check
        loop = next(f for f in answer["findings"] if f["function"] == "collatz_steps" and f["severity"] == "error")
        self.assertEqual(loop["failure"]["kind"], "timeout")
        correct = specter.run((SAMPLES / "correct.rb").read_text(), "ruby", use_model=False)
        self.assertEqual([f["status"] for f in correct["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "buggy.rb").read_text()
        result = specter.replay(source, "ruby", "average", {"exprs": ["[]"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "ZeroDivisionError", 7))
        self.assertEqual(specter.replay(source, "ruby", "average", {"exprs": ["[2, 4]"]}), {"ok": True, "result": "3"})

    def test_module_functions_run_and_missing_files_are_build_errors(self):
        source = "module Stats\n  def self.mean(values)\n    values.sum / values.size\n  end\nend\n"
        found = bugs(specter.run(source, "ruby", use_model=False))
        self.assertIn(("ZeroDivisionError", 3, "error"), found["Stats.mean"])
        broken = specter.run('require_relative "helper"\n\ndef twice(n)\n  n * 2\nend\n', "ruby", use_model=False)
        self.assertEqual([f["status"] for f in broken["findings"]], ["error"])

    def test_model_inputs_are_run_and_injections_dropped(self):
        source = ('RATES = { "SAVE10" => 10, "SAVE20" => 20 }.freeze\n\ndef discount(code)\n'
                  '  return 0 unless code.start_with?("SAVE")\n\n  RATES.fetch(code)\nend\n')
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 6, "bug": "unknown SAVE codes", "trigger_condition": "", "confidence": 0.8,
             "inputs": [['"SAVE30"'], ['system("id")'], ['"#{exit 1}"'], ["`id`"], ['RATES.fetch("x")']]}]}
        provider = llm.ScriptedProvider([{"match": "discount", "answer": answer}])
        llm.set_provider(provider)
        result = specter.run(source, "ruby")
        found = [f for f in result["findings"] if f["status"] == "bug" and f["severity"] != "info"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["error"]) for f in found],
                         [("model", 'discount("SAVE30")', "KeyError")])
        self.assertEqual(found[0]["failure"]["line"], 6)
        self.assertIn("<code-", provider.calls[0]["prompt"])  # the code is fenced as data

    def test_a_model_input_ruby_rejects_does_not_hide_edge_findings(self):
        source = "def mean(values)\n  values.sum / values.size\nend\n"
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 2, "bug": "b", "trigger_condition": "", "confidence": 0.9,
             "inputs": [["[08]"]]}]}  # tree-sitter reads 08 as a number, Ruby rejects it: the build fails
        llm.set_provider(llm.ScriptedProvider([{"match": "mean", "answer": answer}]))
        found = [f for f in specter.run(source, "ruby")["findings"] if f["status"] == "bug"]
        self.assertIn(("edge", "mean([])", "ZeroDivisionError"),
                      [(f["origin"], f["input"]["call"], f["failure"]["error"]) for f in found])
        self.assertNotIn("model", [f["origin"] for f in found])


if __name__ == "__main__":
    unittest.main()
