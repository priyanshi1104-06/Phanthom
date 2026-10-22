"""C and C++ plugins: parsing, literal checking, inputs, crash reports, and Specter end to end with GCC."""
import unittest
from pathlib import Path

from runner import languages, llm, specter
from runner.languages.base import Input

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


    def test_a_function_named_main_is_never_run(self):
        self.assertEqual([f.name for f in C.functions("int main(void) { return 0; }\nint f(void) { return 1; }\n")],
                         ["f"])
        found = {f.name: f for f in CPP.functions("namespace app {\nint main(int x) { return x; }\n}\n")}
        self.assertIn("main", found["app::main"].skip)  # #define main renames it too: it can't be called


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
                     '"a" /* c */', '"a" // c', "1'000", "-x", "!0", "~0", "(int[]){x}", "(int[]){[0] = 1}",
                     "(int[1000000000]){1}", "(char *){0}", "(const char *[]){0}", "(const char *[]){NULL}",
                     "(struct p){1}", "(int[][2]){{1, 2}}", "({ abort(); 1; })", "a = 1", "__LINE__", "main",
                     '"a" PRId64', "1\n", "(int[]){(int)1}", "&x", "*p", "INT_MAX + 1", "(int[]){f()}",
                     "__builtin_trap()", '_Pragma("message(1)")', "(float[2][2]){0}", "12_km"):
            self.assertFalse(C.literal_ok(expr), expr)

    def test_finding_literal_ok_over_rejects_double_question_marks(self):
        # trigraphs are off under -std=gnu17 / gnu++20, so "??" is two plain characters (checked by GCC in
        # test_question_marks_and_prefixes_compile_as_plain_text)
        for lang in (C, CPP):
            for expr in ('"what??"', '"a??b"', '"??/"', '"x??="', "'?'"):
                self.assertTrue(lang.literal_ok(expr), expr)
        self.assertIn("gnu17", C.compiler)
        self.assertIn("gnu++20", CPP.compiler)
        self.assertNotIn("trigraphs", C.compiler + CPP.compiler)

    def test_finding_literal_ok_accepts_non_canonical_literal_forms(self):
        for lang in (C, CPP):
            for expr in ("08", "09", "019", 'L"a"', 'u8"a"', 'u"a"', 'U"a"', '"a" L"b"', "L'a'", "u8'a'", "'ab'",
                         "'é'", "1f", "10uu", "1.5u", "0x"):
                self.assertFalse(lang.literal_ok(expr), f"{lang.name}: {expr}")
            for expr in ("0", "0755", "0x1F", "0b101", "10ul", "10LLU", "1.5f", "1.5L", "1e5", "08.5", "1.", ".5",
                         "'a'", r"'\x41'", r"'\''", r'"\u00e9"'):
                self.assertTrue(lang.literal_ok(expr), f"{lang.name}: {expr}")
        fn = C.functions("int sum(const int *values, int count) { return 0; }")[0]
        self.assertEqual(C.model_input(fn, ["(int[]){1, 2}", "02"], None).assumptions, [])  # octal 02 is 2

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

    def test_every_edge_input_is_a_plain_literal(self):  # replay accepts only what literal_ok accepts
        c_source = ("int f(const char *s, char *w, const char **names, size_t count, char c, _Bool b, double d,"
                    " unsigned char u, long long q, ssize_t z) { return 0; }\n"
                    "int g(char *buf, int x, const double *xs, unsigned long len, bool *flags, int grid[9],"
                    " uint8_t *bytes, size_t nbytes, char **words, int n) { return 0; }\n")
        cpp_source = ("int h(const std::string &s, std::string_view v, std::vector<std::string> names,"
                      " const std::vector<long long> &q, std::vector<bool> &flags, std::vector<char> cs,"
                      " std::vector<std::size_t> sizes, const char *p, char *w, int &r, unsigned x) { return 0; }\n")
        for lang, source in ((C, c_source), (CPP, cpp_source)):
            for fn in lang.functions(source):
                self.assertIsNone(fn.skip, fn.name)
                for inp in lang.edge_inputs(fn):
                    for arg in inp.args:
                        self.assertTrue(lang.literal_ok(arg), f"{fn.name}: {arg}")

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

    def test_exit_is_never_a_finding_and_timeouts_are_timeouts(self):
        source = "#include <stdlib.h>\nint check(int n) {\n    if (n < 0) exit(2);\n    return n;\n}\n"
        fn = C.functions(source)[0]
        harness = C.program(source, fn, [Input(["-1"])])["files"]["phantom_rt.c"]
        self.assertIn('\\"kind\\": \\"exit\\"', harness)  # an atexit handler reports exit(2) as a deliberate stop
        self.assertEqual(C.classify(2, '\n@@PHANTOM {"ok": false, "kind": "exit", "error": "Exit"}\n', "")["kind"],
                         "exit")
        self.assertEqual(C.classify(3, "", "")["kind"], "exit")  # _exit(3) skips atexit: no verdict, no report
        self.assertEqual(C.classify(0, "", "")["kind"], "exit")  # _exit(0)
        self.assertEqual(C.classify(124, "", "")["kind"], "timeout")
        for err in ("==1==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000010\n",
                    "user.c:3:5: runtime error: division by zero\n", "prog: user.c:3: f: Assertion `x' failed.\n",
                    "terminate called after throwing an instance of 'int'\n"):
            self.assertEqual(CPP.classify(1, "", err)["kind"], "crash", err)  # sanitizers exit with status 1


