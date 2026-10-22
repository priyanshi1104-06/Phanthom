"""Java plugin: parsing, literal checking, inputs, and Specter end to end in the Temurin JDK."""
import unittest
from pathlib import Path

from runner import languages, llm, specter
from runner.languages.base import Input
from runner.languages.java import bad_cases

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

    def test_java_25_syntax_is_not_half_typed(self):
        source = ("import module java.base;\npublic class A {\n    final int v;\n    A(int v) {\n"
                  "        if (v < 0) throw new IllegalArgumentException();\n        super();\n        this.v = v;\n"
                  "    }\n    public static int f(int a) {\n        return 10 / a;\n    }\n}\n")
        self.assertFalse(JAVA.has_errors(source))
        self.assertEqual([f.name for f in JAVA.functions(source)], ["A.A", "A.f"])
        self.assertTrue(JAVA.has_errors("class A { static int f( { }"))  # still half-typed

    def test_annotation_only_imports_from_outside_the_jdk_are_blanked(self):
        source = ("import org.jspecify.annotations.Nullable;\nimport com.acme.Money;\n\npublic class Names {\n"
                  "    public static int length(@Nullable String s, Money m) {\n        return 0;\n    }\n}\n")
        context = JAVA.context(source, None)
        self.assertEqual(context.count("\n"), source.count("\n"))
        self.assertNotIn("jspecify", context)
        self.assertNotIn("@Nullable", context)
        self.assertIn("import com.acme.Money;", context)  # used as a type: a real dependency, kept

    def test_jdk_names_the_file_redefines_are_written_in_full_in_the_harness(self):
        source = ("public class Map {\n    public static int tiles(java.util.Map<String, Integer> counts) {\n"
                  "        return 0;\n    }\n}\n")
        fn = JAVA.functions(source)[0]
        self.assertEqual(fn.shadowed, frozenset({"Map"}))
        self.assertEqual(JAVA._qualified('new HashMap<>(Map.of("a", List.of(1)))'),
                         'new java.util.HashMap<>(java.util.Map.of("a", java.util.List.of(1)))')
        harness = JAVA.program(source, fn, JAVA.edge_inputs(fn))["files"]["PhantomHarness.java"]
        self.assertIn("new java.util.HashMap<>(java.util.Map.of(", harness)

    def test_trace_line_skips_the_harness_and_the_jdk(self):
        trace = ("java.lang.NumberFormatException: For input string: \"\"\n"
                 "\tat java.base/java.lang.Integer.parseInt(Integer.java:672)\n"
                 "\tat Crashes.parsePort(Crashes.java:36)\n\tat PhantomHarness.call(PhantomHarness.java:30)\n")
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
                     '"\\u0022 + System.exit(1) + \\u0022"', "'\\u0027'",  # javac decodes these first
                     "1); System.exit(1); (1",
                     "1; } } class X { static { System.exit(1); } } class Y { void m() { __x = 1",
                     "new int[1000000000]", "new ArrayList<>(1000000000)",  # sizes: memory, not literals
                     "new ArrayList<>() {{ System.exit(1); }}", "new HashMap<>(Map.of()) {}", "List.of(1).getClass()",
                     '"\\{x}"', "!true", "~1", "--1", "1 // c", "/* c */ 1", "new int[]{1, /* c */ 2}",
                     "Integer.MAX_VALUE + 1", "new Object()", 'Integer.parseInt("1")', "new int[]{f()}", "{1, 2}",
                     "new ArrayList<>(new ArrayList<>())", "List.<Integer>of()", "java.util.List.of(1)", "this",
                     "true ? 1 : 2", "Math.PI", "new ArrayList<@A Integer>()", "1\n"):
            self.assertFalse(JAVA.literal_ok(expr), expr)

    def test_nesting_that_keeps_javac_busy_is_rejected(self):
        self.assertFalse(JAVA.literal_ok("Map.of(" * 20 + ")" * 20))  # javac: hours of overload resolution
        self.assertTrue(JAVA.literal_ok("List.of(" * 6 + ")" * 6))
        self.assertFalse(JAVA.literal_ok("List.of(" * 7 + ")" * 7))
        self.assertFalse(JAVA.literal_ok('Map.of("a")'))  # an odd number of arguments never compiles
        self.assertFalse(JAVA.literal_ok("Map.of(" + ", ".join(["1"] * 22) + ")"))  # more than 10 pairs

    def test_what_only_javac_would_stop_is_rejected(self):
        for expr in ("List.of.foo(1)", "Arrays.asList.toString()", "Map.of.of()", "'a\"+System.exit(1)+\"b'",
                     "'ab'", '""""""', '"\\x41"', '"\\q"', "2147483648", "1e400"):
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

    def test_inner_collections_are_mutable_too(self):
        rows = JAVA.edge_values("List<List<Integer>>", "rows")
        inner = "new ArrayList<>(List.of(1, 2, 3))"
        self.assertEqual(rows[0], (f"new ArrayList<>(List.of({inner}, {inner}, {inner}))", None))
        self.assertEqual(rows[2], ("new ArrayList<>(List.of(new ArrayList<>()))", None))
        scores = JAVA.edge_values("Map<String, List<Integer>>", "scores")[0][0]
        self.assertEqual(scores, 'new HashMap<>(Map.of("abc", new ArrayList<>(List.of(1, 2, 3)), '
                                 '"de", new ArrayList<>(List.of(1, 2, 3))))')
        source = ("class L {\n  static void f(List<List<Integer>> rows) {}\n"
                  "  static void g(Map<String, List<Integer>> m) {}\n}\n")
        f, g = JAVA.functions(source)
        self.assertEqual(JAVA.model_input(f, ["List.of(List.of(1))"], None).args,
                         ["new ArrayList<>(List.of(new ArrayList<>(List.of(1))))"])
        self.assertEqual(JAVA.model_input(g, ['Map.of("a", List.of(1))'], None).args,
                         ['new HashMap<>(Map.of("a", new ArrayList<>(List.of(1))))'])

    def test_a_start_and_a_count_on_the_same_value_stay_inside_it(self):
        source = ("public class Ranges {\n"
                  "    public static int sumRange(int[] values, int start, int count) { return 0; }\n"
                  "    public static String chunk(String text, int offset, int length) { return \"\"; }\n}\n")
        for fn in JAVA.functions(source):
            for inp in JAVA.edge_inputs(fn):
                value, begin, count = inp.args
                if value == "null":
                    continue
                size = len(value) - 2 if value.startswith('"') else 0 if value.endswith("{}") else value.count(",") + 1
                if int(begin) < 0 or int(begin) + int(count) > size:
                    self.assertTrue(inp.assumptions, (fn.name, inp.args))  # outside it: only as an assumption
        chunk = {tuple(i.args): i.assumptions for i in JAVA.edge_inputs(JAVA.functions(source)[1])}
        self.assertEqual(chunk[('" "', "0", "1")], [])  # the length follows the text
        self.assertNotIn(('" "', "0", "3"), chunk)

    def test_only_values_at_the_limits_of_the_parameter_type_are_extreme(self):
        source = "class C {\n  static int h(long epochMillis) { return 0; }\n  static int f(int x) { return 0; }\n}\n"
        h, f = JAVA.functions(source)
        for fn, arg, assumptions in ((h, "1700000000000L", []), (h, "Integer.MAX_VALUE", []),
                                     (h, "Long.MAX_VALUE", ["epochMillis may be as large as Long.MAX_VALUE"]),
                                     (f, "0xFFFFFFFF", []), (f, "0x7FFFFFFF", ["x may be as large as 0x7FFFFFFF"])):
            self.assertEqual(JAVA.model_input(fn, [arg], None).assumptions, assumptions, arg)

    def test_only_the_inputs_javac_rejects_are_dropped(self):
        source = "class S {\n  static int f(String s) { return 0; }\n}\n"
        fn = JAVA.functions(source)[0]
        inputs = [Input(['"a"']), Input(["1"]), Input(['"b"'])]
        harness = JAVA.program(source, fn, inputs)["files"]["PhantomHarness.java"]
        line = next(n for n, text in enumerate(harness.splitlines(), 1) if "String a0 = 1;" in text)
        message = f"PhantomHarness.java:{line}: error: incompatible types: int cannot be converted to String\n"
        self.assertEqual(bad_cases(harness, message), {1})
        self.assertIsNone(bad_cases(harness, "S.java:2: error: cannot find symbol\n"))  # the user's file

    def test_a_run_stopped_before_the_build_finished_is_not_a_crash(self):
        results = JAVA.parse_output("", 2)  # no @@BUILD line: javac was still busy
        self.assertEqual([r["kind"] for r in results], ["build_error", "build_error"])
        lost = JAVA.parse_output("@@BUILD 0\n", 1)  # built, but no result for the input
        self.assertEqual(lost[0]["kind"], "sandbox_error")
        self.assertNotIn("sandbox_error", specter.FAILURES)

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
        answer = specter.run((SAMPLES / "Crashes.java").read_text(), "java", use_model=False)
        found = bugs(answer)
        expected = {"average": ("ArithmeticException", 15), "pairwiseDiffs": ("ArrayIndexOutOfBoundsException", 21),
                    "initials": ("StringIndexOutOfBoundsException", 29), "parsePort": ("NumberFormatException", 36),
                    "stockLeft": ("NullPointerException", 40), "spread": ("NoSuchElementException", 45),
                    "collatzSteps": ("Timeout", 50), "factorial": ("StackOverflowError", 61),
                    "percent": ("ArithmeticException", 65)}  # private: called through reflection
        for name, (error, line) in expected.items():
            with self.subTest(function=name):
                self.assertIn((error, line, "error"), found[f"Crashes.{name}"])
        self.assertNotIn("Crashes.isLeapYear", found)  # a wrong answer: needs an intent check
        statuses = {f["function"]: f for f in answer["findings"] if f["status"] != "bug"}
        self.assertEqual(statuses["Crashes.main"]["status"], "skipped")
        hang = next(f for f in answer["findings"] if f["function"] == "Crashes.collatzSteps")
        self.assertEqual(hang["failure"]["kind"], "timeout")
        self.assertEqual(set(found["Crashes.average"]) - {("ArithmeticException", 15, "error")},
                         {("NullPointerException", 12, "info")})  # null only as an assumption

    def test_correct_sample_has_no_findings(self):
        answer = specter.run((SAMPLES / "Correct.java").read_text(), "java", use_model=False)
        self.assertEqual([f["status"] for f in answer["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "Crashes.java").read_text()
        result = specter.replay(source, "java", "Crashes.average", {"exprs": ["new int[]{}"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "ArithmeticException", 15))
        self.assertEqual(specter.replay(source, "java", "Crashes.average", {"exprs": ["new int[]{2, 4}"]}),
                         {"ok": True, "result": "3"})
        broken = specter.replay(source, "java", "Crashes.spread", {"exprs": ["List.of(null)"]})
        self.assertEqual(broken["kind"], "not_entered")  # the input itself threw: not the method's bug

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
            {"match": "Function: Orders.quantity",  # 1 is no String: only that input is dropped
             "answer": hypotheses([['"99999999999"'], ["System.exit(1)"], ["1"]])},
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

    def test_correct_code_that_guards_or_changes_its_arguments_has_no_errors(self):
        source = """import java.util.*;
public class Lib {
    public static int percent(int part, int whole) {
        if (whole == 0) {
            throw new IllegalArgumentException("whole must not be zero");
        }
        return part * 100 / whole;
    }
    public static int share(int total, int parts) {
        if (parts <= 0) throw new ArithmeticException("parts must be positive");
        return total / parts;
    }
    public static int length(String s) {
        Objects.requireNonNull(s, "s");
        return s.length();
    }
    public static void sortRows(List<List<Integer>> rows) {
        for (List<Integer> row : rows) {
            Collections.sort(row);
        }
    }
    public static int addScore(Map<String, List<Integer>> scores, String name, int score) {
        List<Integer> list = scores.computeIfAbsent(name, k -> new ArrayList<>());
        list.add(score);
        return list.size();
    }
    public static int sumRange(int[] values, int start, int count) {
        int total = 0;
        for (int k = start; k < start + count; k++) {
            total += values[k];
        }
        return total;
    }
    public static String chunk(String text, int offset, int length) {
        return text.substring(offset, offset + length);
    }
    public static int perHead(int total, int people) {
        return total / people;
    }
}
"""
        answer = specter.run(source, "java", use_model=False)
        errors = {(f["function"], f["failure"]["error"]) for f in answer["findings"]
                  if f["status"] == "bug" and f["severity"] == "error"}
        self.assertEqual(errors, {("Lib.perHead", "ArithmeticException")})  # the one real bug
        statuses = {f["function"]: f["status"] for f in answer["findings"]}
        for name in ("Lib.percent", "Lib.share", "Lib.length"):  # rejecting an input on purpose is no bug
            self.assertEqual(statuses[name], "no_bug_found", name)

    def test_files_javac_25_compiles_with_imports_it_cannot_resolve_or_jdk_names_still_run(self):
        sources = {
            "jspecify": ("import org.jspecify.annotations.Nullable;\n\npublic class Names {\n"
                         "    public static int length(@Nullable String s) {\n        return s == null ? 0 : s.length();\n"
                         "    }\n\n    public static int per(int total, int parts) {\n        return total / parts;\n"
                         "    }\n}\n", {"Names.per": [("ArithmeticException", 9, "error")]}),
            "java 25": ("import module java.base;\npublic class A {\n    final int v;\n    A(int v) {\n"
                        "        if (v < 0) throw new IllegalArgumentException();\n        super();\n"
                        "        this.v = v;\n    }\n    public static int f(int a) {\n"
                        "        List<Integer> xs = List.of(1);\n        return xs.get(0) * 10 / a;\n    }\n}\n",
                        {"A.f": [("ArithmeticException", 11, "error")]}),
            "class Map": ("public class Map {\n    public static int tiles(java.util.Map<String, Integer> counts) {\n"
                          "        return counts.size() / (counts.size() - 1);\n    }\n}\n",
                          {"Map.tiles": [("ArithmeticException", 3, "error"), ("NullPointerException", 3, "info")]}),
        }
        for label, (source, expected) in sources.items():
            with self.subTest(label):
                answer = specter.run(source, "java", use_model=False)
                self.assertTrue(answer["parsed"])
                self.assertEqual(bugs(answer), expected)
                self.assertNotIn("error", [f["status"] for f in answer["findings"]])

    def test_model_inputs_that_would_hang_javac_or_only_look_extreme(self):
        source = ("public class Flags {\n    public static int share(boolean on) {\n        int parts = on ? 4 : 0;\n"
                  "        return 100 / parts;\n    }\n\n"
                  "    private static final String[] NAMES = new String[24];\n\n"
                  "    public static String hourName(long epochMillis) {\n"
                  "        int hour = (int) epochMillis / 3_600_000 % 24;\n        return NAMES[hour];\n    }\n}\n")
        answer = lambda inputs: {"risk": "low", "risk_reason": "", "hypotheses": [  # noqa: E731
            {"kind": "crash", "line": 1, "bug": "x", "trigger_condition": "x", "confidence": 0.9, "inputs": inputs}]}
        llm.set_provider(llm.ScriptedProvider([
            {"match": "Function: Flags.share", "answer": answer([["Map.of(" * 20 + ")" * 20]])},
            {"match": "Function: Flags.hourName", "answer": answer([["1700000000000L"]])},
        ]))
        found = {(f["function"], f["origin"], f["input"]["call"], f["failure"]["error"], f["severity"])
                 for f in specter.run(source, "java")["findings"] if f["status"] == "bug"}
        self.assertEqual(found, {
            ("Flags.share", "edge", "Flags.share(false)", "ArithmeticException", "error"),
            ("Flags.hourName", "model", "Flags.hourName(1700000000000L)", "ArrayIndexOutOfBoundsException", "error"),
        })


if __name__ == "__main__":
    unittest.main()
