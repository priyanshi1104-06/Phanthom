"""C and C++ plugins: parsing, literal checking, inputs, crash reports, and Specter end to end with GCC."""
import unittest
from pathlib import Path

from runner import languages, llm, specter

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
C = languages.get("c")
CPP = languages.get("cpp")


def bugs(answer):
    """{function: [(error, line, severity), ...]} of the reproduced failures."""
    found = {}
    for f in answer["findings"]:
        if f["status"] == "bug":
            found.setdefault(f["function"], []).append((f["failure"]["error"], f["failure"]["line"], f["severity"]))
    return found


def scripted(match, inputs, line):
    answer = {"risk": "low", "risk_reason": "", "hypotheses": [
        {"kind": "crash", "line": line, "bug": "suggested", "trigger_condition": "", "confidence": 0.9,
         "inputs": inputs[i:i + 3]} for i in range(0, len(inputs), 3)]}
    provider = llm.ScriptedProvider([{"match": match, "answer": answer}])
    llm.set_provider(provider)
    return provider


class ParsingTest(unittest.TestCase):
    def test_c_functions_parameters_and_skips(self):
        source = ("#include <stddef.h>\nstatic int sum(const int *values, int count) { return 0; }\n"
                  "char *dup(const char *s,\n          size_t n) { return 0; }\n"
                  "int main(void) { return 0; }\nint g(struct point p) { return 0; }\n"
                  "int cb(int (*fn)(int)) { return 0; }\nint var(int n, ...) { return n; }\n"
                  "void v(void) {}\nint four(const int v[4], char *argv[]) { return 0; }\nint proto(int x);\n")
        found = {f.name: f for f in C.functions(source)}
        self.assertEqual(list(found), ["sum", "dup", "g", "cb", "var", "v", "four"])  # no main, no prototype
        self.assertEqual([(p.name, p.type) for p in found["sum"].params], [("values", "const int *"), ("count", "int")])
        self.assertEqual((found["dup"].line, found["dup"].end_line, found["dup"].returns), (3, 4, "char *"))
        self.assertEqual([(p.name, p.type) for p in found["four"].params],
                         [("v", "const int [4]"), ("argv", "char *[]")])
        self.assertEqual(found["v"].params, [])
        for name in ("sum", "dup", "v", "four"):
            self.assertIsNone(found[name].skip, name)
        self.assertIn("struct point", found["g"].skip)
        self.assertIn("function pointer", found["cb"].skip)
        self.assertIn("variadic", found["var"].skip)

    def test_cpp_namespaces_templates_members_overloads(self):
        source = ("#include <string>\n#include <vector>\nnamespace geo {\ndouble area(double w, const std::string &s,"
                  " std::vector<int> &v) { return w; }\n}\ntemplate <typename T> T id(T x) { return x; }\n"
                  "struct S { int m(int a) { return a; } static int sm(int b); };\nint S::sm(int b) { return b; }\n"
                  "bool operator==(const S &a, const S &b) { return true; }\nauto tr(int x) -> long { return x; }\n"
                  "int over(int x) { return x; }\nint over(double x) { return 1; }\n"
                  "namespace { int anon(int x) { return x; } }\n")
        found = {}
        for f in CPP.functions(source):
            found.setdefault(f.name, f)
        self.assertEqual(list(found), ["geo::area", "id", "S::m", "S::sm", "operator==", "tr", "over", "anon"])
        self.assertEqual([(p.name, p.type) for p in found["geo::area"].params],
                         [("w", "double"), ("s", "const std::string &"), ("v", "std::vector<int> &")])
        self.assertEqual(found["tr"].returns, "long")
        for name in ("geo::area", "tr", "over", "anon"):
            self.assertIsNone(found[name].skip, name)
        self.assertIn("template", found["id"].skip)
        self.assertIn("member", found["S::m"].skip)
        self.assertIn("member", found["S::sm"].skip)
        self.assertIn("operator", found["operator=="].skip)
        overloads = [f for f in CPP.functions(source) if f.name == "over"]
        self.assertIsNone(overloads[0].skip)
        self.assertIn("overloaded", overloads[1].skip)


