"""Go plugin: parsing, literal checking, inputs, and Specter end to end in the phantom-go sandbox."""
import base64
import re
import unittest
from pathlib import Path
from unittest import mock

from runner import languages, llm, specter
from runner.languages.base import Input
from runner.languages.go import runtime_name as go_runtime_name

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "go"
GO = languages.get("go")


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


def model_answer(*inputs, line=1):
    return {"risk": "low", "risk_reason": "", "hypotheses": [
        {"kind": "crash", "line": line, "bug": "crashes", "trigger_condition": "", "confidence": 0.9,
         "inputs": [list(args) for args in inputs]}]}


class ParsingTest(unittest.TestCase):
    SOURCE = """package shop

import "fmt"

type Point struct{ X, Y int }
type IDs []int

func Total(prices []int, a, b int) (int, error) {
	return 0, nil
}

func (s *Stack) Push(x int) {}

func Map[T any](x T) T { return x }

func Sum(xs ...int) int { return 0 }

func Apply(f func(int) int) int { return f(1) }

func Move(p *Point, ids IDs) {}

func main() { fmt.Println(1) }
"""

    def test_functions_parameters_and_skips(self):
        found = {f.name: f for f in GO.functions(self.SOURCE)}
        self.assertEqual(list(found), ["Total", "Stack.Push", "Map", "Sum", "Apply", "Move", "main"])
        total = found["Total"]
        self.assertEqual((total.line, total.end_line, total.returns), (8, 10, "(int, error)"))
        self.assertEqual([(p.name, p.type) for p in total.params], [("prices", "[]int"), ("a", "int"), ("b", "int")])
        self.assertIsNone(total.skip)
        self.assertIsNone(found["Move"].skip)  # *Point and IDs are declared in the file
        self.assertIn("method", found["Stack.Push"].skip)
        self.assertIn("generic", found["Map"].skip)
        self.assertIn("variadic", found["Sum"].skip)
        self.assertIn("func(int) int", found["Apply"].skip)
        self.assertIsNotNone(found["main"].skip)

    def test_user_file_keeps_its_lines(self):
        source = "//go:build ignore\n\npackage shop\n\nfunc main() {\n}\n\nfunc mainly() {}\n"
        program = GO.program(source, GO.functions(source)[1], [Input([])])
        user = program["files"]["main.go"]
        self.assertEqual(user.count("\n"), source.count("\n"))
        self.assertEqual(user.splitlines()[2], "package main")
        self.assertEqual(user.splitlines()[4], "func phantomUserMain() {")
        self.assertNotIn("go:build", user)
        self.assertIn("func mainly() {}", user)
        self.assertIn("mainly()", program["files"]["phantom_harness.go"])


