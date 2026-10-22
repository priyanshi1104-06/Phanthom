"""PHP plugin: parsing, literal checking, inputs, and Specter end to end in the php:8.4-cli sandbox."""
import unittest
from pathlib import Path
from unittest import mock

from runner import languages, llm, specter
from runner.languages.base import Input

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "php"
PHP = languages.get("php")

SOURCE = """<html><title>tools</title>
<?php
declare(strict_types=1);
namespace App\\Util;

/** @param int[] $values */
function total(array $values, ?int $limit = null): int { return 0; }
function untyped($name, $count = 3, $opts = []) {}
function by_ref(array &$items): void {}
function many(int ...$xs): int { return 0; }
if (!function_exists('helper')) {
    function helper(string $s, int $pad = null): string { return $s; }
}
final class Money {
    public static function format(int $cents): string { return ''; }
    private static function secret(): void {}
    public function instance(): void {}
}
class Model extends \\Framework\\Model {
    public static function find(int $id): ?self { return null; }
}
echo total([1]);
"""


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


class ParsingTest(unittest.TestCase):
    def test_functions_methods_and_namespaces(self):
        found = {f.name: f for f in PHP.functions(SOURCE)}
        self.assertEqual(list(found), [
            "App\\Util\\total", "App\\Util\\untyped", "App\\Util\\by_ref", "App\\Util\\many", "App\\Util\\helper",
            "App\\Util\\Money::format", "App\\Util\\Money::secret", "App\\Util\\Money::instance",
            "App\\Util\\Model::find"])
        total = found["App\\Util\\total"]
        self.assertEqual((total.line, total.end_line, total.returns), (7, 7, "int"))
        self.assertEqual([(p.name, p.type) for p in total.params], [("values", "array"), ("limit", "?int")])
        self.assertEqual(total.docs, {"values": "int[]"})
        self.assertEqual([(p.name, p.type) for p in found["App\\Util\\untyped"].params],
                         [("name", None), ("count", "default:3"), ("opts", "default:[]")])
        self.assertEqual([p.type for p in found["App\\Util\\helper"].params], ["string", "int|null"])  # = null
        runnable = {name for name, f in found.items() if f.skip is None}
        self.assertEqual(runnable, {"App\\Util\\total", "App\\Util\\untyped", "App\\Util\\helper",
                                    "App\\Util\\Money::format"})
        self.assertIn("variadic", found["App\\Util\\many"].skip)
        self.assertIn("instance", found["App\\Util\\Money::instance"].skip)
        self.assertIn("private", found["App\\Util\\Money::secret"].skip)
        self.assertIn("Framework\\Model", found["App\\Util\\Model::find"].skip)

    def test_only_declarations_are_loaded(self):
        context = PHP.context(SOURCE, None)
        self.assertEqual(context.count("\n"), SOURCE.count("\n"))
        for gone in ("<html>", "echo", "class Model"):  # HTML, top-level code, a class whose parent is elsewhere
            self.assertNotIn(gone, context)
        for kept in ("<?php", "declare(strict_types=1);", "namespace App\\Util;", "function_exists('helper')",
                     "final class Money"):
            self.assertIn(kept, context)

    def test_main_is_the_entry_point_and_is_not_run(self):
        found = {f.name: f.skip for f in PHP.functions(
            "<?php\nfunction main(array $argv): int { return 0; }\nfunction run(): int { return 0; }\n"
            "final class App { public static function main(): void {} }\n")}
        self.assertIn("entry point", found["main"])
        self.assertIn("entry point", found["App::main"])
        self.assertIsNone(found["run"])

    def test_top_level_setup_stays_and_running_the_program_is_blanked(self):
        source = ("<?php\nrequire_once __DIR__ . '/vendor/autoload.php';\ninclude 'helpers.php';\n"
                  "define('PER_PAGE', 20);\n$RATES = ['EUR' => 1.0];\n$i++;\n"
                  "foreach ([1, 2] as $n) { $SQUARES[$n] = $n * $n; }\nif (!defined('X')) { define('X', 1); }\n"
                  "main();\n$app->run();\nApp::start();\necho 'hi';\nprint 'hi';\nexit(1);\n$db = connect() or die('no');\n"
                  "if (PHP_SAPI === 'cli') { $x = 1; }\nif (__FILE__ === realpath($_SERVER['SCRIPT_FILENAME'])) { main(); }\n"
                  "foreach ($argv as $a) { echo $a; }\nthrow new Exception('x');\n$h = <<<EOT\nx\nEOT;\n"
                  "?>\n<p>demo</p>\n<?= $x ?>\n<?php\nreturn [1];\n")
        context = PHP.context(source, None)
        self.assertEqual(context.count("\n"), source.count("\n"))
        for kept in ("include 'helpers.php';", "define('PER_PAGE', 20);", "$RATES = ['EUR' => 1.0];", "$i++;",
                     "$SQUARES[$n] = $n * $n;", "if (!defined('X'))", "$h = <<<EOT"):
            self.assertIn(kept, context)
        for gone in ("require_once", "main()", "->run()", "App::start", "echo", "print", "exit", "die", "PHP_SAPI",
                     "__FILE__", "$argv", "throw", "<p>", "<?=", "return"):
            self.assertNotIn(gone, context)

    def test_half_typed_code_has_errors(self):
        self.assertTrue(PHP.has_errors("<?php\nfunction f(int $x { return $x; }\n"))
        self.assertFalse(PHP.has_errors((SAMPLES / "crashes.php").read_text()))