class LiteralTest(unittest.TestCase):
    def test_c_plain_literals_pass(self):
        for expr in ("1", "-1", "+2", "- 1", "0x1F", "1.5", "-1.5e3f", "10u", '"abc"', r'"a\n\"b"', '"a" "b"', "'a'",
                     r"'\0'", "true", "false", "NULL", "INT_MAX", "SIZE_MAX", "(int[]){1, 2, 3}", "(int[]){}",
                     '(char[]){"abc"}', '(const char *[]){"a", "b"}', '(char *[]){(char[]){"a"}}',
                     "(unsigned long long[]){1}", "(uint8_t[]){1, 255}", "(double[3]){1.5}", '(char[64]){""}',
                     "(1)", " 3 ", "(int){3}", "(_Bool[]){true}"):
            self.assertTrue(C.literal_ok(expr), expr)

    def test_c_anything_that_runs_code_or_fakes_memory_is_rejected(self):
        for expr in ("x", "f()", "1 + 1", "(int)3", "(char *)1", "sizeof(int)", "1, 2", "{1, 2}", 'system("id")',
                     '1); system("id"); (1', '1); } int evil(void) { return system("id"); } void h(void) { (1',
                     '"a" /* c */', '"a" // c', '"abc??/"', "1'000", "-x", "!0", "~0", "(int[]){x}", "(int[]){[0] = 1}",
                     "(int[1000000000]){1}", "(char *){0}", "(const char *[]){0}", "(const char *[]){NULL}",
                     "(struct p){1}", "(int[][2]){{1, 2}}", "({ abort(); 1; })", "a = 1", "__LINE__", "main",
                     '"a" PRId64', "1\n", "(int[]){(int)1}", "&x", "*p", "INT_MAX + 1", "(int[]){f()}",
                     "__builtin_trap()", '_Pragma("message(1)")', "(float[2][2]){0}", "12_km"):
            self.assertFalse(C.literal_ok(expr), expr)

    def test_cpp_literals(self):
        for expr in ("nullptr", "std::vector<int>{1, 2}", 'std::vector<std::string>{"a"}', 'std::string("abc")',
                     'std::string{"a"}', "{1, 2}", '{"a", "b"}', "{}", "(int[]){1}", "std::vector<long long>{}"):
            self.assertTrue(CPP.literal_ok(expr), expr)
        for expr in ("12_km", 'R"(x)"', "std::vector<int>(5)", "std::string(1000000000, 'a')", "std::vector<Evil>{1}",
                     "std::string(s)", "new int[3]", "[]{ return 1; }()", "std::vector<int>{f()}", "throw 1",
                     "static_cast<int>(1)", "std::vector<int*>{0}", "{.a = 1}", "std::vector<std::vector<int>>{}",
                     '::std::string("a")', "std::cout << 1", "typeid(int)", "std::exit(1)"):
            self.assertFalse(CPP.literal_ok(expr), expr)


