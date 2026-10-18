"""C++: the C plugin (c.py) with g++ -std=gnu++20 and _GLIBCXX_ASSERTIONS, so vector/string operator[]
out of range aborts with a message instead of reading garbage.

Functions at namespace scope are called by their qualified name (::geo::area, which also turns off
argument-dependent lookup); templates, member functions, operators and later overloads are listed
but skipped. Inputs add std::string("abc"), std::vector<T>{...}, braced lists and nullptr; reference
parameters get a local variable to bind to; C array literals for pointer parameters become
exact-size heap copies (phantom_arr). An exception that escapes reports its demangled type, what()
and the stack at the throw, captured by a __cxa_throw hook in the runtime translation unit.
"""
import re

from runner.languages.base import RESULT_MARK
from runner.languages.c import C, declare


class Cpp(C):
    name = "cpp"
    title = "C++"
    vscode_ids = ("cpp",)
    extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hh")
    grammar = "cpp"
    local_tool = "g++"
    memory = "768m"  # cc1plus on <bits/stdc++.h> with sanitizers peaks around 330 MB
    ext = "cpp"
    cpp = True
    literal_nodes = C.literal_nodes | {"initializer_list", "call_expression"}
    literal_calls = frozenset({"std::string"})
    null_words = ("nullptr", "NULL")
    null = "nullptr"
    literal_hint = ("numbers (also -1, 1.5, 0x1F), 'c' chars, \"strings\", true/false, nullptr, limit macros "
                    "such as INT_MAX, std::string(\"abc\"), std::vector<int>{1, 2, 3}, braced lists {1, 2, 3}, "
                    "and C arrays as (int[]){1, 2, 3}")
    compiler = ("g++ -std=gnu++20 -g1 -O0 -fno-omit-frame-pointer -fsanitize=address,undefined "
                "-fno-sanitize-recover=all -D_GLIBCXX_ASSERTIONS -w -fdiagnostics-plain-output -fmax-errors=5")

    def _callee(self, fn):
        return f"::{fn.name}"  # qualified: no argument-dependent lookup of std::size, std::count...

    def _bind(self, k, param, shape, arg):
        plain = re.sub(r"\s*&+$", "", param.type)  # T & / const T & / T &&: a local the call can bind to
        expr = self._cpp_array(arg) if shape["pointer"] else arg
        return [f"{declare(plain, f'phantom_a{k}')} = {expr};"]

    def _cpp_array(self, expr):
        """C array literals for pointer parameters become exact-size heap copies: phantom_arr<T>({...})."""
        _, node = self.expression_tree(expr)
        return self._rewrite(node) if node is not None else expr

    def _rewrite(self, node):
        if node.type in ("string_literal", "concatenated_string"):
            return f"phantom_arr(0, {self.text(node)})"
        if node.type == "compound_literal_expression" and node.child_by_field_name("type").type == "type_descriptor":
            type_text = self.text(node.child_by_field_name("type"))
            items = node.child_by_field_name("value").named_children
            declared = re.search(r"\[(\d*)\]", type_text)
            if declared is None:
                return self.text(node)
            n = declared.group(1) or "0"
            if len(items) == 1 and items[0].type in ("string_literal", "concatenated_string") and "*" not in type_text:
                return f"phantom_arr({n}, {self.text(items[0])})"
            element = re.sub(r"\[\d*\]", "", type_text).strip()
            return f"phantom_arr<{element}>({n}, {{{', '.join(self._rewrite(c) for c in items)}}})"
        return self.text(node)

    def _show(self, fn, call):
        return [f"phantom_call([&]() -> decltype(auto) {{ return {call}; }});"]

    harness_head = r"""#include <climits>
#include <cfloat>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>
#include <type_traits>
#include <typeinfo>
#include <exception>
#include <utility>
#include <initializer_list>
#include <new>
#ifndef INFINITY
#define INFINITY (__builtin_inff())
#endif
#ifndef NAN
#define NAN (__builtin_nanf(""))
#endif
void *phantom_copy(const void *data, std::size_t size);
void *phantom_alloc(std::size_t size);
int phantom_begin(int argc, char **argv);
void phantom_void(void);
void phantom_result(const char *text, std::size_t n);
std::size_t phantom_float_text(char *out, std::size_t size, long double value);
void phantom_exception(const char *type, const char *what);
void phantom_unknown_exception(void);
template <class T> static T *phantom_arr(std::size_t n, std::initializer_list<T> items) {
    n = n > items.size() ? n : items.size();  /* (int[4]){1}: 4 elements, the rest zero */
    T *copy = static_cast<T *>(phantom_alloc(sizeof(T) * n));
    std::size_t k = 0;
    for (const T &item : items) new (copy + k++) T(item);
    for (; k < n; k++) new (copy + k) T();
    return copy;
}
template <class T, std::size_t N> static std::remove_const_t<T> *phantom_arr(std::size_t n, const T (&items)[N]) {
    using U = std::remove_const_t<T>;
    U *copy = static_cast<U *>(phantom_alloc(sizeof(U) * (n > N ? n : N)));
    for (std::size_t k = 0; k < (n > N ? n : N); k++) copy[k] = k < N ? items[k] : U();
    return copy;
}
static std::string phantom_quote(const char *s, std::size_t n, char q) {
    static const char hex[] = "0123456789abcdef";
    std::string out(1, q);
    for (std::size_t i = 0; i < n && i < 300; i++) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (c == static_cast<unsigned char>(q) || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c == '\n') out += "\\n";
        else if (c < 0x20 || c >= 0x7f) { out += "\\x"; out += hex[c >> 4]; out += hex[c & 15]; }
        else out += static_cast<char>(c);
    }
    if (n > 300) out += "...";
    return out + q;
}
template <class T> static std::string phantom_repr(const T &v) {
    using U = std::remove_cv_t<T>;
    if constexpr (std::is_same_v<U, bool>) return v ? "true" : "false";
    else if constexpr (std::is_same_v<U, char>) return phantom_quote(&v, 1, '\'');
    else if constexpr (std::is_enum_v<U>) return std::to_string(static_cast<long long>(v));
    else if constexpr (std::is_integral_v<U>) return std::to_string(v);
    else if constexpr (std::is_floating_point_v<U>) {
        char out[64];
        return std::string(out, phantom_float_text(out, sizeof out, v));
    }
    else if constexpr (std::is_same_v<U, char *> || std::is_same_v<U, const char *>) {
        if (!v) return "nullptr";
        std::size_t n = 0;
        while (n < 301 && v[n]) n++;
        return phantom_quote(v, n, '"');
    }
    else if constexpr (std::is_pointer_v<U> || std::is_null_pointer_v<U>) return v ? "(pointer)" : "nullptr";
    else if constexpr (std::is_convertible_v<const U &, std::string_view>) {
        std::string_view s = v;
        return phantom_quote(s.data(), s.size(), '"');
    }
    else if constexpr (requires { v.first; v.second; })
        return "(" + phantom_repr(v.first) + ", " + phantom_repr(v.second) + ")";
    else if constexpr (requires { v.begin(); v.end(); }) {
        std::string out = "[";
        for (const auto &item : v) {
            if (out.size() > 1) out += ", ";
            if (out.size() > 300) { out += "..."; break; }
            out += phantom_repr(item);
        }
        return out + "]";
    }
    else return "(value)";
}
template <class F> static void phantom_call(F call) {
    if constexpr (std::is_void_v<decltype(call())>) {
        call();
        phantom_void();
    } else {
        const auto &result = call();
        std::string text = phantom_repr(result);
        phantom_result(text.data(), text.size());
    }
}
"""
    harness_main = """int main(int argc, char **argv) {
    try {
        switch (phantom_begin(argc, argv)) {
CASES
        }
    } catch (const std::exception &e) {
        phantom_exception(typeid(e).name(), e.what());
    } catch (const char *text) {
        phantom_exception(typeid(const char *).name(), text ? text : "");
    } catch (const std::string &text) {
        phantom_exception(typeid(std::string).name(), text.c_str());
    } catch (...) {
        phantom_unknown_exception();
    }
    return 0;
}
"""
    runtime = C.runtime + r"""
#include <typeinfo>
#include <execinfo.h>
#include <dlfcn.h>
#include <sanitizer/common_interface_defs.h>
extern "C" char *__cxa_demangle(const char *, char *, size_t *, int *);
extern "C" std::type_info *__cxa_current_exception_type();
static void *phantom_frames[64];
static int phantom_depth;
extern "C" void __cxa_throw(void *object, void *type, void (*destroy)(void *)) {
    /* remember where the exception was thrown: the harness reports it if nobody catches it */
    phantom_depth = backtrace(phantom_frames, 64);
    typedef void (*throw_fn)(void *, void *, void (*)(void *));
    static throw_fn real = (throw_fn)dlsym(RTLD_NEXT, "__cxa_throw");
    real(object, type, destroy);
    __builtin_unreachable();
}
size_t phantom_float_text(char *out, size_t size, long double v) {
    return (size_t)snprintf(out, size, "%.15Lg", v);
}
void phantom_exception(const char *type, const char *what) {
    int status = 0;
    char *name = __cxa_demangle(type, 0, 0, &status);
    static char trace[4096];
    size_t used = 0;
    for (int k = 1; k < phantom_depth && used < sizeof trace - 1100; k++) {
        char frame[1024];
        __sanitizer_symbolize_pc((char *)phantom_frames[k] - 1, "%f (%s:%l)", frame, sizeof frame);
        used += (size_t)snprintf(trace + used, sizeof trace - used, "    at %s\n", frame);
    }
    phantom_reported = 1;
    printf("\nRESULT_MARK{\"ok\": false, \"kind\": \"exception\", \"error\": ");
    const char *shown = status == 0 && name ? name : type;
    phantom_json(shown, strlen(shown));
    printf(", \"message\": ");
    phantom_json(what, strnlen(what, 1000));
    printf(", \"trace\": ");
    phantom_json(trace, used);
    printf("}\n");
    fflush(stdout);
}
void phantom_unknown_exception(void) {
    std::type_info *type = __cxa_current_exception_type();
    phantom_exception(type ? type->name() : "unknown", "");
}
""".replace("RESULT_MARK", RESULT_MARK)