class ConventionTest(unittest.TestCase):
    """Shared conventions: .entered, failing on purpose, exit, and showing a returned char *."""

    def test_the_harness_marks_entry_after_the_arguments_and_before_the_call(self):
        self.assertTrue(C.marks_entry and CPP.marks_entry)
        for lang, source in ((C, "int f(const int *a, int n) { return n; }\n"),
                             (CPP, "#include <string>\nint f(const std::string &s, int n) { return n; }\n")):
            fn = lang.functions(source)[0]
            files = lang.program(source, fn, [Input(["(int[]){1}" if lang is C else '"a"', "1"])])["files"]
            body = files[f"phantom_main.{lang.ext}"].split("static void phantom_input_0(void) {")[1]
            self.assertLess(body.index("phantom_a1 = 1;"), body.index("phantom_enter();"))
            self.assertLess(body.index("phantom_enter();"), body.index("f(phantom_a0, phantom_a1)"))
            self.assertIn('fopen(".entered", "w")', files[f"phantom_rt.{lang.ext}"])

    def test_failing_on_purpose_is_not_a_bug_but_a_crash_on_that_line_is(self):
        source = ("#include <assert.h>\n#include <stdlib.h>\n#include <string.h>\n"
                  "int f(const char *s, int n) {\n"
                  "    assert(n >= 0);\n"                                 # 5
                  "    if (n > 9) abort();\n"                             # 6
                  "    if (n == 7) throw std::invalid_argument(\"seven\");\n"  # 7
                  "    assert(strlen(s) < 100);\n"                        # 8
                  "    return n;\n}\n")
        crash = {"ok": False, "kind": "crash"}
        on_purpose = [{**crash, "error": "assertion failed", "message": "Assertion `n >= 0' failed.", "line": 5},
                      {**crash, "error": "abort", "message": "ABRT on unknown address", "line": 6},
                      {**crash, "error": "SIGABRT", "message": "", "line": 6},
                      {"ok": False, "kind": "exception", "error": "std::invalid_argument", "line": 7}]
        bugs_here = [{**crash, "error": "null pointer dereference", "message": "SEGV", "line": 8},  # strlen(NULL)
                     {**crash, "error": "null pointer dereference", "message": "SEGV", "line": 5},
                     {**crash, "error": "assertion failed", "message": "Assertion '__n < this->size()' failed",
                      "line": 8},  # libstdc++'s own check, reached from the line
                     {**crash, "error": "std::out_of_range", "message": "uncaught", "line": 7},  # noexcept: terminate
                     {**crash, "error": "abort", "message": "", "line": 9},
                     {**crash, "error": "abort", "message": "", "line": None}]
        for failure in on_purpose:
            self.assertTrue(CPP.deliberate(source, failure), failure)
        for failure in bugs_here:
            self.assertFalse(CPP.deliberate(source, failure), failure)
        self.assertFalse(C.deliberate("int f(int n) {\n    static_assert(1, \"x\"); return 1 / n;\n}\n",
                                      {**crash, "error": "assertion failed", "message": "Assertion `x' failed.",
                                       "line": 2}))

    def test_finding_display_time_over_read_of_a_returned_char_pointer(self):
        # a char * the function returns has no known length: showing it reads only up to the end of its
        # block, and a pointer the harness can't read at all is not the function's crash
        self.assertIn("__asan_address_is_poisoned", C.runtime)
        self.assertIn("phantom_cstr_len(v, 301, &cut)", CPP.harness_head)
        self.assertNotIn("strnlen(s, 301)", C.runtime)
        self.assertNotIn("v[n]) n++", CPP.harness_head)
        wild = ("AddressSanitizer:DEADLYSIGNAL\n==1==ERROR: AddressSanitizer: SEGV on unknown address 0x000000000001"
                " (pc 0x1 bp 0x2 sp 0x3 T0)\n==1==The signal is caused by a READ memory access.\n"
                "    #0 0x5 in phantom_cstr_len /tmp/w/phantom_rt.c:30\n"
                "    #1 0x5 in phantom_string /tmp/w/phantom_rt.c:80\n    #2 0x5 in phantom_input_0 "
                "/tmp/w/phantom_main.c:40\n    #3 0x5 in main /tmp/w/phantom_main.c:50\n"
                "SUMMARY: AddressSanitizer: SEGV /tmp/w/phantom_rt.c:30 in phantom_cstr_len\n")
        for lang in (C, CPP):
            self.assertNotIn(lang.classify(1, "", wild)["kind"], specter.FAILURES)
            in_cpp = wild.replace("phantom_cstr_len /tmp", "phantom_cstr_len(char const*, unsigned long, int*) /tmp")
            self.assertNotIn(lang.classify(1, "", in_cpp)["kind"], specter.FAILURES)
        # the function's own crash, and a dangling std::string_view shown by its known length, stay crashes
        own = wild.replace("#0 0x5 in phantom_cstr_len /tmp/w/phantom_rt.c:30", "#0 0x5 in echo /tmp/w/user.c:2")
        self.assertEqual((C.classify(1, "", own)["kind"], C.classify(1, "", own)["line"]), ("crash", 2))
        view = wild.replace("phantom_cstr_len", "phantom_quote")
        self.assertEqual(CPP.classify(1, "", view)["kind"], "crash")