class InputsTest(unittest.TestCase):
    def test_types_decide_the_values(self):
        values = dict(C.edge_values("int", "n"))
        self.assertEqual(list(values)[:4], ["3", "0", "-1", "1"])
        self.assertEqual(values["INT_MAX"], "n may be as large as INT_MAX")
        self.assertEqual(dict(C.edge_values("const char *", "s"))["NULL"], "s may be NULL")
        self.assertIn(('(char[]){""}', None), C.edge_values("char *", "s"))  # writable strings are char arrays
        self.assertEqual(dict(CPP.edge_values("const char *", "s"))["nullptr"], "s may be nullptr")
        self.assertIn(("std::vector<int>{}", None), CPP.edge_values("const std::vector<int> &", "v"))
        self.assertEqual(C.edge_values("struct point", "p"), [])

    def test_a_pointer_and_its_length_change_together(self):
        fn = C.functions("int sum(const int *values, int count) { return 0; }")[0]
        inputs = {tuple(i.args): i.assumptions for i in C.edge_inputs(fn)}
        self.assertEqual(list(inputs)[0], ("(int[]){1, 2, 3}", "3"))
        self.assertEqual(inputs[("(int[]){}", "0")], [])
        self.assertEqual(inputs[("(int[]){0}", "1")], [])
        self.assertEqual(inputs[("NULL", "3")], ["values may be NULL"])
        self.assertFalse([args for args in inputs if args[0].count(",") + 1 < int(args[1]) and args[0] != "NULL"])

    def test_indexes_and_unknown_lengths_carry_assumptions(self):
        fn = C.functions("int at(const int *a, int n, int i) { return a[i]; }")[0]
        inputs = {tuple(i.args): i.assumptions for i in C.edge_inputs(fn)}
        self.assertEqual(inputs[("(int[]){1, 2, 3}", "3", "2")], [])
        self.assertEqual(inputs[("(int[]){1, 2, 3}", "3", "3")], ["i may be outside a"])
        self.assertEqual(inputs[("(int[]){}", "0", "0")], ["i may be outside a"])
        fn = C.functions("int third(const int *a) { return a[2]; }\nvoid set(int *out) { *out = 1; }")
        self.assertEqual(C.edge_inputs(fn[0])[0].assumptions, ["a has at least 3 elements"])
        self.assertEqual([(i.args, i.assumptions) for i in C.edge_inputs(fn[1])][0], (["(int[]){0}"], []))

    def test_model_inputs_get_assumptions_their_values_need(self):
        fn = C.functions("int sum(const int *values, int count) { return 0; }")[0]
        self.assertEqual(C.model_input(fn, ["(int[]){1, 2}", "2"], None).assumptions, [])
        self.assertEqual(C.model_input(fn, ["(int[]){1}", "3"], None).assumptions,
                         ["count may be larger than the length of values"])
        self.assertEqual(C.model_input(fn, ["0", "3"], None).assumptions, ["values may be NULL"])
        self.assertEqual(C.model_input(fn, ["(int[]){1}", "INT_MAX"], None).assumptions,
                         ["count may be as large as INT_MAX", "count may not match the length of values"])
        self.assertIsNone(C.model_input(fn, ["(int[]){1}", "f()"], None))
        fn = C.functions("int len(const char *s) { return 0; }")[0]
        self.assertEqual(C.model_input(fn, ["(char[]){'a', 'b'}"], None).assumptions, ["s may not be NUL-terminated"])
        fn = CPP.functions("int first(const std::vector<int> &v, int i) { return v[i]; }")[0]
        self.assertEqual(CPP.model_input(fn, ["{1, 2}", "2"], None).assumptions, ["i may be outside v"])


