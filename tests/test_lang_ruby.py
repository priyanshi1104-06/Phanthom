"""Ruby plugin: parsing, literal checking, inputs, and Specter end to end in the ruby:3.4 sandbox."""
import unittest
from pathlib import Path

from runner import languages, llm, specter
from runner.languages.base import Input
from runner.languages.ruby import FAMILY_VALUES

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

    def test_main_is_not_run_as_a_function(self):
        found = {f.name: f for f in RB.functions("def main\n  puts 1\nend\nmodule M\n  def self.main = 1\nend\n")}
        self.assertEqual(found["main"].skip, "the program's entry point is not run")
        self.assertIsNone(found["M.main"].skip)

    def test_yard_param_types_are_read(self):
        source = ('# Joins.\n# @param values [Array<Integer>] the numbers\n# @param [String] sep\n'
                  'def join(values, sep = ",", extra = nil)\n  values.join(sep)\nend\n')
        fn = RB.functions(source)[0]
        self.assertEqual([(p.name, p.type) for p in fn.params],
                         [("values", "[Array<Integer>]"), ("sep", '[String] default:","'), ("extra", "default:nil")])

    def test_top_level_code_that_runs_the_program_is_blanked(self):
        source = ('WORDS = %w[a b]\nINDEX = {}\nWORDS.each { |w| INDEX[w] = true }\nWORDS.each { |w| puts w }\n'
                  'def f(x) = x\nf(1)\np f(2)\n$stdout.puts "x"\nApp.new.run\nmain if __FILE__ == $0\n'
                  'if $PROGRAM_NAME == __FILE__\n  f(3)\nend\nabort("usage") if ARGV.empty?\n'
                  'while (line = gets)\n  f(line)\nend\ndefine_method(:g) { puts 1 }\nresult = f(4)\n'
                  'begin\n  require "set"\nrescue LoadError\n  warn "no set"\n  exit 1\nend\n')
        lines = RB.context(source, None).splitlines()
        kept = [line for line in lines if line.strip()]
        self.assertEqual(kept, ["WORDS = %w[a b]", "INDEX = {}", "WORDS.each { |w| INDEX[w] = true }", "def f(x) = x",
                                "f(1)", "define_method(:g) { puts 1 }", "result = f(4)", "begin", '  require "set"',
                                "rescue LoadError", '  warn "no set"', "  exit 1", "end"])


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
        self.assertEqual(values["[]"], "values is an array (guessed from its name)")
        self.assertEqual(values["[0]"], "values is an array of numbers (guessed from its name)")
        self.assertEqual(values["nil"], "values may be nil")
        self.assertIn('[""]', dict(RB.edge_values(None, "words")))  # a list of strings
        self.assertEqual(RB.edge_values(None, "count")[0], ("3", "count is an integer (guessed from its name)"))
        self.assertTrue(all(assumption for _, assumption in RB.edge_values(None, "thing")))  # unknown kind
        self.assertTrue(all(assumption for _, assumption in RB.edge_values(None, "user")))  # unknown keys
        self.assertEqual(RB.edge_values("default:nil", "name")[-1], ("nil", None))  # the default allows nil
        self.assertEqual(RB.edge_values("default:[]", "thing")[:2], [("[]", None), ("[1, 2, 3]", "thing may contain numbers")])
        self.assertEqual(RB.edge_values("default::asc", "order")[0], (":asc", None))
        self.assertEqual(RB.edge_values("default:3", "limit")[:3], [("3", None), ("0", None), ("-1", None)])
        self.assertTrue(all(RB.literal_ok(v) for kind in ("values", "text", "user", "thing", "is_on")
                            for v, _ in RB.edge_values(None, kind)))
        self.assertTrue(all(RB.literal_ok(v) for values in FAMILY_VALUES.values() for v in values))

    def test_doc_types_are_declared(self):
        self.assertEqual(RB.edge_values("[Array<Integer>]", "items"),
                         [("[1, 2, 3]", None), ("[]", None), ("[0]", None), ("[-1]", None), ("nil", "items may be nil")])
        self.assertEqual(RB.edge_values("[String, nil]", "x")[-1], ("nil", None))
        self.assertEqual(RB.edge_values("[Hash{String => Integer}, nil]", "counts"),
                         [("{}", None), ('{"a" => 1}', None), ("nil", None)])
        self.assertEqual(RB.edge_values("[Integer]", "index")[0], ("1", None))  # an index, as by name
        self.assertEqual(RB.edge_values("[Array<String>]", "rows")[0], ('["a", "b", "c"]', None))
        self.assertEqual(RB.edge_values("[User]", "user")[0][1], "user is a hash (guessed from its name)")

    def test_a_kind_guessed_from_a_name_or_default_is_an_assumption(self):
        # finding: order_total(items), normalize_tags(tags = []), to_utf8(text, from = "ISO-8859-1") were red
        self.assertEqual(dict(RB.edge_values(None, "items"))["[0]"], "items is an array of numbers (guessed from its name)")
        tags = dict(RB.edge_values("default:[]", "tags"))
        self.assertIsNone(tags["[]"])
        self.assertEqual(tags['["a", "b", "c"]'], "tags contains strings (guessed from its name)")
        encoding = RB.edge_values('default:"ISO-8859-1"', "from")
        self.assertEqual(encoding[0], ('"ISO-8859-1"', None))  # the default is the typical value
        self.assertTrue(all(assumption for _, assumption in encoding[1:]))  # any other string is an option guess

    def test_a_hash_default_is_its_own_typical_value(self):
        # finding: options = {} made {a: 1} typical, so every finding carried 'options may be {a: 1}'
        self.assertEqual(RB.edge_values("default:{}", "options"),
                         [("{}", None), ("{a: 1}", "options may be {a: 1}"), ("nil", "options may be nil")])
        fn = RB.functions("def initial(text, options = {})\n  text[0]\nend\n")[0]
        for inp in RB.edge_inputs(fn):
            if inp.args[1] == "{}":
                self.assertFalse([a for a in inp.assumptions if a.startswith("options")], inp.args)

    def test_an_index_stays_inside_its_array(self):
        fn = RB.functions("def nth(values, index)\n  values.fetch(index)\nend\n")[0]
        inputs = {tuple(i.args): i.assumptions for i in RB.edge_inputs(fn)}
        guess = ["values is an array of numbers (guessed from its name)", "index is an integer index (guessed from its name)"]
        self.assertEqual(inputs[("[1, 2, 3]", "1")], guess)
        self.assertEqual(inputs[("[1, 2, 3]", "-1")], guess)  # -1 is the last element in Ruby
        self.assertIn("index may be out of range for values", inputs[("[]", "1")])

    def test_the_index_range_assumption_names_only_the_indexed_parameter(self):
        # finding: pick(values, index, sep = ",") got 'index may be out of range for sep'
        fn = RB.functions('def pick(values, index, sep = ",")\n  values.fetch(index).to_s + sep\nend\n')[0]
        notes = [a for i in RB.edge_inputs(fn) for a in i.assumptions if "out of range" in a]
        self.assertTrue(notes)
        self.assertTrue(all(note.endswith("for values") for note in notes), notes)
        fn = RB.functions("def char_at(items, text, i)\n  text[i]\nend\n")[0]  # indexes text, not items
        notes = {a for inp in RB.edge_inputs(fn) for a in inp.assumptions if "out of range" in a}
        self.assertEqual(notes, {"i may be out of range for text"})

    def test_model_inputs_carry_the_untyped_assumption(self):
        fn = RB.functions("def f(name = nil, n)\n  n\nend\n")[0]
        self.assertEqual(RB.model_input(fn, ["nil", "nil"], {}).assumptions, ["n may be nil"])
        self.assertEqual(RB.model_input(fn, ["nil", "3"], {}).assumptions,
                         ["the parameters have no types, so this input is assumed to be allowed"])
        self.assertIsNone(RB.model_input(fn, ['"#{exit}"', "1"], {}))
        typed = RB.functions('# @param values [Array<Integer>]\ndef g(values, sep = ",")\n  values\nend\n')[0]
        self.assertEqual(RB.model_input(typed, ["[1, -2]", '","'], {}).assumptions, [])
        self.assertEqual(RB.model_input(typed, ["[1, -2]", '""'], {}).assumptions, ['sep may be ""'])
        self.assertEqual(RB.model_input(typed, ['["a"]', '","'], {}).assumptions,
                         ["the parameters have no types, so this input is assumed to be allowed"])


