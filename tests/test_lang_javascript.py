"""JavaScript and TypeScript plugins: parsing, literal checking, inputs, and Specter end to end in Node."""
import unittest
from pathlib import Path

from runner import languages, llm, specter

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
JS = languages.get("javascript")
TS = languages.get("typescript")


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


class ParsingTest(unittest.TestCase):
    def test_functions_and_parameters(self):
        source = ("function a(x, y = 2) {}\nconst b = (items) => items[0];\n"
                  "export function c(n: number, s?: string): string { return s; }\nfunction* gen() {}\n"
                  "function d({x}) {}\n")
        found = {f.name: f for f in TS.functions(source)}
        self.assertEqual(list(found), ["a", "b", "c", "gen", "d"])
        self.assertEqual([(p.name, p.type) for p in found["a"].params], [("x", None), ("y", "default:2")])
        self.assertEqual((found["b"].line, found["c"].line), (2, 3))
        self.assertEqual([(p.name, p.type) for p in found["c"].params],
                         [("n", "number"), ("s", "string | undefined")])
        self.assertEqual(found["c"].returns, "string")
        self.assertIsNotNone(found["gen"].skip)
        self.assertIsNotNone(found["d"].skip)  # destructuring: not yet

    def test_top_level_statements_do_not_run(self):
        source = "function f() {}\nconsole.log(f());\nmain();\nconst X = 1;\n"
        context = JS.context(source, None)
        self.assertEqual(context.count("\n"), source.count("\n"))
        self.assertNotIn("console", context)
        self.assertNotIn("main", context)
        self.assertIn("const X = 1;", context)


    def test_entry_points_and_never_returning_functions_are_skipped(self):
        source = ("function main() {}\nexport function fail(m: string): never {\n  throw new Error(m);\n}\n"
                  "export function ok(m: string): string {\n  return m;\n}\n")
        skips = {f.name: f.skip for f in TS.functions(source)}
        self.assertIn("entry point", skips["main"])
        self.assertIn("never", skips["fail"])
        self.assertIsNone(skips["ok"])
        jsdoc = JS.functions("/** @returns {never} */\nfunction fail(m = '') {\n  throw new Error(m);\n}\n")[0]
        self.assertIn("never", jsdoc.skip)

    def test_context_keeps_what_the_function_needs_and_blanks_script_code(self):
        strict = '"use strict";\n\nfunction countItems(items) {\n  total = items.length;\n  return total;\n}\n'
        self.assertEqual(JS.context(strict, JS.functions(strict)[0]), strict)  # the directive stays
        script = ('const fs = require("fs");\n\nfunction wordCount(text) {\n  return text.length;\n}\n\n'
                  'const input = fs.readFileSync("input.txt", "utf8");\nconsole.log(wordCount(input));\n')
        context = JS.context(script, JS.functions(script)[0])
        self.assertEqual(context.count("\n"), script.count("\n"))
        self.assertIn('const fs = require("fs");', context)  # a builtin: harmless to load
        self.assertNotIn("readFileSync", context)  # script code the function doesn't use
        self.assertNotIn("console.log", context)
        table = ("const SQUARES: number[] = [];\nfor (let i = 0; i < 10; i++) SQUARES.push(i * i);\n\n"
                 "export function square(digit: number): number {\n  return SQUARES[digit];\n}\n")
        self.assertIn("SQUARES.push(i * i);", TS.context(table, TS.functions(table)[0]))  # fills what it reads
        imported = ('import { round2 } from "./money";\n\nexport function clamp(x: number): number {\n'
                    "  return Math.max(x, 0);\n}\n")
        self.assertNotIn("./money", TS.context(imported, TS.functions(imported)[0]))  # unused: not loaded