class LiteralTest(unittest.TestCase):
    def test_plain_literals_pass(self):
        for expr in ("1", "-1", "+2", "1.5e3", "0x1F", "1_000", "'it\\'s'", '"a\\tb"', '"\\$x"', "true", "NULL",
                     "False", "[]", "[1, [2, 3]]", "['a' => 1, 'b' => [true, null]]", "array(1, 2)", "PHP_INT_MAX",
                     "-PHP_INT_MAX", "-INF", "NAN", '"\\u{41}"'):
            self.assertTrue(PHP.literal_ok(expr), expr)

    def test_anything_that_runs_code_is_rejected(self):
        for expr in ("$x", '"$x"', '"{$x}"', '"${x}"', '"\\\\$x"', '"\\{$x}"', '"a$"', "`id`", 'system("id")',
                     "exec('id')", "shell_exec('id')", "eval('1')", "include 'x.php'", "new Foo()", "fn() => 1",
                     "function () { return 1; }", "(int) '3'", "'a' . 'b'", "1 + 1", '1); system("x"); (1',
                     '"a" ?> <?php system(1); ("a"', "1 ?>", "[1, /* c */ 2]", "[&$x]", "[...[1]]", "!1", "~1", "@1",
                     "- -1", "-(1)", "-'a'", "E_ALL", "__FILE__", "PHP_VERSION", "\\PHP_INT_MAX", "Foo::BAR",
                     "null ?? 1", "$GLOBALS", "print 1", "exit(1)", "1\n", "<<<EOT\nx\nEOT", "[system('id') => 1]",
                     "clone $x", "throw new E()", "(1)"):
            self.assertFalse(PHP.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_declared_types_decide_the_values(self):
        values = dict(PHP.edge_values("int", "n"))
        self.assertEqual(list(values)[:3], ["1", "0", "-1"])
        self.assertIsNone(values["0"])
        self.assertEqual(values["PHP_INT_MAX"], "n may be as large as PHP_INT_MAX")
        self.assertNotIn("null", values)  # PHP itself rejects null for a plain int
        self.assertEqual(PHP.edge_values("?string", "s")[-1], ("null", None))
        self.assertEqual(PHP.edge_values("Foo", "x"), [])  # an object: no literal makes one
        self.assertTrue(PHP.nullable("int|null") and PHP.nullable("mixed") and not PHP.nullable("int"))

    def test_arrays_follow_the_docblock_name_and_key_reads(self):
        self.assertEqual(PHP.edge_values("array", "values")[:2],
                         [("[1, 2, 3]", "values holds ints (guessed from its name)"), ("[]", None)])
        self.assertEqual(PHP.edge_values("array", "names")[0],
                         ("['a', 'b', 'c']", "names holds strings (guessed from its name)"))
        self.assertEqual(PHP.edge_values("array", "items", "string[]")[0], ("['a', 'b', 'c']", None))
        self.assertEqual(PHP.edge_values("array", "counts", "array<string, int>"), [("['a' => 1]", None), ("[]", None)])
        self.assertIn(("[]", "xs may be empty"), PHP.edge_values("array", "xs", "non-empty-list<int>"))
        # a map whose needed keys are unknown: every value is an assumption
        self.assertTrue(all(a for _, a in PHP.edge_values("array", "user")))
        self.assertTrue(all(a for _, a in PHP.edge_values("array", "items", None, True)))
        fn = PHP.functions("<?php function label($row) { return $row['name']; }")[0]
        self.assertTrue(all(i.assumptions for i in PHP.edge_inputs(fn)))

    def test_untyped_parameters_work_like_javascript(self):
        values = dict(PHP.edge_values(None, "values"))
        self.assertEqual(values["[]"], "values is an array (guessed from its name)")
        self.assertEqual(values["null"], "values may be null")  # worded for severity "info"
        self.assertEqual(dict(PHP.edge_values(None, "count"))["0"], "count is an int (guessed from its name)")
        self.assertEqual(PHP.edge_values("default:3", "label")[:2], [("1", None), ("0", None)])  # the default says
        self.assertEqual(PHP.edge_values(None, "s", "string")[0], ("'abc'", None))  # so does the docblock
        self.assertTrue(all(a for _, a in PHP.edge_values(None, "thing")))  # kind unknown
        self.assertEqual(PHP.edge_values("default:null", "x")[-1], ("null", None))
        self.assertEqual(PHP.edge_values(None, "x", "int|null")[0], ("1", None))  # the docblock decides

    def test_edge_inputs_vary_one_parameter_and_keep_indexes_in_range(self):
        fn = PHP.functions("<?php function nth(array $items, int $index) { return $items[$index]; }")[0]
        inputs = {tuple(i.args): i.assumptions for i in PHP.edge_inputs(fn)}
        guess = "items holds ints (guessed from its name)"
        self.assertEqual(list(inputs)[0], ("[1, 2, 3]", "1"))
        self.assertEqual(inputs[("[1, 2, 3]", "0")], [guess])
        self.assertEqual(inputs[("[]", "1")], ["index may be out of range for items"])
        self.assertEqual(inputs[("[1, 2, 3]", "-1")], [guess, "index may be out of range for items"])
        self.assertNotIn(("[]", "0"), inputs)

    def test_model_inputs(self):
        fn = PHP.functions("<?php function f(int $n, $s) {}")[0]
        made = PHP.model_input(fn, ["NULL", "'a'"], {})
        self.assertIn("n may be null", made.assumptions)
        self.assertIn("the parameters have no types, so this input is assumed to be allowed", made.assumptions)
        self.assertIsNone(PHP.model_input(fn, ["1", "`id`"], {}))
        self.assertIsNone(PHP.model_input(fn, ["1"], {}))


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if PHP.sandbox() != "docker":
            self.skipTest("no PHP sandbox: docker pull php:8.4-cli")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)
        for name in ("cache", "model_cache"):  # fresh caches: every test really runs its code
            patcher = mock.patch.object(specter, name, specter.Cache())
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_buggy_sample(self):
        answer = specter.run((SAMPLES / "crashes.php").read_text(), "php", use_model=False)
        found = bugs(answer)
        expected = {"average": ("DivisionByZeroError", 9), "split_bill": ("DivisionByZeroError", 14),
                    "display_name": ("ErrorException", 20), "parse_port": ("ErrorException", 25),
                    "shout": ("TypeError", 31), "indent": ("ValueError", 36), "find_index": ("TypeError", 46),
                    "collatz_steps": ("Timeout", 52), "factorial": ("StackOverflow", 63)}
        for name, (error, line) in expected.items():
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[name])
        self.assertNotIn("is_leap_year", found)  # a wrong answer: needs an intent check
        kinds = {(f["function"], f["failure"]["error"]): f["failure"]["kind"]
                 for f in answer["findings"] if f["status"] == "bug"}
        self.assertEqual(kinds[("collatz_steps", "Timeout")], "timeout")
        self.assertEqual(kinds[("find_index", "TypeError")], "return_type")

    def test_correct_sample(self):
        answer = specter.run((SAMPLES / "correct.php").read_text(), "php", use_model=False)
        self.assertEqual([(f["function"], f["status"]) for f in answer["findings"]],
                         [("App\\Util\\clamp", "no_bug_found"), ("App\\Util\\safe_average", "no_bug_found"),
                          ("App\\Util\\percent", "no_bug_found"), ("App\\Util\\Money::format", "no_bug_found")])

    def test_replay(self):
        source = (SAMPLES / "crashes.php").read_text()
        result = specter.replay(source, "php", "average", {"exprs": ["[]"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "DivisionByZeroError", 9))
        self.assertEqual(specter.replay(source, "php", "average", {"exprs": ["[2, 4]"]}), {"ok": True, "result": "3.0"})

    def test_what_is_not_a_bug(self):
        source = ("<?php\nfunction check(int $n): int\n{\n    if ($n === 0) {\n"
                  "        throw new InvalidArgumentException('n must not be 0');\n    }\n    assert($n > -5);\n"
                  "    if ($n === 7) {\n        exit(1);\n    }\n    return $n === 8 ? helper($n) : $n;\n}\n")
        fn = PHP.functions(source)[0]
        results = PHP.run_inputs(source, fn, [Input([a]) for a in ("0", "-9", "7", "8", "'x'", "3")], "docker")
        # thrown on purpose, a failed assert(), exit(), a helper from another file, a string for an int
        self.assertEqual([r.get("kind") for r in results[:5]],
                         ["exception", "exception", "exit", "unavailable", "invalid_input"])
        self.assertEqual([PHP.deliberate(source, r) for r in results[:2]], [True, True])
        self.assertEqual(results[5], {"ok": True, "result": "3"})

    def test_failing_while_the_file_loads_is_not_entered(self):
        # the top-level setup only fails for input 1 (it reads the harness's argument), after a clean build
        source = "<?php\n$STEP = intdiv(10, (int) $argv[1] - 1);\nfunction f(int $n): int\n{\n    return $n;\n}\n"
        fn = PHP.functions(source)[0]
        results = PHP.run_inputs(source, fn, [Input(["5"]), Input(["6"])], "docker")
        self.assertEqual(results[0], {"ok": True, "result": "5"})
        self.assertEqual(results[1]["kind"], "not_entered")  # intdiv(10, 0) while loading, for input 1 only

    def test_a_broken_file_is_a_build_error(self):
        answer = specter.run("<?php\nfunction strlen(string $s): int { return 1; }\n", "php", use_model=False)
        self.assertEqual(answer["findings"][0]["status"], "error")
        self.assertIn("Cannot redeclare", answer["findings"][0]["failure"]["message"])

    def test_infinite_loop_is_a_timeout(self):
        source = ("<?php\nfunction wait_for(int $ready): int\n{\n    while ($ready < 5) {\n        usleep(1000);\n"
                  "    }\n    return $ready;\n}\n")
        failures = [f["failure"] for f in specter.run(source, "php", use_model=False)["findings"]
                    if f["status"] == "bug"]
        self.assertEqual({(f["kind"], f["error"]) for f in failures}, {("timeout", "Timeout")})

    def test_model_input_finds_what_edge_values_miss(self):
        source = "<?php\nfunction slots_left(int $booked): int\n{\n    return intdiv(100, 12 - $booked);\n}\n"
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 4, "bug": "division by zero when 12 are booked", "trigger_condition": "12",
             "confidence": 0.9, "inputs": [["12"], ["'twelve'"], ["system('id')"], ["`id`"]]}]}
        provider = llm.ScriptedProvider([{"match": "slots_left", "answer": answer}])
        llm.set_provider(provider)
        found = [f for f in specter.run(source, "php")["findings"]
                 if f["status"] == "bug" and f["failure"]["error"] == "DivisionByZeroError"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["line"], f["severity"]) for f in found],
                         [("model", "slots_left(12)", 4, "error")])
        self.assertIn("<code-", provider.calls[0]["prompt"])

    def test_model_input_that_does_not_compile_keeps_the_edge_findings(self):
        self.assertTrue(PHP.literal_ok('"\\u{110000}"'))  # tree-sitter accepts it, PHP refuses to compile it
        source = "<?php\nfunction share(int $total, int $people): int\n{\n    return intdiv($total, $people);\n}\n"
        answer = {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 4, "bug": "x", "trigger_condition": "x", "confidence": 0.9,
             "inputs": [['"\\u{110000}"', "1"], ["'ten'", "2"]]}]}
        llm.set_provider(llm.ScriptedProvider([{"match": "share", "answer": answer}]))
        found = bugs(specter.run(source, "php"))
        self.assertIn(("DivisionByZeroError", 4, "error"), found["share"])


