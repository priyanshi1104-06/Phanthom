"""Java plugin: parsing, literal checking, inputs, and Specter end to end in the Temurin JDK."""
import unittest
from pathlib import Path

from runner import languages, llm, specter

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "java"
JAVA = languages.get("java")


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


class ParsingTest(unittest.TestCase):
    SOURCE = """package demo;
import java.util.*;
public class Stats {
    public static double average(int[] values) { return 0; }
    static <T> T first(List<T> xs) { return xs.get(0); }
    static int sum(String... parts) { return 0; }
    public int size(Map<String, Integer> m) { return 1; }
    private static int get(int values[], @Nullable Integer i) { return 0; }
    static int get(int[] a) { return 0; }
    static class Inner {
        static int g(final long n) { return 1; }
    }
    Stats() {}
    public static void main(String[] args) {}
    abstract static class Shape { abstract double area(); }
    @Deprecated
    static @NonNull String name(Map<String, List<Integer>> m) { return ""; }
}
interface Shapes { static int count() { return 1; } int m(); default int d() { return 2; } }
enum Color { RED; static Color parse(char c) { return RED; } }
record Point(int x) { static Point origin() { return new Point(0); } }
"""

    def test_static_methods_types_and_skips(self):
        found = {f.name: f for f in JAVA.functions(self.SOURCE)}
        self.assertEqual(list(found), ["Stats.average", "Stats.first", "Stats.sum", "Stats.size", "Stats.get",
                                       "Stats.Inner.g", "Stats.Stats", "Stats.main", "Stats.name", "Shapes.count",
                                       "Shapes.d", "Color.parse", "Point.origin"])
        average = found["Stats.average"]
        self.assertEqual((average.line, average.end_line, average.returns, average.skip), (4, 4, "double", None))
        self.assertEqual([(p.name, p.type) for p in average.params], [("values", "int[]")])
        get = JAVA.functions(self.SOURCE)[4]
        self.assertEqual([(p.name, p.type) for p in get.params], [("values", "int[]"), ("i", "@Nullable Integer")])
        self.assertTrue(get.private)  # run through reflection
        self.assertEqual(found["Stats.Inner.g"].owner, "Stats$Inner")
        self.assertEqual(found["Stats.name"].line, 17)  # the name's line, not the annotation's
        self.assertTrue(found["Stats.name"].non_null)
        for name, reason in (("Stats.first", "generic"), ("Stats.sum", "varargs"), ("Stats.size", "instance"),
                             ("Stats.Stats", "constructor"), ("Stats.main", "entry point"), ("Shapes.d", "instance")):
            self.assertIn(reason, found[name].skip, name)
        overload = [f for f in JAVA.functions(self.SOURCE) if f.name == "Stats.get"][1]
        self.assertIn("overloaded", overload.skip)
        for name in ("Stats.Inner.g", "Shapes.count", "Color.parse", "Point.origin"):
            self.assertIsNone(found[name].skip, name)

    def test_package_line_is_blanked_and_file_named_after_the_public_class(self):
        context = JAVA.context(self.SOURCE, None)
        self.assertEqual(context.count("\n"), self.SOURCE.count("\n"))
        self.assertNotIn("package", context)
        self.assertEqual(JAVA.file_class(self.SOURCE), "Stats")
        self.assertEqual(JAVA.file_class("class A {}\nclass B {}\n"), "A")
        files = JAVA.program(context, JAVA.functions(self.SOURCE)[0], [])["files"]
        self.assertEqual(sorted(files), ["PhantomHarness.java", "Stats.java"])

    def test_trace_line_skips_the_harness_and_the_jdk(self):
        trace = ("java.lang.NumberFormatException: For input string: \"\"\n"
                 "\tat java.base/java.lang.Integer.parseInt(Integer.java:672)\n"
                 "\tat Buggy.parsePort(Buggy.java:36)\n\tat PhantomHarness.call(PhantomHarness.java:30)\n")
        self.assertEqual(JAVA.trace_line(trace), 36)
        self.assertIsNone(JAVA.trace_line("\tat PhantomHarness.main(PhantomHarness.java:12)\n"))