class CrashReportTest(unittest.TestCase):
    def test_sanitizer_reports_name_the_error(self):
        cases = [
            ("==1==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x6 at pc 0x4 bp 0x7 sp 0x7\n"
             "READ of size 4 at 0x6 thread T0\n    #0 0x4 in sum /tmp/w/user.c:5\n"
             "SUMMARY: AddressSanitizer: heap-buffer-overflow /tmp/w/user.c:5 in sum\n", "heap-buffer-overflow", 5),
            ("user.c:8:34: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'\n"
             "    #0 0x4 in add /tmp/w/user.c:8\n", "signed integer overflow", 8),
            ("user.c:9:5: runtime error: load of null pointer of type 'const int'\n", "null pointer dereference", 9),
            ("==1==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000000 (pc 0x1 bp 0x2 sp 0x3 T0)\n"
             "Hint: address points to the zero page.\n    #0 0x1 in f /tmp/w/user.c:3\n"
             "SUMMARY: AddressSanitizer: SEGV /tmp/w/user.c:3 in f\n", "null pointer dereference", 3),
            ("==1==ERROR: AddressSanitizer: stack-overflow on address 0x7\n    #0 0x1 in f /tmp/w/user.c:2\n"
             "    #1 0x1 in f /tmp/w/user.c:4\n    #2 0x1 in f /tmp/w/user.c:4\n"
             "SUMMARY: AddressSanitizer: stack-overflow /tmp/w/user.c:2 in f\n", "stack-overflow", 4),
            ("/usr/local/include/c++/15.3.0/bits/stl_vector.h:1282: constexpr std::vector<_Tp, _Alloc>::reference "
             "std::vector<_Tp, _Alloc>::operator[](size_type) [with _Tp = int]: Assertion '__n < this->size()' failed.\n"
             "==1==ERROR: AddressSanitizer: ABRT on unknown address 0x1\n    #9 0x1 in at(std::vector<int>&, int) "
             "/tmp/w/user.cpp:7\nSUMMARY: AddressSanitizer: ABRT\n", "index out of range", 7),
            ("prog: user.c:12: check: Assertion `n > 0' failed.\n", "assertion failed", 12),
            ("terminate called after throwing an instance of 'std::runtime_error'\n  what():  boom\n",
             "std::runtime_error", None),
        ]
        for err, error, line in cases:
            with self.subTest(error=error):
                result = CPP.classify(1, "", err)
                self.assertEqual((result["kind"], result["error"], result["line"]), ("crash", error, line))

    def test_exit_is_a_result_and_timeouts_are_timeouts(self):
        self.assertEqual(C.classify(1, '\n@@PHANTOM {"ok": true, "result": "exit() was called"}\n', "")["ok"], True)
        self.assertEqual(C.classify(124, "", "")["kind"], "timeout")


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if C.sandbox() != "docker":
            self.skipTest("no Docker image gcc:15: docker pull gcc:15")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def test_c_samples(self):
        answer = specter.run((SAMPLES / "c" / "buggy.c").read_text(), "c", use_model=False)
        found = bugs(answer)
        expected = {"average": ("division by zero", 10, "error"),
                    "last_element": ("heap-buffer-overflow", 14, "error"),
                    "port_of": ("null pointer dereference", 19, "error"),
                    "last_char": ("heap-buffer-overflow", 24, "error"),
                    "collatz_steps": ("Timeout", 27, "error"),
                    "factorial": ("stack-overflow", 39, "error"),
                    "join_words": ("heap-buffer-overflow", 46, "error"),
                    "digit_value": ("index out of bounds", 52, "error"),
                    "midpoint": ("signed integer overflow", 56, "warning")}  # only with INT_MAX
        for name, expectation in expected.items():
            with self.subTest(function=name):
                self.assertIn(expectation, found[name])
        self.assertIn(("null pointer dereference", 9, "info"), found["average"])  # average(NULL, 3)
        self.assertNotIn("is_leap_year", found)  # a wrong answer: needs an intent check
        timeouts = [f for f in answer["findings"] if f["function"] == "collatz_steps" and f["status"] == "bug"]
        self.assertIn("timeout", [f["failure"]["kind"] for f in timeouts])
        correct = specter.run((SAMPLES / "c" / "correct.c").read_text(), "c", use_model=False)
        self.assertEqual([f["status"] for f in correct["findings"]], ["no_bug_found"] * 4)

    def test_cpp_samples(self):
        found = bugs(specter.run((SAMPLES / "cpp" / "buggy.cpp").read_text(), "cpp", use_model=False))
        expected = {"average": ("division by zero", 12, "error"),
                    "top_score": ("null pointer dereference", 16, "error"),
                    "port_of": ("std::invalid_argument", 21, "error"),
                    "last_char": ("index out of range", 25, "error"),
                    "collatz_steps": ("Timeout", 28, "error"),
                    "factorial": ("stack-overflow", 39, "error"),
                    "price_of": ("std::out_of_range", 44, "error"),
                    "checksum": ("heap-buffer-overflow", 49, "error"),
                    "midpoint": ("signed integer overflow", 54, "warning")}
        for name, expectation in expected.items():
            with self.subTest(function=name):
                self.assertIn(expectation, found[name])
        self.assertNotIn("is_leap_year", found)
        correct = specter.run((SAMPLES / "cpp" / "correct.cpp").read_text(), "cpp", use_model=False)
        self.assertEqual([f["status"] for f in correct["findings"]], ["no_bug_found"] * 4)

    def test_replay(self):
        source = (SAMPLES / "c" / "buggy.c").read_text()
        result = specter.replay(source, "c", "average", {"exprs": ["(int[]){}", "0"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "division by zero", 10))
        self.assertEqual(specter.replay(source, "c", "average", {"exprs": ["(int[]){2, 4}", "2"]}),
                         {"ok": True, "result": "3"})
        source = (SAMPLES / "cpp" / "buggy.cpp").read_text()
        result = specter.replay(source, "cpp", "price_of", {"exprs": ['"kiwi"']})
        self.assertEqual((result["error"], result["message"], result["line"]), ("std::out_of_range", "map::at", 44))
        self.assertEqual(specter.replay(source, "cpp", "price_of", {"exprs": ['"pear"']}), {"ok": True, "result": "75"})

    def test_c_model_input_finds_what_edges_miss_and_injections_are_dropped(self):
        source = ("#include <ctype.h>\n#include <stddef.h>\n"
                  'static const char *const LEVELS[] = {"debug", "info", "warn", "error"};\n\n'
                  "const char *level_name(const char *code) {\n"
                  "    if (code == NULL || !isdigit((unsigned char)code[0]))\n        return \"unknown\";\n"
                  "    return LEVELS[code[0] - '0'];\n}\n")
        scripted("level_name", [['"9"'], ['system("id")'], ['"9"); abort(); ("'], ["(char *)1"], ["0"]], 8)
        found = [f for f in specter.run(source, "c")["findings"] if f["status"] == "bug"]
        self.assertEqual([(f["origin"], f["input"]["call"], f["failure"]["error"], f["failure"]["line"], f["severity"])
                          for f in found], [("model", 'level_name("9")', "index out of bounds", 8, "error")])

    def test_cpp_model_input_and_an_input_that_does_not_compile(self):
        source = ("#include <cctype>\n#include <string>\n#include <vector>\n\n"
                  "std::string level_name(const std::string &code) {\n"
                  '    static const std::vector<std::string> levels = {"debug", "info", "warn", "error"};\n'
                  '    if (code.empty() || !std::isdigit(static_cast<unsigned char>(code[0]))) return "unknown";\n'
                  "    return levels.at(code[0] - '0');\n}\n\n"
                  "int ratio(int part, int whole) {\n    return part * 100 / whole;\n}\n")
        provider = scripted("level_name", [['std::string("7")']], 8)
        provider.rules.append({"match": "ratio", "answer": {"risk": "low", "risk_reason": "", "hypotheses": [
            {"kind": "crash", "line": 12, "bug": "x", "trigger_condition": "", "confidence": 0.9,
             "inputs": [['"abc"', "{1, 2}"]]}]}})
        found = [f for f in specter.run(source, "cpp")["findings"] if f["status"] == "bug"]
        summary = {(f["function"], f["origin"], f["failure"]["error"], f["failure"]["line"]) for f in found}
        self.assertIn(("level_name", "model", "std::out_of_range", 8), summary)
        self.assertIn(("ratio", "edge", "division by zero", 12), summary)  # the bad model input didn't hide it


if __name__ == "__main__":
    unittest.main()