class FindingsTest(unittest.TestCase):
    """Regression tests for the adversarial review of this plugin, one per finding."""

    def setUp(self):
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)
        for name in ("cache", "model_cache"):
            patcher = mock.patch.object(specter, name, specter.Cache())
            patcher.start()
            self.addCleanup(patcher.stop)

    def sandbox(self):
        if PHP.sandbox() != "docker":
            self.skipTest("no PHP sandbox: docker pull php:8.4-cli")

    def severities(self, source):
        """{function: {severity of each reproduced failure}}, every function present."""
        found = {}
        for f in specter.run(source, "php", use_model=False)["findings"]:
            found.setdefault(f["function"], set())
            if f["status"] == "bug":
                found[f["function"]].add(f["severity"])
        return found

    def test_plain_array_element_type_guessed_from_name_is_an_assumption(self):
        # the review: order_total([0]), upper_loop([0]) and double_ids(['']) were red errors
        total = PHP.functions("<?php\nfunction order_total(array $items): float\n{\n    $t = 0.0;\n"
                              "    foreach ($items as $item) {\n        $t += $item['price'] * $item['qty'];\n"
                              "    }\n    return $t;\n}\n")[0]
        inputs = {tuple(i.args): i.assumptions for i in PHP.edge_inputs(total)}
        self.assertEqual(inputs, {("[['price' => 1, 'qty' => 1]]",): ["items may be [['price' => 1, 'qty' => 1]]"],
                                  ("[]",): []})  # a list of records: only [] is sure to fit
        self.assertEqual(PHP.edge_values("array", "ids")[0], ("[1, 2, 3]", "ids holds ints (guessed from its name)"))
        self.sandbox()
        source = ("<?php\ndeclare(strict_types=1);\nfunction order_total(array $items): float\n{\n    $t = 0.0;\n"
                  "    foreach ($items as $item) {\n        $t += $item['price'] * $item['qty'];\n    }\n"
                  "    return $t;\n}\nfunction upper_loop(array $items): array\n{\n    $out = [];\n"
                  "    foreach ($items as $s) {\n        $out[] = strtoupper($s);\n    }\n    return $out;\n}\n"
                  "function double_ids(array $ids): array\n{\n    return array_map(fn(int $id): int => $id * 2, $ids);\n}\n")
        found = self.severities(source)
        self.assertEqual(found["order_total"], set())
        self.assertEqual(found["double_ids"], set())
        self.assertEqual(found["upper_loop"], {"warning"})  # names the guess: "items holds ints"

    def test_docblock_array_types_nested_or_nullable_are_understood(self):
        self.assertEqual(PHP.edge_values("array", "matrix", "int[][]"),
                         [("[[1, 2, 3]]", None), ("[]", None), ("[[]]", None), ("[[0]]", None)])
        self.assertEqual(PHP.edge_values("?array", "items", "string[]|null")[0], ("['a', 'b', 'c']", None))
        self.assertEqual(PHP.edge_values("?array", "items", "string[]|null")[-1], ("null", None))
        self.assertEqual(PHP.edge_values("array", "names", "?list<string>")[0], ("['a', 'b', 'c']", None))
        self.assertEqual(PHP.edge_values("array", "grid", "list<int[]>")[0], ("[[1, 2, 3]]", None))
        self.assertEqual(PHP.edge_values("array", "grid", "array<array<float>>")[0], ("[[1.5, 2.5]]", None))
        self.assertEqual(PHP.edge_values("array", "row", "array{id: int, name?: string}"),
                         [("['id' => 1]", None), ("['id' => 0]", None)])
        self.assertEqual(PHP.edge_values("array", "users", "User[]"), [("[]", None)])  # objects: only []
        self.sandbox()
        source = ("<?php\ndeclare(strict_types=1);\n/** @param int[][] $matrix */\nfunction width(array $matrix): int\n"
                  "{\n    return $matrix === [] ? 0 : count($matrix[0]);\n}\n/** @param string[]|null $items */\n"
                  "function shout_all(?array $items): array\n{\n    $out = [];\n    foreach ($items ?? [] as $s) {\n"
                  "        $out[] = strtoupper($s);\n    }\n    return $out;\n}\n")
        self.assertEqual(self.severities(source), {"width": set(), "shout_all": set()})

    def test_exceptions_created_on_purpose_outside_the_throw_line_are_rejections(self):
        source = ("<?php\nfinal class Percent\n{\n    public static function of(int $part, int $whole): float\n    {\n"
                  "        if ($whole === 0) {\n            throw self::zeroWhole();\n        }\n"
                  "        return $part / $whole * 100;\n    }\n"
                  "    private static function zeroWhole(): InvalidArgumentException\n    {\n"
                  "        return new InvalidArgumentException('whole must not be zero');\n    }\n}\n"
                  "function checked_sqrt(float $x): float\n{\n    if ($x < 0) {\n"
                  "        $e = new DomainException('negative');\n        throw $e;\n    }\n    return sqrt($x);\n}\n"
                  "/** @throws JsonException on invalid JSON */\nfunction parse_config(string $json): array\n{\n"
                  "    return json_decode($json, true, 512, JSON_THROW_ON_ERROR);\n}\n"
                  "/** @throws RuntimeException */\nfunction load(string $s): int\n{\n    return helper_check($s);\n}\n"
                  "function helper_check(string $s): int\n{\n    return intdiv(1, strlen($s));\n}\n")
        factory = {"error": "InvalidArgumentException", "line": 13, "traceback": "main.php:13\n#0 main.php(7): x()"}
        self.assertTrue(PHP.deliberate(source, factory))
        self.assertTrue(PHP.deliberate(source, {"error": "DomainException", "line": 19}))
        self.assertTrue(PHP.deliberate(source, {"error": "JsonException", "line": 27}))
        self.assertFalse(PHP.deliberate(source, {"error": "TypeError", "line": 13}))  # not what that line creates
        # @throws RuntimeException covers its subclasses on the failing path, not a DivisionByZeroError
        trace = "main.php:36\n#0 main.php(32): helper_check()"
        self.assertTrue(PHP.deliberate(source, {"error": "UnexpectedValueException", "line": 36, "traceback": trace}))
        self.assertFalse(PHP.deliberate(source, {"error": "DivisionByZeroError", "line": 36, "traceback": trace}))
        self.assertFalse(PHP.deliberate(source, {"error": "ErrorException", "line": 19, "php_error": 2}))  # a warning
        self.sandbox()
        found = self.severities(source)
        for name in ("Percent::of", "checked_sqrt", "parse_config"):
            self.assertEqual(found[name], set(), name)
        self.assertEqual(found["load"], {"error"})  # strlen('') = 0: a real bug the @throws doesn't cover

    def test_top_level_setup_is_loaded_global_array_and_define(self):
        source = ("<?php\n$RATES = ['EUR' => 1.0, 'USD' => 1.08];\ndefine('PER_PAGE', 20);\n\n"
                  "function convert(float $amount, string $currency): float\n{\n    global $RATES;\n"
                  "    if (!array_key_exists($currency, $RATES)) {\n        return $amount;\n    }\n"
                  "    return $amount * $RATES[$currency];\n}\n"
                  "function pages(int $total, int $perPage): int\n{\n    return intdiv($total + PER_PAGE - 1, $perPage);\n}\n"
                  "echo convert(1.0, 'USD');\n")
        self.assertIn("define('PER_PAGE', 20);", PHP.context(source, None))
        self.sandbox()
        answer = specter.run(source, "php", use_model=False)
        self.assertEqual([f["status"] for f in answer["findings"] if f["function"] == "convert"], ["no_bug_found"])
        self.assertIn(("DivisionByZeroError", 15, "error"), bugs(answer)["pages"])

    def test_deprecations_and_the_codes_own_notices_are_not_errors(self):
        self.sandbox()
        source = ("<?php\nfunction legacy_slug(string $title): string\n{\n"
                  "    trigger_error('legacy_slug() is deprecated, use slugify()', E_USER_DEPRECATED);\n"
                  "    return strtolower($title);\n}\nfunction jitter(float $base): float\n{\n"
                  "    return $base * (1 + lcg_value() / 10);\n}\nfunction stop(int $n): int\n{\n    if ($n < 0) {\n"
                  "        trigger_error('negative', E_USER_ERROR);\n    }\n    return $n;\n}\n"
                  "function initial(?string $s): string\n{\n    return strtoupper($s);\n}\n")
        answer = specter.run(source, "php", use_model=False)
        found = bugs(answer)
        for name in ("legacy_slug", "jitter", "stop"):
            self.assertNotIn(name, found)
        # a deprecation about the data (null for a string parameter) is still a bug
        self.assertEqual([(e, line) for e, line, _ in found["initial"]], [("ErrorException", 20)])

    def test_shebang_line_and_strict_types_still_build(self):
        source = "#!/usr/bin/env php\n<?php\ndeclare(strict_types=1);\n\nfunction ratio(int $a, int $b): int\n{\n" \
                 "    return intdiv($a, $b);\n}\n"
        self.assertTrue(PHP.context(source, None).startswith("#!/usr/bin/env php\n<?php\ndeclare(strict_types=1);"))
        self.sandbox()
        self.assertIn(("DivisionByZeroError", 7, "error"), bugs(specter.run(source, "php", use_model=False))["ratio"])

    def test_untyped_parameter_kind_guessed_from_its_name_is_a_warning(self):
        self.sandbox()
        source = "<?php\n/** Splits \"a, b\" into ['a', 'b']. */\nfunction parse_list($list)\n{\n" \
                 "    return array_map('trim', explode(',', $list));\n}\n"
        self.assertEqual(self.severities(source)["parse_list"], {"warning", "info"})

    def test_inputs_do_not_share_files_they_write(self):
        self.sandbox()
        source = ("<?php\nfunction bump(int $n): int\n{\n    static $calls = 0;\n    $calls++;\n"
                  "    $f = __DIR__ . '/state.txt';\n    $seen = is_file($f) ? (int) file_get_contents($f) : 0;\n"
                  "    file_put_contents($f, (string) ($seen + 1));\n    file_put_contents('cwd.txt', 'x', FILE_APPEND);\n"
                  "    return $calls * 1000 + $seen + strlen(file_get_contents('cwd.txt'));\n}\n")
        fn = PHP.functions(source)[0]
        results = PHP.run_inputs(source, fn, [Input(["1"]), Input(["2"]), Input(["3"])], "docker")
        self.assertEqual([r.get("result") for r in results], ["1001", "1001", "1001"])


if __name__ == "__main__":
    unittest.main()