class SandboxTest(unittest.TestCase):
    def setUp(self):
        if C.sandbox() != "docker":
            self.skipTest("no Docker image gcc:15: docker pull gcc:15")
        llm.set_provider(None)
        self.addCleanup(llm.set_provider, None)

    def test_c_samples(self):
        answer = specter.run((SAMPLES / "c" / "crashes.c").read_text(), "c", use_model=False)
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
        found = bugs(specter.run((SAMPLES / "cpp" / "crashes.cpp").read_text(), "cpp", use_model=False))
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
        source = (SAMPLES / "c" / "crashes.c").read_text()
        result = specter.replay(source, "c", "average", {"exprs": ["(int[]){}", "0"]})
        self.assertEqual((result["ok"], result["error"], result["line"]), (False, "division by zero", 10))
        self.assertEqual(specter.replay(source, "c", "average", {"exprs": ["(int[]){2, 4}", "2"]}),
                         {"ok": True, "result": "3"})
        source = (SAMPLES / "cpp" / "crashes.cpp").read_text()
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

    def test_finding_display_time_over_read_of_a_returned_char_pointer_reproduced(self):
        for lang in ("c", "cpp"):  # the review's repro: echo never reads s, so it can't fail
            scripted("echo", [["(char[]){'a','b','c'}"]], 1)
            answer = specter.run("char *echo(char *s) { return s; }\n", lang)
            self.assertEqual([f["status"] for f in answer["findings"]], ["no_bug_found"], lang)
        llm.set_provider(None)
        for lang in ("c", "cpp"):  # (char *)3 can't be shown at all: still not a crash of wild()
            answer = specter.run("char *wild(int n) {\n    return (char *)(long)n;\n}\n", lang, use_model=False)
            self.assertEqual([f["status"] for f in answer["findings"]], ["no_bug_found"], lang)

    def test_c_conventions_failing_on_purpose_exit_and_a_returned_buffer_without_nul(self):
        source = ("#include <assert.h>\n#include <stdlib.h>\n#include <string.h>\n#include <unistd.h>\n\n"
                  "char *echo(char *s) {\n    return s;\n}\n\n"
                  "int positive(int n) {\n    assert(n > 0);\n    return n;\n}\n\n"
                  "int must(int n) {\n    if (n < 0)\n        abort();\n    return n;\n}\n\n"
                  "int leave(int n) {\n    if (n < 0)\n        exit(2);\n    if (n == 0)\n        _exit(3);\n"
                  "    return n;\n}\n\n"
                  "size_t len_of(const char *s) {\n    assert(strlen(s) < 100);\n    return strlen(s);\n}\n")
        scripted("Function: echo (", [["(char[]){'a', 'b', 'c'}"]], 7)
        found = bugs(specter.run(source, "c"))
        self.assertEqual(list(found), ["len_of"])  # assert, abort, exit and _exit are deliberate; echo is fine
        self.assertEqual([(line, severity) for _, line, severity in found["len_of"]], [(30, "info")])  # strlen(NULL)
        self.assertEqual(specter.replay(source, "c", "echo", {"exprs": ["(char[]){'a', 'b', 'c'}"]}),
                         {"ok": True, "result": '"abc" (not NUL-terminated)'})

    def test_question_marks_and_prefixes_compile_as_plain_text(self):
        source = "#include <string.h>\nsize_t len_of(const char *s) {\n    return strlen(s);\n}\n"
        for lang in (C, CPP):  # no trigraphs: "??/" is three characters, not an escaped closing quote
            self.assertEqual(specter.replay(source, lang.name, "len_of", {"exprs": ['"??/"']}),
                             {"ok": True, "result": "3"})

    def test_cpp_conventions_throw_on_purpose_and_a_failing_static_initializer(self):
        source = ("#include <stdexcept>\n#include <vector>\n\nchar *echo(char *s) {\n    return s;\n}\n\n"
                  "int parse_digit(char c) {\n    if (c < '0' || c > '9')\n"
                  "        throw std::invalid_argument(\"not a digit\");\n    return c - '0';\n}\n\n"
                  "int fourth(const std::vector<int> &v) {\n    return v.at(3);\n}\n")
        scripted("Function: echo (", [["(char[]){'a', 'b', 'c'}"]], 5)
        found = bugs(specter.run(source, "cpp"))
        self.assertEqual(found, {"fourth": [("std::out_of_range", 15, "error")]})  # thrown by the library
        self.assertEqual(specter.replay(source, "cpp", "echo", {"exprs": ["(char[]){'a', 'b', 'c'}"]}),
                         {"ok": True, "result": '"abc" (not NUL-terminated)'})
        llm.set_provider(None)
        source = ("#include <string>\nstruct Config {\n    Config() { std::string s; (void)s.at(3); }\n};\n"
                  "static Config config;\n\nint id(int x) {\n    return x;\n}\n")
        answer = specter.run(source, "cpp", use_model=False)
        self.assertEqual([(f["status"], f["failure"]["kind"]) for f in answer["findings"] if f["function"] == "id"],
                         [("error", "not_entered")])  # it fails as the program starts, not in id


if __name__ == "__main__":
    unittest.main()
