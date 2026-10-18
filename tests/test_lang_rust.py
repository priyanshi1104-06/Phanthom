"""Rust plugin: parsing, literal checking, inputs, and Specter end to end with rustc in Docker."""
import unittest
from pathlib import Path

from runner import languages, llm, specter
from runner.languages.base import Input

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "rust"
RUST = languages.get("rust")


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


class ParsingTest(unittest.TestCase):
    def test_functions_and_parameters(self):
        source = """use std::collections::HashMap;
pub fn add(a: i32, mut b: &'static str) -> i32 { a }
pub struct Stats;
impl Stats {
    pub fn mean(values: &[f64]) -> f64 { 0.0 }
    fn total(&self) -> f64 { 0.0 }
}
impl std::fmt::Display for Stats { fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { Ok(()) } }
fn first<'a>(values: &'a [i32], m: HashMap<String, Vec<u8>>) -> Option<&'a i32> { values.first() }
fn largest<T: Ord>(values: &[T]) -> &T { &values[0] }
async fn fetch(url: &str) {}
unsafe fn raw(p: *const u8) {}
fn report(stats: &Stats) {}
fn apply(f: impl Fn(i32) -> i32) {}
#[test]
fn it_works() {}
fn main() {}
"""
        found = {f.name: f for f in RUST.functions(source)}
        self.assertEqual(list(found), ["add", "Stats::mean", "Stats::total", "first", "largest", "fetch", "raw",
                                       "report", "apply", "it_works", "main"])  # trait impls are left out
        self.assertEqual([(p.name, p.type) for p in found["add"].params], [("a", "i32"), ("b", "&'static str")])
        self.assertEqual((found["add"].line, found["add"].returns), (2, "i32"))
        self.assertEqual((found["Stats::mean"].line, found["Stats::mean"].end_line), (5, 5))
        self.assertEqual([p.type for p in found["first"].params], ["&'a [i32]", "HashMap<String, Vec<u8>>"])
        for name in ("add", "Stats::mean", "first"):  # lifetimes are fine
            self.assertIsNone(found[name].skip, name)
        for name in ("Stats::total", "largest", "fetch", "raw", "report", "apply", "it_works", "main"):
            self.assertIsNotNone(found[name].skip, name)
        self.assertIn("&Stats", found["report"].skip)

    def test_user_file_keeps_its_lines(self):
        source = "#![allow(dead_code)]\n//! Crate docs.\nfn helper() {}\nfn main() {\n    helper();\n}\n"
        user = RUST.user_source(source)
        self.assertEqual(user.splitlines()[2:], ["fn helper() {}", "fn __phantom_user_main() {", "    helper();", "}"])
        self.assertEqual(user.count("\n"), source.count("\n"))
        self.assertEqual(user.splitlines()[0].strip() + user.splitlines()[1].strip(), "")  # invalid in include!

    def test_recursive_call_line(self):
        source = "fn fact(n: i64) -> i64 {\n    if n == 0 { 1 }\n    else { n * fact(n - 1) }\n}\n"
        self.assertEqual(RUST.self_call_line(source, RUST.functions(source)[0]), 3)