class LiteralTest(unittest.TestCase):
    def test_plain_literals_pass(self):
        for expr in ("1", "-1", "0.5", '"a"', "'b'", "`plain`", "[]", '[1, "a", null]', '{"a": [1]}',
                     "{a: 1}", "true", "undefined", "NaN", "-Infinity", "(1)"):
            self.assertTrue(JS.literal_ok(expr), expr)

    def test_anything_that_runs_code_is_rejected(self):
        for expr in ("x", "f()", "1 + 1", "`${process.exit(1)}`", "require('fs')", "() => 1",
                     '1); require("child_process").execSync("id"); (1', "1]; process.exit(3); [1",
                     "!0", "typeof x", "new Array(5)", "[...x]", "{a: f()}", "{[k]: 1}", "a\n", "process.env",
                     "void 0", "this", "globalThis"):
            self.assertFalse(JS.literal_ok(expr), expr)

    def test_literals_that_throw_or_do_not_compile_are_rejected(self):
        for expr in ("+1n", "[+1n]", "+{__proto__: null}", "{a: +{__proto__: null}}", "{__proto__: []}",
                     "[void 0]", "[typeof 1]", "[delete 1]", "[!0]", "-[]", "+'12'", "~1",  # signs: numbers only
                     '"\\u{110000}"', "`\\u{FFFFFF}`", "`\\1`", "`\\8`", '"\\1"', '"\\8"', "010", "00", "0_1",
                     "07.5", "{__proto__: 1, __proto__: 2}", '{"__proto__": [], __proto__: []}'):
            for lang in (JS, TS):
                self.assertFalse(lang.literal_ok(expr), (lang.name, expr))
        for expr in ("-1n", "-0.5", "+3", "0", "0.5", "`\\${x}`", '"\\u{10FFFF}"', '"\\0"'):
            self.assertTrue(JS.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_names_and_types_decide_the_values(self):
        values = dict(JS.edge_values(None, "values"))
        self.assertIn("[]", values)
        self.assertEqual(values["[]"], "values is an array (guessed from its name)")  # a guess, not a type
        self.assertEqual(values["null"], "values may be null")
        self.assertTrue(all(assumption for _, assumption in JS.edge_values(None, "thing")))  # unknown kind
        self.assertTrue(all(assumption for _, assumption in JS.edge_values(None, "user")))  # unknown fields
        self.assertEqual(TS.edge_values("number | null", "n")[-1], ("null", None))  # the type allows it
        self.assertEqual(TS.edge_values("{ name: string }", "user"), [])  # shape unknown: skipped

    def test_record_values_follow_their_value_type(self):
        self.assertEqual(TS.edge_values("Record<string, User>", "users"), [("{}", None)])  # unknown shape
        self.assertEqual(TS.edge_values("Record<string, string[]>", "tags"),
                         [('{"a": ["abc", "abc"]}', None), ("{}", None)])
        self.assertEqual(TS.edge_values("{ [key: string]: number }", "m"), [('{"a": 3}', None), ("{}", None)])

    def test_a_kind_guessed_from_the_name_is_an_assumption(self):
        values = JS.edge_values(None, "name")
        self.assertEqual(values[0], ('"abc"', "name is a string (guessed from its name)"))
        self.assertTrue(all(assumption for _, assumption in values))
        self.assertEqual(JS.edge_values("default:0", "lo")[0], ("3", None))  # a default is as good as a type
        documented = JS.functions("/** @param {number[]} xs */\nfunction f(xs) {\n  return xs;\n}\n")[0]
        self.assertEqual(documented.params[0].type, "number[]")  # a JSDoc type is declared
        self.assertEqual(JS.edge_inputs(documented)[0].assumptions, [])

    def test_model_inputs_of_the_wrong_type_are_dropped(self):
        shout = TS.functions("export function shout(text: string): string {\n  return text.toUpperCase();\n}\n")[0]
        self.assertIsNone(TS.model_input(shout, ["[]"], None))
        self.assertIsNone(TS.model_input(shout, ["1"], None))
        self.assertEqual(TS.model_input(shout, ['"x"'], None).assumptions, [])
        self.assertEqual(TS.model_input(shout, ["null"], None).assumptions, ["text may be null"])
        documented = JS.functions("/** @param {number[]} xs */\nfunction f(xs) {\n  return xs;\n}\n")[0]
        self.assertIsNone(JS.model_input(documented, ['"a"'], None))
        defaulted = JS.functions("function f(sep = ' ') {\n  return sep;\n}\n")[0]
        self.assertEqual(JS.model_input(defaulted, ["1"], None).assumptions, ["sep may be 1 (its default is a string)"])

    def test_edge_inputs_vary_one_parameter_at_a_time(self):
        fn = TS.functions("function f(a: number, b: string) {}")[0]
        inputs = [tuple(i.args) for i in TS.edge_inputs(fn)]
        self.assertEqual(inputs[0], ("3", '"abc"'))
        self.assertIn(("0", '"abc"'), inputs)
        self.assertIn(("3", '""'), inputs)
        self.assertNotIn(("0", '""'), inputs)


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if JS.sandbox() is None:
            self.skipTest("no Node sandbox: python phantom.py setup javascript")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def test_javascript_samples(self):
        found = bugs(specter.run((SAMPLES / "javascript" / "buggy.js").read_text(), "javascript", use_model=False))
        expected = {"average": ("TypeError", 8), "firstWord": ("TypeError", 15), "lastPrice": ("TypeError", 21),
                    "padLabel": ("RangeError", 26), "parsePort": ("TypeError", 32), "collatzSteps": ("Timeout", 36),
                    "factorial": ("RangeError", 47)}  # declared by JSDoc types: errors
        for name, (error, line) in expected.items():
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[name])
        self.assertNotIn("isLeapYear", found)  # a wrong answer: needs an intent check
        correct = specter.run((SAMPLES / "javascript" / "correct.js").read_text(), "javascript", use_model=False)
        severities = {f["severity"] for f in correct["findings"] if f["status"] == "bug"}
        self.assertLessEqual(severities, {"info"})  # at most "crashes on null"

    def test_typescript_samples(self):
        found = bugs(specter.run((SAMPLES / "typescript" / "buggy.ts").read_text(), "typescript", use_model=False))
        expected = {"average": "TypeError", "findIndex": "ReturnTypeError", "initials": "TypeError",
                    "chunk": "Timeout", "topScore": "TypeError", "formatPrice": "RangeError", "shout": "TypeError"}
        for name, error in expected.items():
            with self.subTest(function=name):
                self.assertIn(error, [e for e, _, severity in found[name] if severity == "error"])
        self.assertNotIn("isLeapYear", found)
        correct = specter.run((SAMPLES / "typescript" / "correct.ts").read_text(), "typescript", use_model=False)
        self.assertEqual([f["status"] for f in correct["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "javascript" / "buggy.js").read_text()
        result = specter.replay(source, "javascript", "average", {"exprs": ["[]"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "TypeError", 8))
        fixed = specter.replay(source, "javascript", "average", {"exprs": ["[2, 4]"]})
        self.assertEqual(fixed, {"ok": True, "result": "3"})

    def test_model_inputs_are_run_and_injections_dropped(self):
        source = ("function parseAge(text) {\n  const n = parseInt(text, 10);\n"
                  "  if (n > 150) return n.toFixed(-1);\n  return n;\n}\n")  # not a deliberate throw
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 3, "bug": "throws above 150", "trigger_condition": "", "confidence": 0.8,
             "inputs": [['"200"'], ["process.exit(1)"], ['(() => { throw new RangeError("age out of range") })()']]}]}
        provider = llm.ScriptedProvider([{"match": "parseAge", "answer": answer}])
        llm.set_provider(provider)
        result = specter.run(source, "javascript")
        found = [f for f in result["findings"] if f["status"] == "bug"]
        self.assertEqual([(f["origin"], f["input"]["call"]) for f in found], [("model", 'parseAge("200")')])
        self.assertEqual(found[0]["failure"]["line"], 3)
        self.assertIn("<code-", provider.calls[0]["prompt"])  # the code is fenced as data


if __name__ == "__main__":
    unittest.main()