class DeliberateTest(unittest.TestCase):
    SOURCE = """def check(n, s)
  raise ArgumentError, "negative" if n.negative?
  raise ArgumentError, "bad #{s.foo}" if s.empty?
  fail "zero" if Integer(s).zero?
  raise NotImplementedError if n > 100
  [n].each { |v| raise KeyError.new("k") if v == 7 }
end
"""

    def failure(self, error, line, top):
        return {"kind": "exception", "error": error, "line": line,
                "traceback": f"/tmp/w/main.rb:{top}\nharness.rb:40:in '<main>'"}

    def test_a_raise_that_raised_that_exception_is_a_rejection(self):
        self.assertTrue(RB.deliberate(self.SOURCE, self.failure("ArgumentError", 2, "2:in 'Object#check'")))
        self.assertTrue(RB.deliberate(self.SOURCE, self.failure("RuntimeError", 4, "4:in 'Object#check'")))
        self.assertTrue(RB.deliberate(self.SOURCE, self.failure("KeyError", 6, "6:in 'block in Object#check'")))

    def test_other_failures_on_a_raise_line_are_bugs(self):
        # NoMethodError while building the message; ArgumentError from Kernel#Integer (a core frame first)
        self.assertFalse(RB.deliberate(self.SOURCE, self.failure("NoMethodError", 3, "3:in 'Object#check'")))
        integer = {"kind": "exception", "error": "ArgumentError", "line": 4,
                   "traceback": "<internal:kernel>:288:in 'Kernel#Integer'\n/tmp/w/main.rb:4:in 'Object#check'"}
        self.assertFalse(RB.deliberate(self.SOURCE, integer))
        self.assertFalse(RB.deliberate(self.SOURCE, self.failure("TypeError", 4, "4:in 'Integer#zero?'")))
        self.assertFalse(RB.deliberate(self.SOURCE, self.failure("NotImplementedError", 5, "5:in 'Object#check'")))
        self.assertFalse(RB.deliberate("def f(x)\n  puts \"fail: #{x.foo}\"\nend\n",
                                       self.failure("NoMethodError", 2, "2:in 'Object#f'")))


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if RB.sandbox() != "docker":
            self.skipTest("no Ruby sandbox: python phantom.py setup ruby")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def test_samples(self):
        answer = specter.run((SAMPLES / "crashes.rb").read_text(), "ruby", use_model=False)
        found = bugs(answer)
        expected = {"average": ("ZeroDivisionError", 10), "median": ("IndexError", 16),
                    "month_number": ("KeyError", 25), "badge": ("TypeError", 30), "percent": ("FloatDomainError", 36),
                    "chunks": ("ArgumentError", 42), "collatz_steps": ("Timeout", 46),
                    "digit_sum": ("SystemStackError", 57)}
        for name, (error, line) in expected.items():  # YARD-typed: errors
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[name])
        self.assertIn(("NoMethodError", 20, "warning"), found["first_word"])  # untyped: text's kind is a guess
        warning = next(f for f in answer["findings"] if f["function"] == "first_word" and f["severity"] == "warning")
        self.assertEqual(warning["assumptions"], ["text is a string (guessed from its name)"])
        self.assertNotIn("leap_year?", found)  # a wrong answer: needs an intent check
        loop = next(f for f in answer["findings"] if f["function"] == "collatz_steps" and f["severity"] == "error")
        self.assertEqual(loop["failure"]["kind"], "timeout")
        correct = specter.run((SAMPLES / "correct.rb").read_text(), "ruby", use_model=False)
        self.assertEqual([f["status"] for f in correct["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "crashes.rb").read_text()
        result = specter.replay(source, "ruby", "average", {"exprs": ["[]"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "ZeroDivisionError", 10))
        self.assertEqual(specter.replay(source, "ruby", "average", {"exprs": ["[2, 4]"]}), {"ok": True, "result": "3"})

    def test_module_functions_run_and_missing_files_are_build_errors(self):
        source = ("module Stats\n  # @param values [Array<Integer>]\n  def self.mean(values)\n    values.sum / values.size\n"
                  "  end\nend\n")
        found = bugs(specter.run(source, "ruby", use_model=False))
        self.assertIn(("ZeroDivisionError", 4, "error"), found["Stats.mean"])
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


class RegressionTest(unittest.TestCase):
    """The adversarial review's findings, end to end in the sandbox: each test is named after one."""

    def setUp(self):
        if RB.sandbox() != "docker":
            self.skipTest("no Ruby sandbox: python phantom.py setup ruby")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def statuses(self, source):
        return {f["function"]: (f["status"], f.get("severity")) for f in specter.run(source, "ruby", use_model=False)["findings"]}

    def test_a_kind_guessed_from_a_name_or_default_is_not_an_error(self):
        source = ('def order_total(items)\n  return 0 if items.nil?\n\n  items.sum { |item| item[:price] * item[:qty] }\nend\n\n'
                  'def normalize_tags(tags = [])\n  return [] if tags.nil?\n\n  tags.map { |tag| tag.strip.downcase }.uniq\nend\n\n'
                  'def to_utf8(text, from = "ISO-8859-1")\n  text.to_s.encode("UTF-8", from.to_s)\nend\n\n'
                  'def top_words(counts, limit = 3)\n  return [] if counts.nil? || limit.nil? || limit <= 0\n\n'
                  '  counts.sort_by { |_word, n| -n }.first(limit).map(&:first)\nend\n')
        answer = specter.run(source, "ruby", use_model=False)
        self.assertEqual([f for f in answer["findings"] if f.get("severity") == "error"], [])
        self.assertEqual(len(answer["findings"]), 4)
        self.assertNotIn("error", {f["status"] for f in answer["findings"]})

    def test_top_level_globals_ivars_include_using_and_alias_are_kept(self):
        source = ('$memo = {}\n\ndef fib(n)\n  return 0 if n.nil?\n  return n if n < 2\n\n  $memo[n] ||= fib(n - 1) + fib(n - 2)\nend\n\n'
                  'include Math\n\ndef distance(x, y)\n  return 0.0 if x.nil? || y.nil?\n\n  sqrt(x * x + y * y)\nend\n\n'
                  '@rates = { "EUR" => 0.9 }\n\ndef convert(amount, code = "EUR")\n  return 0 if amount.nil?\n\n'
                  '  amount * @rates.fetch(code.to_s, 1)\nend\n\n'
                  'def mean(values)\n  return 0 if values.nil? || values.empty?\n\n  values.sum.fdiv(values.size)\nend\n'
                  'alias average_of mean\n\ndef report(values)\n  "avg=#{average_of(values)}"\nend\n\n'
                  'module Blank\n  refine String do\n    def blank? = strip.empty?\n  end\nend\nusing Blank\n\n'
                  'def present?(text)\n  !text.to_s.blank?\nend\n')
        self.assertEqual(set(self.statuses(source).values()), {("no_bug_found", None)})

    def test_a_heredoc_constant_keeps_its_body(self):
        source = ('USAGE = <<~TEXT # shown by --help\n  usage: greet NAME\nTEXT\n\nputs <<~DEMO\n  demo\nDEMO\n\n'
                  'def usage_line(name)\n  "#{USAGE.lines.first.strip} (#{name})"\nend\n')
        self.assertEqual(self.statuses(source), {"usage_line": ("no_bug_found", None)})

    def test_the_input_number_does_not_leak_into_argv(self):
        source = 'SLICE = Integer(ARGV.first || "2")\n\ndef pairs(values)\n  return [] if values.nil?\n\n  values.each_slice(SLICE).to_a\nend\n'
        self.assertEqual(self.statuses(source), {"pairs": ("no_bug_found", None)})
        self.assertEqual(specter.replay(source, "ruby", "pairs", {"exprs": ["[1, 2, 3]"]}),
                         {"ok": True, "result": "[[1, 2], [3]]"})

    def test_gem_version_works_without_a_require(self):
        source = ('def latest_version(names)\n  return nil if names.nil? || names.empty?\n\n'
                  '  names.max_by { |v| Gem::Version.correct?(v) ? Gem::Version.new(v) : Gem::Version.new("0") }\nend\n')
        self.assertEqual(self.statuses(source), {"latest_version": ("no_bug_found", None)})

    def test_data_after_end_is_readable(self):
        source = ('def fixture_count(name)\n  DATA.each_line.count { |line| line.start_with?(name.to_s) }\nend\n\n'
                  '__END__\nabc 1\nabd 2\n')
        self.assertTrue(RB.context(source, None).endswith("__END__\nabc 1\nabd 2\n"))
        self.assertEqual(self.statuses(source), {"fixture_count": ("no_bug_found", None)})
        self.assertEqual(specter.replay(source, "ruby", "fixture_count", {"exprs": ['"ab"']}), {"ok": True, "result": "2"})

    def test_exit_and_abort_in_a_function_are_not_bugs(self):
        source = ('HELP = { "add" => "add NAME" }.freeze\n\ndef show_help(name)\n  puts HELP.fetch(name.to_s, "no help")\n  exit\nend\n\n'
                  '# @param n [Integer]\ndef give_up(n)\n  abort("negative") if n.negative?\n  exit!(1) if n.zero?\n  n\nend\n')
        self.assertEqual(self.statuses(source), {"show_help": ("no_bug_found", None), "give_up": ("bug", "info")})
        result = specter.replay(source, "ruby", "give_up", {"exprs": ["0"]})
        self.assertEqual((result["ok"], result["kind"]), (False, "exit"))

    def test_a_hash_default_does_not_turn_another_parameters_bug_into_a_warning(self):
        source = ('def initial(text, options = {})\n  text[0].upcase + options.fetch(:suffix, ".")\nend\n\n'
                  '# @param text [String]\ndef typed_initial(text, options = {})\n  text[0].upcase + options.fetch(:suffix, ".")\nend\n')
        found = {f["function"]: f for f in specter.run(source, "ruby", use_model=False)["findings"]
                 if f["status"] == "bug" and f["severity"] != "info"}
        self.assertEqual((found["initial"]["input"]["call"], found["initial"]["assumptions"]),
                         ('initial("", {})', ["text is a string (guessed from its name)"]))
        self.assertEqual((found["typed_initial"]["input"]["call"], found["typed_initial"]["severity"]),
                         ('typed_initial("", {})', "error"))

    def test_a_basic_object_result_is_shown_not_reported(self):
        source = ('class Blank < BasicObject\n  def initialize(value)\n    @value = value\n  end\nend\n\n'
                  'def blank_box(text)\n  Blank.new(text)\nend\n')
        self.assertEqual(self.statuses(source)["blank_box"], ("no_bug_found", None))
        self.assertEqual(specter.replay(source, "ruby", "blank_box", {"exprs": ['"a"']}), {"ok": True, "result": "#<Blank>"})

    def test_deliberate_raises_and_load_failures_are_not_findings(self):
        source = ('# @param n [Integer]\ndef checked(n)\n  raise ArgumentError, "negative" if n.negative?\n  fail "zero" if n.zero?\n\n'
                  '  10 / n\nend\n\n# @param s [String]\ndef parsed(s)\n  raise ArgumentError, "negative" if Integer(s) < 0\n\n  s\nend\n')
        found = bugs(specter.run(source, "ruby", use_model=False))
        self.assertEqual(list(found["checked"]), [("NoMethodError", 3, "info")])  # only nil: the raises are on purpose
        self.assertIn(("ArgumentError", 11, "error"), found["parsed"])  # raised by Integer(), not by the raise
        broken = specter.run('LIMIT = Integer(ENV.fetch("PHANTOM_LIMIT"))\n\ndef twice(n)\n  n * LIMIT\nend\n',
                             "ruby", use_model=False)["findings"]
        self.assertEqual([(f["status"], f["failure"]["kind"]) for f in broken], [("error", "build_error")])
        self.assertIn("KeyError", broken[0]["failure"]["message"])
        # loads in the build check (no .out yet) but fails while loading for every input: not entered
        late = 'raise "loading failed" if File.exist?(File.join(__dir__, ".out"))\n\ndef twice(n)\n  n * 2\nend\n'
        results = RB.run_inputs(late, RB.functions(late)[0], [Input(["1"]), Input(["1..\"a\""])], "docker")
        self.assertEqual([r["kind"] for r in results], ["not_entered", "not_entered"])
        self.assertEqual([(f["status"], f["failure"]["kind"]) for f in specter.run(late, "ruby", use_model=False)["findings"]],
                         [("error", "not_entered")])
        source = "def f(x)\n  x.foo\nend\n"
        results = RB.run_inputs(source, RB.functions(source)[0], [Input(["1"]), Input(["1..\"a\""])], "docker")
        self.assertEqual([r["kind"] for r in results], ["exception", "input_error"])


if __name__ == "__main__":
    unittest.main()
