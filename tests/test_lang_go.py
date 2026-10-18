"""Go plugin: parsing, literal checking, inputs, and Specter end to end in the phantom-go sandbox."""
import unittest
from pathlib import Path
from unittest import mock

from runner import languages, llm, specter
from runner.languages.base import Input

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
        found = bugs(specter.run((SAMPLES / "buggy.go").read_text(), "go", use_model=False))
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
        source = (SAMPLES / "buggy.go").read_text()
        result = specter.replay(source, "go", "Average", {"exprs": ["[]int{}"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "integer divide by zero", 20))
        self.assertIn("main.go:20", result["traceback"])
        self.assertEqual(specter.replay(source, "go", "Average", {"exprs": ["[]int{2, 4}"]}),
                         {"ok": True, "result": "3"})
        lookup = specter.replay((SAMPLES / "correct.go").read_text(), "go", "Lookup", {"exprs": ["nil", '"tea"']})
        self.assertEqual(lookup, {"ok": True, "result": '(0, error("no price for \\"tea\\""))'})

    def test_model_inputs_are_run_and_injections_dropped(self):
        source = ('package ages\n\nimport "strconv"\n\nfunc ParseAge(text string) int {\n'
                  '\tn, err := strconv.Atoi(text)\n\tif err != nil {\n\t\treturn -1\n\t}\n'
                  '\tif n > 150 {\n\t\tpanic("age out of range")\n\t}\n\treturn n\n}\n')
        answer = model_answer(['"200"'], ["os.Exit(1)"], ['func() string { panic("age out of range") }()'],
                              ['"1"); panic("age out of range"); ParseAge("1'], line=11)
        provider = llm.ScriptedProvider([{"match": "ParseAge", "answer": answer}])
        llm.set_provider(provider)
        found = [f for f in specter.run(source, "go")["findings"] if f["status"] == "bug"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["error"], f["failure"]["line"])
                          for f in found], [("model", 'ParseAge("200")', "panic(string)", 11)])
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


if __name__ == "__main__":
    unittest.main()