class LiteralTest(unittest.TestCase):
    def test_plain_literals_pass(self):
        for expr in ("1", "-1", "+2", "0x1F", "1_000", "-1.5e3", "1i", "'a'", "'\\n'", '"a\\tb"', "`raw`", "true",
                     "nil", "[]int{1, 2}", "[]int{}", 'map[string]int{"a": 1}', "[2]int{1, 2}", "[...]int{1}",
                     '[][]string{{"a"}, {}}', 'map[string][]int{"a": {1}}', "Point{1, 2}", "&Point{1, 2}",
                     "new(int)", '[]byte("abc")', "math.MaxInt64", "[]int{math.MinInt64}", "[]any{1, nil}",
                     "[]interface{}{1}", "(1)", "map[[2]int]bool{{1, 2}: true}", "[]*int{nil}"):
            self.assertTrue(GO.literal_ok(expr), expr)

    def test_anything_that_runs_code_is_rejected(self):
        for expr in ("x", "f()", "1 + 1", "-x", "!true", "^1", "<-ch", "&x", "*p", "x.(int)", "int8(-1)",
                     "string(65)", 'len("a")', "func() int { return 1 }()", "[]int{1}[0]", '"abc"[0]',
                     "Point{X: 1}", "map[string]int{k: 1}", "[N]int{}", "struct{}{}", "[]func(){}", "[]chan int{}",
                     "make([]int, 3)", "new(x.Y)", "os.Args", "time.Second", "math.Sqrt(2)", "-math.MaxInt64",
                     "interface{ M() }(nil)", "[]T[int]{}", "1 /* c */", "1 // c", "1, 2", "iota", "--1",
                     '1); os.Exit(3); f(1', "1)) }; func init() { panic(1) }; var _ = ((1",
                     "[]int{1}; func init() { os.Exit(3) }", '"a" + "b"', "[]int{1,\n2}"):
            self.assertFalse(GO.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_values_follow_the_declared_types(self):
        ints = dict(GO.edge_values("int", "n"))
        self.assertEqual(GO.edge_values("int", "n")[0], ("3", None))
        self.assertIsNone(ints["-1"])
        self.assertEqual(ints["math.MaxInt"], "n may be as large as math.MaxInt")
        self.assertIsNone(dict(GO.edge_values("int8", "n"))["math.MaxInt8"])  # 127 is an everyday int8
        values = dict(GO.edge_values("[]string", "names"))
        self.assertIn("[]string{}", values)
        self.assertIsNone(values["[]string{}"])
        self.assertNotIn("nil", values)  # a nil slice is just an empty one
        self.assertEqual(dict(GO.edge_values("map[string]int", "m"))["nil"], "m may be nil")
        self.assertEqual(GO.edge_values("*int", "p"), [("new(int)", None), ("nil", "p may be nil")])
        self.assertEqual(GO.edge_values("[][]int", "grid")[0][0], "[][]int{{1, 2, 3}, {1, 2, 3}, {1, 2, 3}}")
        for kind in ("chan int", "func()", "error", "time.Duration", "...int", "Unknown"):
            self.assertEqual(GO.edge_values(kind, "x"), [], kind)
        self.assertTrue(GO.nullable("[]int") and GO.nullable("any"))
        self.assertFalse(GO.nullable("map[string]int") or GO.nullable("*int") or GO.nullable("int"))

    def test_types_declared_in_the_file(self):
        source = ("package p\ntype Point struct {\n\tX int\n}\ntype IDs []int\ntype Celsius float64\n"
                  "func f(p Point, q *Point, ids IDs, c Celsius) {}\n")
        options = [GO.edge_values(p.type, p.name, fn.types) for fn in GO.functions(source) for p in fn.params]
        self.assertEqual(options[0], [("Point{}", "p may be the zero value Point{}")])
        self.assertEqual(options[1][0], ("&Point{}", "q may be the zero value Point{}"))
        self.assertEqual(options[2][0], ("IDs{1, 2, 3}", None))
        self.assertEqual(options[3][0], ("1.5", None))
        for values in options:  # a finding's input must pass the literal check again to be replayed
            self.assertTrue(all(GO.literal_ok(value) for value, _ in values), values)

    def test_edge_inputs_vary_one_parameter_at_a_time(self):
        inputs = [tuple(i.args) for i in GO.edge_inputs(GO.functions("package p\nfunc f(a int, b string) {}")[0])]
        self.assertEqual(inputs[0], ("3", '"abc"'))
        self.assertIn(("0", '"abc"'), inputs)
        self.assertIn(("3", '""'), inputs)
        self.assertNotIn(("0", '""'), inputs)

    def test_an_index_outside_its_slice_needs_an_assumption(self):
        fn = GO.functions("package p\nfunc get(values []int, i int) int { return values[i] }")[0]
        inputs = {tuple(i.args): i.assumptions for i in GO.edge_inputs(fn)}
        self.assertEqual(inputs[("[]int{1, 2, 3}", "1")], [])
        self.assertEqual(inputs[("[]int{1, 2, 3}", "2")], [])
        self.assertEqual(inputs[("[]int{1, 2, 3}", "3")], ["i may be outside the bounds of values"])
        self.assertEqual(inputs[("[]int{}", "1")], ["i may be outside the bounds of values"])
        made = GO.model_input(fn, ["[]int{7}", "5"], {})
        self.assertEqual(made.assumptions, ["i may be outside the bounds of values"])
        fn = GO.functions("package p\nfunc cut(s string, start, end int) string { return s[start:end] }")[0]
        inputs = {tuple(i.args): i.assumptions for i in GO.edge_inputs(fn)}
        self.assertEqual(inputs[('"abc"', "0", "3")], [])
        self.assertEqual(inputs[('"é"', "0", "3")], ["end may be outside the bounds of s"])  # 2 bytes
        self.assertIn("start may be greater than end", inputs[('"abc"', "0", "-1")])

    def test_model_nil_needs_an_assumption_where_go_panics_on_it(self):
        fn = GO.functions("package p\nfunc f(m map[string]int, xs []int) {}")[0]
        self.assertEqual(GO.model_input(fn, ["nil", "nil"], {}).assumptions, ["m may be nil"])
        self.assertIsNone(GO.model_input(fn, ["nil", "os.Args"], {}))


def run_line(i, code, out="", err="", entered=1):
    """One input's part of the sandbox script's output."""
    encode = lambda text: base64.b64encode(text.encode()).decode()
    return f"@@RUN {i} {code} 0 {entered}\n{encode(out)}\n{encode(err)}\n"


class ConventionTest(unittest.TestCase):
    """The shared conventions, without Docker: entry marker, deliberate panics, exits."""

    def test_the_harness_marks_entry_after_building_typed_arguments(self):
        self.assertTrue(GO.marks_entry)
        fn = GO.functions("package p\nfunc f(n, m int8) int8 { return n }\n")[0]
        harness = GO.program("", fn, [Input(["-1", "math.MaxInt8"])])["files"]["phantom_harness.go"]
        self.assertIn('var phantomA0 int8 = -1; var phantomA1 int8 = 127; phantomEnter(); '
                      'return phantomShow(f(phantomA0, phantomA1))', harness)
        self.assertIn('phantomOS.Create(".entered")', harness)

    def test_an_explicit_panic_is_deliberate_but_a_runtime_error_on_its_line_is_not(self):
        source = ('package p\n\nfunc f(width int, xs []int) string {\n\tif width < 0 {\n'
                  '\t\tpanic(fmt.Sprintf("bad width %d, first %d", width, xs[0]))\n\t}\n'
                  '\tlog.Panicf("no")\n\treturn strings.Repeat(" ", width)\n}\n')
        deliberate = lambda kind, error, line: GO.deliberate(source, {"kind": kind, "error": error, "line": line})
        self.assertTrue(deliberate("exception", "panic(string)", 5))
        self.assertTrue(deliberate("crash", "panic", 5))  # a panic in another goroutine
        self.assertTrue(deliberate("exception", "panic(string)", 7))  # log.Panicf
        self.assertFalse(deliberate("exception", "index out of range", 5))  # xs[0] inside the message
        self.assertFalse(deliberate("exception", "panic(string)", 8))  # strings.Repeat: library code
        self.assertFalse(deliberate("timeout", "Timeout", 5))
        for line in ("x.panic(1)", "dontpanic(1)", "// panics on nil"):
            self.assertIsNone(re.search(GO.rejection_pattern, line), line)

    def test_ending_the_program_on_purpose_is_kind_exit(self):
        self.assertEqual(GO.classify(2, "", "usage: tool FILE\n")["kind"], "exit")
        self.assertEqual(GO.classify(1, "", "2026/10/19 04:47:41 DATABASE_URL is not set\n")["kind"], "exit")
        self.assertEqual(GO.classify(0, "", "")["kind"], "exit")  # os.Exit(0): the harness reports before its own
        panic = "panic: boom\n\ngoroutine 6 [running]:\nmain.f.func1()\n\t/tmp/w/main.go:5 +0x1d\n"
        self.assertEqual(GO.classify(2, "", panic)["kind"], "crash")
        self.assertEqual(GO.classify(2, "", panic.split("\n\n", 1)[1])["kind"], "crash")  # panic line cut off

    def test_an_exit_before_the_function_is_entered_is_not_entered(self):
        stdout = "@@BUILD 0\n" + run_line(0, 1, err="DATABASE_URL is not set\n", entered=0) \
            + run_line(1, 2, err="usage: tool FILE\n", entered=1)
        results = GO.parse_output(stdout, 2)
        self.assertEqual([r["kind"] for r in results], ["not_entered", "exit"])
        self.assertIn("DATABASE_URL", results[0]["message"])


class ReviewFindingsTest(unittest.TestCase):
    """Regression tests for the adversarial review (findings_go.json) that need no sandbox."""

    def test_model_literals_that_fail_inside_the_harness_are_rejected(self):
        # finding: map[any] keys that are slices, slice-to-array conversions and huge arrays passed literal_ok
        for expr in ("map[any]int{[]int{}: 1}", 'map[any]int{[]byte("a"): 1}', "map[any]int{[]int(nil): 1}",
                     "map[any]int{[1]any{[]int{}}: 1}", "map[[1]any]int{{[]int{}}: 1}",
                     "[3]int([]int{1})", "[]F(1)", "new([1099511627776]byte)", "[]any{[1099511627776]byte{}}",
                     "[1 << 40]byte{}", "[4096][4096]byte{}"):
            self.assertFalse(GO.literal_ok(expr), expr)
        for expr in ('[]byte("abc")', '[]rune("é")', "[]int(nil)", "map[string]int(nil)", "[256]byte{}",
                     "map[[2]int]bool{{1, 2}: true}", "map[any]int{1: 1, \"a\": 2}", "[]int{0: 1, 2: 3}"):
            self.assertTrue(GO.literal_ok(expr), expr)

    def test_unsigned_index_parameter_gets_no_negative_edge_value(self):
        fn = GO.functions("package p\n\nfunc At(values []int, i uint) int {\n\treturn values[i]\n}\n")[0]
        inputs = {tuple(i.args): i.assumptions for i in GO.edge_inputs(fn)}
        self.assertFalse([args for args in inputs if args[1].startswith("-")])
        self.assertEqual(inputs[("[]int{1, 2, 3}", "3")], ["i may be outside the bounds of values"])
        self.assertNotIn("-1", dict(GO.edge_values("uint32", "n")))

    def test_pointer_to_pointer_edge_values_are_valid_go(self):
        source = "package p\ntype Node struct {\n\tVal  int\n\tNext *Node\n}\nfunc f(head **Node, all []**Node) {}\n"
        fn = GO.functions(source)[0]
        head, nodes = (GO.edge_values(p.type, p.name, fn.types) for p in fn.params)
        self.assertEqual(head, [("new(*Node)", "*head may be nil"), ("nil", "head may be nil")])
        self.assertEqual(nodes[0][0], "[]**Node{new(*Node), new(*Node), new(*Node)}")
        self.assertEqual(GO.edge_values("*int", "p"), [("new(int)", None), ("nil", "p may be nil")])
        for value, _ in head + nodes:
            self.assertTrue(GO.literal_ok(value), value)

    def test_value_bounds_named_like_indexes_are_not_indexes_into_the_slice(self):
        source = ("package p\n\nfunc Between(readings []int, low, high int) []int {\n\tout := make([]int, 0, high-low)\n"
                  "\tfor _, r := range readings {\n\t\tif r >= low && r <= high {\n\t\t\tout = append(out, r)\n"
                  "\t\t}\n\t}\n\treturn out\n}\n\nfunc Sum(v []int, start, end int) int {\n\tt := 0\n"
                  "\tfor k := start; k < end; k++ {\n\t\tt += v[k]\n\t}\n\treturn t\n}\n")
        between, total = GO.functions(source)
        inputs = {tuple(i.args): i.assumptions for i in GO.edge_inputs(between)}
        self.assertFalse([a for notes in inputs.values() for a in notes if "bounds" in a])
        self.assertEqual(inputs[("[]int{1, 2, 3}", "3", "0")], ["low may be greater than high"])
        self.assertIn(("[]int{1, 2, 3}", "math.MinInt", "3"), inputs)  # ordinary int values
        inputs = {tuple(i.args): i.assumptions for i in GO.edge_inputs(total)}  # a loop counter indexes v
        self.assertEqual(inputs[("[]int{1, 2, 3}", "-1", "3")], ["start may be outside the bounds of v"])
        self.assertEqual(go_runtime_name("runtime error: makeslice: cap out of range"), "makeslice: cap out of range")

    def test_a_call_to_the_users_main_reaches_the_renamed_main(self):
        source = "package p\n\nfunc Restart() {\n\tmain()\n}\n\nfunc main() {}\n"
        user = GO.program(source, GO.functions(source)[0], [Input([])])["files"]["main.go"]
        self.assertEqual(user.splitlines()[3], "\tphantomUserMain()")
        self.assertEqual(user.splitlines()[6], "func phantomUserMain() {}")
        self.assertEqual(user.count("\n"), source.count("\n"))

    def test_literals_that_can_never_compile_are_rejected(self):
        for expr in ('"\\q"', '"\\400"', '"\\ud800"', "1e1000", "08", "18446744073709551616", "[]int{-1: 1}",
                     "[3]int{5: 1}", 'map[string]int{"a": 1, "a": 2}', "phantomCalls{}", "new(phantomCalls)", "[]F(1)"):
            self.assertFalse(GO.literal_ok(expr), expr)
        for expr in ('"\\x41\\u00e9\\377"', "0x1p-2", "1i", "0o17", "math.MaxUint64", "18446744073709551615"):
            self.assertTrue(GO.literal_ok(expr), expr)


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if GO.sandbox() != "docker":
            self.skipTest("no Go sandbox: docker build -f sandbox/go.Dockerfile -t phantom-go .")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)
        for name in ("cache", "model_cache"):  # each test starts without earlier results
            patcher = mock.patch.object(specter, name, specter.Cache())
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_buggy_sample(self):
        found = bugs(specter.run((SAMPLES / "crashes.go").read_text(), "go", use_model=False))
        expected = {"Average": ("integer divide by zero", 20, "error"),
                    "FirstWord": ("index out of range", 24, "error"),
                    "LastPrice": ("index out of range", 29, "error"),
                    "ParsePort": ("index out of range", 33, "error"),
                    "DisplayName": ("slice bounds out of range", 39, "error"),
                    "PadLeft": ("panic(string)", 43, "error"),
                    "Deposit": ("nil pointer dereference", 47, "info"),  # only with account == nil
                    "CollatzSteps": ("Timeout", 53, "error"),
                    "Factorial": ("stack overflow", 68, "error")}
        for name, finding in expected.items():
            with self.subTest(function=name):
                self.assertIn(finding, found[name])
        self.assertNotIn("IsLeapYear", found)  # a wrong answer: needs an intent check
        self.assertEqual(found["CollatzSteps"], [("Timeout", 53, "error")])  # one hang, one finding

    def test_correct_sample(self):
        correct = specter.run((SAMPLES / "correct.go").read_text(), "go", use_model=False)
        self.assertEqual([f["status"] for f in correct["findings"]], ["no_bug_found"] * 4)

    def test_replay_and_error_results(self):
        source = (SAMPLES / "crashes.go").read_text()
        result = specter.replay(source, "go", "Average", {"exprs": ["[]int{}"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "integer divide by zero", 20))
        self.assertIn("main.go:20", result["traceback"])
        self.assertEqual(specter.replay(source, "go", "Average", {"exprs": ["[]int{2, 4}"]}),
                         {"ok": True, "result": "3"})
        lookup = specter.replay((SAMPLES / "correct.go").read_text(), "go", "Lookup", {"exprs": ["nil", '"tea"']})
        self.assertEqual(lookup, {"ok": True, "result": '(0, error("no price for \\"tea\\""))'})

    def test_model_inputs_are_run_and_injections_dropped(self):
        source = ('package ages\n\nimport "strconv"\n\nvar groups = []int{0, 1, 2}\n\nfunc ParseAge(text string) int {\n'
                  '\tn, err := strconv.Atoi(text)\n\tif err != nil {\n\t\treturn -1\n\t}\n'
                  '\treturn groups[n/50]\n}\n')
        answer = model_answer(['"200"'], ["os.Exit(1)"], ['func() string { return groups[9] }()'],
                              ['"1"); _ = groups[9]; ParseAge("1'], line=12)
        provider = llm.ScriptedProvider([{"match": "ParseAge", "answer": answer}])
        llm.set_provider(provider)
        found = [f for f in specter.run(source, "go")["findings"] if f["status"] == "bug"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["error"], f["failure"]["line"])
                          for f in found], [("model", 'ParseAge("200")', "index out of range", 12)])
        self.assertIn("<code-", provider.calls[0]["prompt"])  # the code is fenced as data

    def test_a_model_input_that_does_not_compile_keeps_the_edge_findings(self):
        source = "package stats\n\nfunc Mean(values []int) int {\n\ttotal := 0\n\tfor _, v := range values {\n" \
                 "\t\ttotal += v\n\t}\n\treturn total / len(values)\n}\n"
        llm.set_provider(llm.ScriptedProvider([{"match": "Mean", "answer": model_answer(['[]string{"a"}'], ["7"])}]))
        found = bugs(specter.run(source, "go"))
        self.assertEqual(found, {"Mean": [("integer divide by zero", 8, "error")]})

    def test_fatal_errors_and_hangs(self):
        source = ("package jobs\n\nfunc Send(n int) int {\n\tch := make(chan int)\n\tif n == 0 {\n\t\tch <- 1\n\t}\n"
                  "\treturn n\n}\n\nfunc First(values []int) int {\n\tdone := make(chan int)\n"
                  "\tgo func() { done <- values[0] }()\n\treturn <-done\n}\n\nfunc Spin(n int) int {\n"
                  "\tfor n > 0 {\n\t\tn += 2\n\t\tn -= 1\n\t}\n\treturn n\n}\n")
        result = specter.run(source, "go", use_model=False)
        found = {f["function"]: f for f in result["findings"] if f["status"] == "bug"}
        self.assertEqual((found["Send"]["failure"]["error"], found["Send"]["failure"]["line"]), ("deadlock", 6))
        self.assertEqual(found["First"]["failure"]["kind"], "crash")  # a panic in another goroutine
        self.assertEqual((found["First"]["failure"]["error"], found["First"]["failure"]["line"]),
                         ("index out of range", 13))
        self.assertEqual((found["Spin"]["failure"]["kind"], found["Spin"]["failure"]["line"]), ("timeout", 18))

    def test_init_and_var_initializer_failures_are_not_bugs(self):
        # finding: init() and package var initializers ran per input and their failure was a bug in every function
        on_init = ('package p\n\nimport (\n\t"log"\n\t"os"\n)\n\nfunc init() {\n\tif os.Getenv("DATABASE_URL") == "" {\n'
                   '\t\tlog.Fatal("DATABASE_URL is not set")\n\t}\n}\n\nfunc Greeting(name string) string {\n'
                   '\treturn "hello " + name\n}\n')
        on_var = ('package p\n\nimport "os"\n\nvar config = mustRead("/etc/app/config.json")\n\n'
                  'func mustRead(path string) []byte {\n\tdata, err := os.ReadFile(path)\n\tif err != nil {\n'
                  '\t\tpanic(err)\n\t}\n\treturn data\n}\n\nfunc Greeting(name string) string {\n\treturn "hello " + name\n}\n')
        for source, error in ((on_init, "os.Exit(1)"), (on_var, "panic")):
            findings = [f for f in specter.run(source, "go", use_model=False)["findings"] if f["function"] == "Greeting"]
            self.assertEqual([(f["status"], f["failure"]["kind"], f["failure"]["error"]) for f in findings],
                             [("error", "not_entered", error)])

    def test_model_literals_that_fail_inside_the_harness_are_not_bugs(self):
        # finding: three accepted literals failed while the harness built them -> red bugs on correct functions.
        # literal_ok now rejects them; even run directly they fail before .entered exists: not_entered.
        source = 'package p\n\nimport "fmt"\n\nfunc Kind(v any) string {\n\treturn fmt.Sprintf("%T", v)\n}\n'
        fn = GO.functions(source)[0]
        results = GO.run_inputs(source, fn, [Input(["map[any]int{[]int{}: 1}"]), Input(["new([1099511627776]byte)"]),
                                             Input(["1"])], "docker")
        self.assertEqual([r.get("kind") for r in results[:2]], ["not_entered", "not_entered"])
        self.assertEqual(results[2], {"ok": True, "result": '"int"'})
        llm.set_provider(llm.ScriptedProvider([{"match": "Function: Kind", "answer": model_answer(
            ["map[any]int{[]int{}: 1}"], ["new([1099511627776]byte)"], ["[]any{[3]int([]int{1})}"])}]))
        self.assertEqual(bugs(specter.run(source, "go")), {})