class LiteralTest(unittest.TestCase):
    def test_plain_literals_pass(self):
        for expr in ("1", "-1", "0x1F", "2L", "-1.5e3", "0b101", "1_000", '"a\\"b"', "'c'", "'\\''", "true", "null",
                     "new int[]{1, -2}", "new String[]{}", "new int[][]{{1}, {}}", "new Integer[]{1, null}",
                     "List.of(1, 2)", 'Map.of("a", 1)', 'Set.of("x")', "Arrays.asList(1, null)",
                     "new ArrayList<>(List.of(1))", "new HashMap<String, Integer>(Map.of())", "new TreeSet<>()",
                     "Integer.MAX_VALUE", "-Long.MIN_VALUE", "Double.NaN", "List.of(new int[]{1})"):
            self.assertTrue(JAVA.literal_ok(expr), expr)

    def test_anything_that_runs_code_is_rejected(self):
        for expr in ("x", "f()", "1 + 1", '"a" + "b"', "System.exit(1)", "Runtime.getRuntime()",
                     'new java.io.File("x")', 'new ProcessBuilder("id")', "(int) 1", "x -> x", "String::valueOf",
                     '"\\u0022 + System.exit(1) + \\u0022"', "'\\u0027'",  # javac decodes \u escapes before strings
                     "1); System.exit(1); (1", "1; } } class X { static { System.exit(1); } } class Y { void m() { __x = 1",
                     "new int[1000000000]", "new ArrayList<>(1000000000)",  # sizes: memory, not literals
                     "new ArrayList<>() {{ System.exit(1); }}", "new HashMap<>(Map.of()) {}", "List.of(1).getClass()",
                     '"\\{x}"', "!true", "~1", "--1", "1 // c", "/* c */ 1", "new int[]{1, /* c */ 2}",
                     "Integer.MAX_VALUE + 1", "new Object()", 'Integer.parseInt("1")', "new int[]{f()}", "{1, 2}",
                     "new ArrayList<>(new ArrayList<>())", "List.<Integer>of()", "java.util.List.of(1)", "this",
                     "true ? 1 : 2", "Math.PI", "new ArrayList<@A Integer>()", "1\n"):
            self.assertFalse(JAVA.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_values_follow_the_declared_type(self):
        ints = dict(JAVA.edge_values("int", "n"))
        self.assertEqual(list(ints)[:4], ["3", "0", "1", "-1"])
        self.assertEqual(ints["Integer.MAX_VALUE"], "n may be as large as Integer.MAX_VALUE")
        self.assertNotIn("null", ints)  # a primitive can't be null
        self.assertEqual(dict(JAVA.edge_values("String", "s"))["null"], "s may be null")
        self.assertIsNone(dict(JAVA.edge_values("@Nullable String", "s"))["null"])  # the annotation allows it
        self.assertEqual(JAVA.edge_values("Long", "id")[0], ("3L", None))  # Long id = 3 wouldn't compile
        self.assertIn(("new int[]{}", None), JAVA.edge_values("int[]", "values"))
        self.assertEqual(JAVA.edge_values("List<Integer>", "xs")[:2],
                         [("new ArrayList<>(List.of(1, 2, 3))", None), ("new ArrayList<>()", None)])
        self.assertEqual(JAVA.edge_values("Map<String, Integer>", "m")[0],
                         ('new HashMap<>(Map.of("abc", 1, "de", 2))', None))
        for unknown in ("Object", "Point", "List<? extends Number>", "List<Short>", "Set<List<Integer>>"):
            self.assertEqual(JAVA.edge_values(unknown, "x"), [], unknown)

    def test_index_and_count_parameters_follow_their_collection(self):
        source = ("class S {\n  static int get(int[] values, int i) { return 0; }\n"
                  "  static int sum(int[] values, int count) { return 0; }\n"
                  "  static int dot(int[] a, int[] b) { return 0; }\n}\n")
        get, total, dot = JAVA.functions(source)
        inputs = {tuple(i.args): i.assumptions for i in JAVA.edge_inputs(get)}
        self.assertEqual(inputs[("new int[]{1, 2, 3}", "0")], [])
        self.assertEqual(inputs[("new int[]{1, 2, 3}", "2")], [])
        self.assertEqual(inputs[("new int[]{1, 2, 3}", "3")], ["i may be 3, past the end of values"])
        self.assertEqual(inputs[("new int[]{}", "0")], ["i may be 0 when values is empty"])
        pairs = [tuple(i.args) for i in JAVA.edge_inputs(total)]
        self.assertIn(("new int[]{}", "0"), pairs)
        self.assertIn(("new int[]{0}", "1"), pairs)
        self.assertNotIn(("new int[]{0}", "3"), pairs)  # never a count past the end
        mixed = {tuple(i.args): i.assumptions for i in JAVA.edge_inputs(dot)}
        self.assertEqual(mixed[("new int[]{}", "new int[]{1, 2, 3}")], ["a and b may have different lengths"])

    def test_model_inputs(self):
        source = ("class S {\n  static int f(List<Integer> xs, int n) { return 0; }\n"
                  "  static int g(Object o) { return 0; }\n}\n")
        f, g = JAVA.functions(source)
        made = JAVA.model_input(f, ["List.of(1, 2)", "Integer.MAX_VALUE"], None)
        self.assertEqual(made.args, ["new ArrayList<>(List.of(1, 2))", "Integer.MAX_VALUE"])  # mutable copy
        self.assertEqual(made.assumptions, ["n may be as large as Integer.MAX_VALUE"])
        self.assertEqual(JAVA.model_input(f, ["Arrays.asList(1, null)", "-2147483648"], None).assumptions,
                         ["xs may contain null", "n may be as small as -2147483648"])
        self.assertEqual(JAVA.model_input(f, ["null", "1"], None).assumptions, ["xs may be null"])
        self.assertIsNone(JAVA.model_input(f, ["System.exit(1)", "1"], None))
        self.assertIsNone(JAVA.model_input(g, ["null"], None))  # Object: no edge values, no harness type


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if JAVA.sandbox() != "docker":
            self.skipTest("no Java sandbox image: docker pull eclipse-temurin:25-jdk")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)
        self.addCleanup(setattr, specter, "cache", specter.cache)
        specter.cache = specter.Cache()  # fresh runs, not results remembered from another test

    def test_buggy_sample(self):
        answer = specter.run((SAMPLES / "Buggy.java").read_text(), "java", use_model=False)
        found = bugs(answer)
        expected = {"average": ("ArithmeticException", 15), "pairwiseDiffs": ("ArrayIndexOutOfBoundsException", 21),
                    "initials": ("StringIndexOutOfBoundsException", 29), "parsePort": ("NumberFormatException", 36),
                    "stockLeft": ("NullPointerException", 40), "spread": ("NoSuchElementException", 45),
                    "collatzSteps": ("Timeout", 50), "factorial": ("StackOverflowError", 61),
                    "percent": ("ArithmeticException", 65)}  # private: called through reflection
        for name, (error, line) in expected.items():
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[f"Buggy.{name}"])
        self.assertNotIn("Buggy.isLeapYear", found)  # a wrong answer: needs an intent check
        statuses = {f["function"]: f for f in answer["findings"] if f["status"] != "bug"}
        self.assertEqual(statuses["Buggy.main"]["status"], "skipped")
        hang = next(f for f in answer["findings"] if f["function"] == "Buggy.collatzSteps")
        self.assertEqual(hang["failure"]["kind"], "timeout")
        self.assertEqual(set(found["Buggy.average"]) - {("ArithmeticException", 15, "error")},
                         {("NullPointerException", 12, "info")})  # null only as an assumption

    def test_correct_sample_has_no_findings(self):
        answer = specter.run((SAMPLES / "Correct.java").read_text(), "java", use_model=False)
        self.assertEqual([f["status"] for f in answer["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "Buggy.java").read_text()
        result = specter.replay(source, "java", "Buggy.average", {"exprs": ["new int[]{}"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "ArithmeticException", 15))
        self.assertEqual(specter.replay(source, "java", "Buggy.average", {"exprs": ["new int[]{2, 4}"]}),
                         {"ok": True, "result": "3"})
        broken = specter.replay(source, "java", "Buggy.spread", {"exprs": ["List.of(null)"]})
        self.assertEqual(broken["kind"], "bad_input")  # the input itself threw: not the method's bug

    def test_model_inputs_find_what_edges_miss_and_bad_ones_are_dropped(self):
        source = ("public class Orders {\n"
                  "    public static int quantity(String text) {\n"
                  "        if (text.isBlank() || !Character.isDigit(text.charAt(0))) {\n"
                  "            return 0;\n"
                  "        }\n"
                  "        return Integer.parseInt(text);\n"
                  "    }\n\n"
                  "    public static int perBox(int items, int boxes) {\n"
                  "        return items / boxes;\n"
                  "    }\n"
                  "}\n")
        hypotheses = lambda inputs: {"risk": "low", "risk_reason": "", "hypotheses": [  # noqa: E731
            {"kind": "crash", "line": 6, "bug": "overflow", "trigger_condition": "", "confidence": 0.8,
             "inputs": inputs}]}
        provider = llm.ScriptedProvider([
            {"match": "Function: Orders.quantity", "answer": hypotheses([['"99999999999"'], ["System.exit(1)"]])},
            {"match": "Function: Orders.perBox", "answer": hypotheses([['"ten"', "1"]])},  # doesn't type-check
        ])
        llm.set_provider(provider)
        answer = specter.run(source, "java")
        found = [f for f in answer["findings"] if f["status"] == "bug"]
        quantity = [f for f in found if f["function"] == "Orders.quantity"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["error"], f["failure"]["line"])
                          for f in quantity if f["severity"] == "error"],
                         [("model", 'Orders.quantity("99999999999")', "NumberFormatException", 6)])
        per_box = [(f["origin"], f["failure"]["error"]) for f in found if f["function"] == "Orders.perBox"]
        self.assertIn(("edge", "ArithmeticException"), per_box)  # the broken model input didn't hide it
        self.assertIn("<code-", provider.calls[0]["prompt"])


if __name__ == "__main__":
    unittest.main()