class LiteralTest(unittest.TestCase):
    def test_plain_literals_pass(self):
        for expr in ("1", "-1", "3u8", "-128i8", "1_000", "0xff", "2.5", "-0.5f32", "1e9", '"a"', 'r#"a"b"#', 'b"x"',
                     "'c'", "'\\n'", '"\\u{1F600}"', '"é"', "true", "[1, 2]", "&[1, 2]", "&[]", "&mut [1]", "vec![]",
                     "&vec![vec![1], vec![]]", "&mut vec![1]", 'String::from("a")', '"a".to_string()', "Some(1)",
                     "None", 'HashMap::from([("a", 1)])', "HashSet::from([1])", "Vec::new()", "(1, \"a\")", "(1,)",
                     "()", "(1)", "i32::MAX", "u64::MAX", "f64::NAN", "-1 as u32", "Some(vec![(1, 'a')])"):
            self.assertTrue(RUST.literal_ok(expr), expr)

    def test_anything_that_runs_code_is_rejected(self):
        for expr in ("x", "f()", "1 + 1", "-x", "-i32::MIN", "!true", "*p", "&x", "x as u8", "1 as fn()", "1..10",
                     "[0; 1000000000]", "vec![0; 100]", "vec![x]", "vec![f()]", "vec!(1)", "std::vec![1]",
                     'println!("x")', "std::process::exit(1)", 'include_str!("/etc/passwd")', 'env!("HOME")',
                     'format!("{}", 1)', "|| 1", "{ 1 }", "unsafe { 1 }", "S { a: 1 }", "Box::new(1)", "Ok(1)",
                     'std::fs::read_to_string("/etc/passwd")', '"a".repeat(1000000000)', "'a'.to_string()",
                     'String::from(std::env::var("HOME").unwrap())', "Some(f())", "Some", "None::<i32>",
                     "1; } fn evil() { std::process::exit(1)", '1); std::process::exit(1); (1', "1 /* c */",
                     "vec![1 /* c */]", "1 // c", "vec![1, 2].len()", "Vec::<i32>::new()", "loop {}", "1?",
                     "if true { 1 } else { 2 }", "[1, x]", "(1, 2).0", "[1][0]", "-(1)", "#[cfg(x)] 1", "a\nb"):
            self.assertFalse(RUST.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_values_follow_the_declared_types(self):
        ints = dict(RUST.edge_values("i32", "n"))
        self.assertIsNone(ints["0"])
        self.assertIsNone(ints["-1"])
        self.assertEqual(ints["i32::MAX"], "n may be as large as i32::MAX")
        self.assertEqual(ints["i32::MIN"], "n may be as small as i32::MIN")
        self.assertNotIn("-1", dict(RUST.edge_values("usize", "n")))
        self.assertIn(("None", None), RUST.edge_values("Option<i32>", "n"))  # the type allows it
        self.assertEqual(RUST.edge_values("&[i32]", "values")[:2], [("&[1, 2, 3]", None), ("&[]", None)])
        self.assertIn(('""', None), RUST.edge_values("&str", "s"))
        self.assertIn(('&mut vec![]', None), RUST.edge_values("&mut Vec<i32>", "v"))
        self.assertIn(("HashMap::from([])", None), RUST.edge_values("HashMap<String, i32>", "m"))
        self.assertEqual(RUST.edge_values("Box<i32>", "b"), [])
        self.assertEqual(RUST.edge_values("&dyn Fn()", "f"), [])
        self.assertTrue(RUST.nullable("Option<&str>"))
        self.assertFalse(RUST.nullable("&str"))

    def test_an_index_must_fit_its_collection(self):
        fn = RUST.functions("fn pick(values: &[i32], index: usize) -> i32 { values[index] }")[0]
        inputs = RUST.edge_inputs(fn)
        self.assertEqual((inputs[0].args, inputs[0].assumptions), (["&[1, 2, 3]", "1"], []))
        lengths = {"&[1, 2, 3]": 3, "&[]": 0, "&[0]": 1}
        for made in inputs:
            index = int(made.args[1]) if made.args[1].isdigit() else 2 ** 64
            if index >= lengths[made.args[0]]:
                self.assertIn("index may be out of range for values", made.assumptions, made.args)
        self.assertIn(["&[1, 2, 3]", "0"], [i.args for i in inputs if not i.assumptions])

    def test_start_after_end_needs_an_assumption(self):
        fn = RUST.functions("fn days(start: u32, end: u32) -> u32 { end - start }")[0]
        later = [i for i in RUST.edge_inputs(fn) if i.args == ["1", "0"]]
        self.assertEqual(later[0].assumptions, ["start may be after end"])

    def test_model_inputs(self):
        fn = RUST.functions("fn square(n: i64, s: &str) -> i64 { n * n }")[0]
        self.assertIsNone(RUST.model_input(fn, ["std::process::exit(1)", '""'], {}))
        self.assertIsNone(RUST.model_input(fn, ["1"], {}))
        self.assertEqual(RUST.model_input(fn, ["3", '"a"'], {}).assumptions, [])
        big = RUST.model_input(fn, ["9223372036854775807", '"a"'], {})
        self.assertEqual(big.assumptions, ["n may be as large as 9223372036854775807"])


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if RUST.sandbox() != "docker":
            self.skipTest("no Rust image: docker pull rust:1-slim-bookworm")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)
        for name in ("cache", "model_cache"):  # earlier runs of the same source must not answer for this one
            self.addCleanup(setattr, specter, name, getattr(specter, name))
            setattr(specter, name, specter.Cache())

    def test_buggy_sample(self):
        answer = specter.run((SAMPLES / "buggy.rs").read_text(), "rust", use_model=False)
        found = bugs(answer)
        expected = {"average": ("attempt to divide by zero", 10), "pairwise_diffs": ("index out of bounds", 17),
                    "largest": ("called `Option::unwrap()` on a `None` value", 24),
                    "parse_port": ("called `Result::unwrap()` on an `Err` value", 30),
                    "display_name": ("no entry found for key", 35), "middle": ("attempt to subtract with overflow", 40),
                    "initial": ("byte index is not a char boundary", 48), "collatz_steps": ("Timeout", 52),
                    "factorial": ("stack overflow", 66)}
        for name, (error, line) in expected.items():
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[name])
        self.assertEqual(set(found), set(expected))  # is_leap_year answers wrong without failing: an intent check
        kinds = {(f["function"], f["failure"]["kind"]) for f in answer["findings"] if f["status"] == "bug"}
        self.assertIn(("collatz_steps", "timeout"), kinds)
        self.assertIn(("factorial", "crash"), kinds)

    def test_correct_sample_has_no_findings(self):
        answer = specter.run((SAMPLES / "correct.rs").read_text(), "rust", use_model=False)
        self.assertEqual([f["status"] for f in answer["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "buggy.rs").read_text()
        result = specter.replay(source, "rust", "average", {"exprs": ["&[]"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "attempt to divide by zero", 10))
        self.assertIn("user.rs:10", result["traceback"])
        self.assertEqual(specter.replay(source, "rust", "average", {"exprs": ["&[2, 4]"]}), {"ok": True, "result": "3"})

    def test_harness_details(self):
        source = """#![allow(dead_code)]
fn main() { std::process::exit(3); }
fn total(values: &[u8]) -> u8 { values.iter().sum() }
fn ping(n: i32) -> i32 { pong(n) }
fn pong(n: i32) -> i32 { if n == 0 { 0 } else { ping(n - 1) } }
struct Opaque;
fn make() -> Opaque { Opaque }
"""
        fns = {f.name: f for f in RUST.functions(source)}
        overflow, fine = RUST.run_inputs(source, fns["total"], [Input(["&[200, 100]"]), Input(["&[1, 2]"])], "docker")
        self.assertEqual((overflow["error"], overflow["line"]), ("attempt to add with overflow", 3))  # inside std
        self.assertEqual(fine, {"ok": True, "result": "3"})  # and main didn't run
        crash = RUST.run_inputs(source, fns["ping"], [Input(["-1"])], "docker")[0]
        self.assertEqual((crash["kind"], crash["error"], crash["line"]), ("crash", "stack overflow", None))
        self.assertEqual(RUST.run_inputs(source, fns["make"], [Input([])], "docker")[0]["ok"], True)  # no Debug

    def test_model_input_finds_what_edge_values_miss(self):
        source = ('pub fn discount(code: &str) -> u32 {\n    let pct = if code == "HALF" { 50 } else { 0 };\n'
                  "    100 / (50 - pct)\n}\n")
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 3, "bug": "HALF divides by zero", "trigger_condition": "", "confidence": 0.9,
             "inputs": [['"HALF"'], ["std::process::exit(1)"], ['{ panic!("x") }']]}]}
        provider = llm.ScriptedProvider([{"match": "discount", "answer": answer}])
        llm.set_provider(provider)
        found = [f for f in specter.run(source, "rust")["findings"] if f["status"] == "bug"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["error"], f["failure"]["line"]) for f in found],
                         [("model", 'discount("HALF")', "attempt to divide by zero", 3)])
        self.assertIn("<code-", provider.calls[0]["prompt"])

    def test_model_input_that_does_not_type_check_keeps_edge_findings(self):
        source = "pub fn share(total: u32, people: u32) -> u32 {\n    total / people\n}\n"
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 2, "bug": "", "trigger_condition": "", "confidence": 0.9,
             "inputs": [['"ten"', "2"]]}]}
        llm.set_provider(llm.ScriptedProvider([{"match": "share", "answer": answer}]))
        found = [f for f in specter.run(source, "rust")["findings"] if f["status"] == "bug"]
        self.assertEqual([(f["origin"], f["failure"]["error"], f["failure"]["line"], f["severity"]) for f in found],
                         [("edge", "attempt to divide by zero", 2, "error")])


if __name__ == "__main__":
    unittest.main()