class ReviewFindingsSandboxTest(unittest.TestCase):
    """Regression tests for the adversarial review that run Go: one file, run once."""
    SOURCE = """package tools

import (
	"fmt"
	"os"
)

type Node struct {
	Val  int
	Next *Node
}

func At(values []int, i uint) int {
	return values[i]
}

func Push(head **Node, val int) {
	*head = &Node{Val: val, Next: *head}
}

func Between(readings []int, low, high int) []int {
	out := make([]int, 0, high-low)
	for _, r := range readings {
		if r >= low && r <= high {
			out = append(out, r)
		}
	}
	return out
}

func Squares() []int {
	out := make([]int, 6000000)
	for i := range out {
		out[i] = i * i
	}
	return out
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: tool FILE")
	os.Exit(2)
}

func Describe(values []int) string {
	if len(values) < 3 {
		panic(fmt.Sprintf("need 3 values, got %d starting with %d", len(values), values[0]))
	}
	return "ok"
}

func Restart() {
	main()
}

func main() {}
"""

    @classmethod
    def setUpClass(cls):
        if GO.sandbox() != "docker":
            raise unittest.SkipTest("no Go sandbox: docker build -f sandbox/go.Dockerfile -t phantom-go .")
        llm.set_provider(None)
        with mock.patch.object(specter, "cache", specter.Cache()), mock.patch.object(specter, "model_cache", specter.Cache()):
            answer = specter.run(cls.SOURCE, "go", use_model=False)
        cls.found = {}
        for f in answer["findings"]:
            cls.found.setdefault(f["function"], []).append(f)

    def line(self, text):
        return next(n for n, line in enumerate(self.SOURCE.splitlines(), 1) if text in line)

    def summary(self, name):
        return [(f["status"], f.get("severity"), f.get("assumptions"), (f.get("failure") or {}).get("error"),
                 (f.get("failure") or {}).get("line")) for f in self.found[name]]

    def test_a_large_return_value_is_not_reported_as_a_timeout(self):
        # finding: phantomShow formatted 6M ints with %#v before truncating -> "possible infinite loop"
        self.assertEqual(self.summary("Squares"), [("no_bug_found", None, None, None, None)])

    def test_unsigned_index_parameter_is_checked(self):
        # finding: the edge value -1 for `i uint` didn't compile, so At was left unchecked (status error)
        self.assertEqual(self.summary("At"), [("bug", "warning", ["i may be outside the bounds of values"],
                                               "index out of range", self.line("return values[i]"))])

    def test_pointer_to_pointer_parameter_is_checked(self):
        # finding: the edge value &&Node{} for `head **Node` didn't compile, so Push was left unchecked
        self.assertEqual(self.summary("Push"), [("bug", "info", ["head may be nil"], "nil pointer dereference",
                                                 self.line("*head = &Node"))])

    def test_value_bounds_named_like_indexes_are_not_indexes(self):
        # finding: low/high got "low may be outside the bounds of readings"; the name was "makeslice"
        self.assertEqual(self.summary("Between"), [("bug", "warning", ["low may be greater than high"],
                                                    "makeslice: cap out of range", self.line("make([]int, 0"))])

    def test_helpers_that_exit_on_purpose_are_not_bugs(self):
        # finding: usage() { ...; os.Exit(2) } was a red "os.Exit(2)" crash
        self.assertEqual(self.summary("usage"), [("no_bug_found", None, None, None, None)])

    def test_a_runtime_error_on_a_panic_line_is_still_a_bug(self):
        # convention: panic(...) rejects on purpose (Describe([]int{1}) is not a finding), but values[0] on
        # that line with an empty slice is a real index error
        self.assertEqual(self.summary("Describe"), [("bug", "error", [], "index out of range",
                                                     self.line("panic(fmt.Sprintf"))])

    def test_a_call_to_the_users_main_reaches_the_renamed_main(self):
        # finding: main() inside Restart bound to the harness's main: "initialization cycle", status error
        self.assertEqual(self.summary("Restart"), [("no_bug_found", None, None, None, None)])
        self.assertEqual(self.found["main"][0]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
