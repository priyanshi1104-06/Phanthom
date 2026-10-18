"""C and C++: top-level functions built by GCC 15 with AddressSanitizer and UBSan, one process per input.

The user's file is written unchanged as user.c / user.cpp and #included by a generated
phantom_main.c(pp): `#define main phantom_user_main` renames the user's own main on its line, then
one function per input and a main that runs input number argv[1]. The verdict printing and the
C++ throw hook live in a second translation unit (phantom_rt), so their #includes can't clash with
names in the user's code. A run that dies without a verdict is classified from the sanitizer report
(heap-buffer-overflow, signed integer overflow, null pointer dereference, stack-overflow...), at the
innermost user.c frame. C++ exceptions report their type, what() and the stack at the throw.

Inputs are literals. Arrays are compound literals, (int[]){1, 2, 3} or (char[]){"abc"}; the harness
copies every array argument to a heap block of exactly its size, so one element too far is caught
and string literals are writable. A pointer and its length parameter (values, count) change
together, so an input never claims more elements than it has. NULL, extreme integers (INT_MAX) and
arrays whose length nothing states carry assumptions. C++ adds std::string, std::vector<T> and
braced lists.
"""
import re

from runner.languages.base import MAX_INPUTS, RESULT_MARK, Function, Input, Language, Param

INTS = {  # canonical integer type -> (signed, MIN macro, MAX macro)
    "signed char": (True, "SCHAR_MIN", "SCHAR_MAX"), "short": (True, "SHRT_MIN", "SHRT_MAX"),
    "int": (True, "INT_MIN", "INT_MAX"), "long": (True, "LONG_MIN", "LONG_MAX"),
    "long long": (True, "LLONG_MIN", "LLONG_MAX"), "unsigned char": (False, None, "UCHAR_MAX"),
    "unsigned short": (False, None, "USHRT_MAX"), "unsigned int": (False, None, "UINT_MAX"),
    "unsigned long": (False, None, "ULONG_MAX"), "unsigned long long": (False, None, "ULLONG_MAX"),
    "size_t": (False, None, "SIZE_MAX"), "ssize_t": (True, None, "SSIZE_MAX"),
    "ptrdiff_t": (True, "PTRDIFF_MIN", "PTRDIFF_MAX"), "intptr_t": (True, "INTPTR_MIN", "INTPTR_MAX"),
    "uintptr_t": (False, None, "UINTPTR_MAX"), "intmax_t": (True, "INTMAX_MIN", "INTMAX_MAX"),
    "uintmax_t": (False, None, "UINTMAX_MAX"),
    **{f"int{b}_t": (True, f"INT{b}_MIN", f"INT{b}_MAX") for b in (8, 16, 32, 64)},
    **{f"uint{b}_t": (False, None, f"UINT{b}_MAX") for b in (8, 16, 32, 64)},
}
LIMITS = frozenset(m for _, low, high in INTS.values() for m in (low, high) if m)
FLOATS = ("float", "double", "long double")
QUALIFIERS = ("const", "volatile", "restrict", "__restrict", "register", "struct_none")
INT_WORDS = {"signed", "unsigned", "short", "long", "int", "char", "double"}
LENGTH_WORDS = {"n", "len", "length", "count", "cnt", "size", "sz", "num", "nelem", "nelems", "nmemb", "nitems",
                "cap", "capacity"}
STRING_LENGTH_WORDS = {"len", "length", "size", "sz", "cap", "capacity", "nbytes"}
INDEX_WORDS = {"i", "j", "idx", "index", "pos", "position", "offset", "at", "nth"}
OUT_WORDS = {"buf", "buffer", "out", "output", "dst", "dest", "result", "res"}
SANITIZER_ENV = {
    "ASAN_OPTIONS": "detect_leaks=0:handle_abort=1:abort_on_error=0:symbolize=1:color=never:print_summary=1",
    "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=1:symbolize=1:color=never",
}
NUMBER = re.compile(r"[+-]?(0[xX][0-9a-fA-F]+|0[bB][01]+|(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?)[uUlLfF]{0,3}")
UBSAN = (  # first matching phrase of a "runtime error:" line -> error name
    ("signed integer overflow", "signed integer overflow"), ("negation of", "signed integer overflow"),
    ("division of", "signed integer overflow"), ("division by zero", "division by zero"),
    ("null pointer passed as argument", "null pointer argument"),
    ("null pointer returned", "null pointer returned"), ("offset to null pointer", "null pointer arithmetic"),
    ("null pointer", "null pointer dereference"), ("out of bounds for type", "index out of bounds"),
    ("shift", "invalid shift"), ("misaligned address", "misaligned address"),
    ("pointer index expression", "pointer overflow"), ("unsigned offset", "pointer overflow"),
    ("end of a value-returning function", "missing return"), ("unreachable program point", "unreachable"),
    ("variable length array bound", "invalid array size"), ("is not a valid value for type", "invalid value"),
    ("outside the range of representable values", "float to integer overflow"),
)


class C(Language):
    name = "c"
    title = "C"
    vscode_ids = ("c",)
    extensions = (".c", ".h")
    grammar = "c"
    image = "gcc:15"
    local_tool = "gcc"
    memory = "768m"
    tmpfs = "256m"
    env = SANITIZER_ENV
    build_timeout = 60.0
    call_timeout = 2
    user_file = "user"
    ext = "c"
    cpp = False
    null_words = ("NULL",)
    null = "NULL"
    literal_names = LIMITS | {"INFINITY", "NAN", "FLT_MAX", "DBL_MAX", "FLT_MIN", "DBL_MIN"}
    literal_hint = ("numbers (also -1, 1.5, 0x1F), 'c' chars, \"strings\", true/false, NULL, limit macros "
                    "such as INT_MAX or SIZE_MAX, and arrays as compound literals: (int[]){1, 2, 3}, "
                    "(char[]){\"abc\"}, (const char *[]){\"a\", \"b\"}")
    compiler = ("gcc -std=gnu17 -g -O0 -fno-omit-frame-pointer -fsanitize=address,undefined "
                "-fno-sanitize-recover=all -w -Werror=incompatible-pointer-types -Werror=int-conversion "
                "-fdiagnostics-plain-output -fmax-errors=5")

    # ---------- functions ----------

    def functions(self, source):
        root = self.parse(source).root_node
        namespaces = {self.text(n) for n in _walk(root) if n.type == "namespace_identifier"}
        found = []
        self._collect(root, "", found, namespaces)
        seen = set()
        for fn in found:  # one name per function: later overloads aren't run
            if fn.name in seen and not fn.skip:
                fn.skip = f"overloaded: only the first {fn.name} is run"
            seen.add(fn.name)
        return found

    def _collect(self, holder, prefix, found, namespaces):
        for node in holder.named_children:
            if node.type == "function_definition":
                fn = self._function(node, prefix, namespaces)
                if fn is not None:
                    found.append(fn)
            elif node.type in ("preproc_ifdef", "preproc_if", "declaration_list"):  # include guards, extern "C"
                self._collect(node, prefix, found, namespaces)
            elif node.type == "linkage_specification" and node.child_by_field_name("body") is not None:
                self._collect(node.child_by_field_name("body"), prefix, found, namespaces)
            elif node.type == "namespace_definition" and node.child_by_field_name("body") is not None:
                name = node.child_by_field_name("name")
                self._collect(node.child_by_field_name("body"), prefix + (f"{self.text(name)}::" if name else ""),
                              found, namespaces)
            elif node.type == "template_declaration":
                inner = next((c for c in node.named_children if c.type == "function_definition"), None)
                fn = self._function(inner, prefix, namespaces) if inner is not None else None
                if fn is not None:
                    fn.skip, fn.line, fn.text = "templates are not run yet", node.start_point[0] + 1, self.text(node)
                    found.append(fn)
            elif node.type in ("class_specifier", "struct_specifier") and node.child_by_field_name("body"):
                cls = self.text(node.child_by_field_name("name")) or "<anonymous>"
                for member in node.child_by_field_name("body").named_children:
                    fn = self._function(member, f"{prefix}{cls}::", namespaces) \
                        if member.type == "function_definition" else None
                    if fn is not None:
                        fn.skip = "member functions are not run yet"
                        found.append(fn)

    def _function(self, node, prefix, namespaces):
        declarator, suffix = node.child_by_field_name("declarator"), ""
        while declarator is not None and declarator.type in ("pointer_declarator", "reference_declarator"):
            suffix += "*" if declarator.type == "pointer_declarator" else "&"
            declarator = declarator.child_by_field_name("declarator") or (declarator.named_children or [None])[-1]
        if declarator is None or declarator.type != "function_declarator" or node.child_by_field_name("body") is None \
                or declarator.child_by_field_name("parameters") is None:
            return None
        name_node = declarator.child_by_field_name("declarator")
        name, skip = self.text(name_node), None
        if name_node.type in ("identifier", "field_identifier"):
            if name == "main" and not prefix:
                return None  # the program's entry point: renamed away in the harness
            name = prefix + name
        elif name_node.type == "qualified_identifier" and name.rpartition("::")[0].split("::")[-1] in namespaces:
            name = prefix + name  # a namespace function defined outside its namespace block
        elif name_node.type == "operator_name":
            skip = "operators are not run yet"
        else:
            skip = "member functions are not run yet"
        returns = self._return_type(node, declarator, suffix)
        params = []
        for index, child in enumerate(declarator.child_by_field_name("parameters").named_children):
            if child.type == "variadic_parameter":
                skip = skip or "variadic functions are not run yet"
                continue
            param, problem = self._param(child, index)
            if param is None:
                skip = skip or problem
                continue
            if param.type == "void" and param.name == f"arg{index + 1}":
                continue  # f(void)
            if self._shape(param.type) is None:
                skip = skip or f"parameter {param.name} has type {param.type}, which Specter can't build yet"
            params.append(param)
        if any(c.type == "declaration" for c in node.named_children):
            skip = skip or "old-style (K&R) parameter declarations are not supported"
        if any(self.text(c) == "consteval" for c in node.children):
            skip = skip or "consteval functions only run at compile time"
        return Function(name=name, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, params=params,
                        returns=returns, text=self.text(node), skip=skip)

    def _return_type(self, node, declarator, suffix):
        trailing = next((c for c in declarator.named_children if c.type == "trailing_return_type"), None)
        if trailing is not None:
            return self.text(trailing).removeprefix("->").strip()
        quals = [self.text(c) for c in node.children if c.type == "type_qualifier" and self.text(c) != "constexpr"]
        base = " ".join(quals + [" ".join((self.text(node.child_by_field_name("type")) or "").split())])
        return f"{base} {suffix}" if suffix else base

    def _param(self, node, index):
        """(Param with a normalised type text such as "const char *", None) or (None, why not)."""
        if node.type not in ("parameter_declaration", "optional_parameter_declaration"):
            return None, f"parameter {self.text(node)!r} is not supported yet"
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return None, f"parameter {self.text(node)!r} is not supported yet"
        quals = [self.text(c) for c in node.children if c.type == "type_qualifier"]
        base = " ".join(quals + [" ".join(self.text(type_node).split())])
        stars, ref, name, decl = "", "", None, node.child_by_field_name("declarator")
        while decl is not None:
            if decl.type == "identifier":
                name = self.text(decl)
                break
            if decl.type in ("pointer_declarator", "abstract_pointer_declarator"):
                stars += "*" + "".join(self.text(c) for c in decl.children if c.type == "type_qualifier")
            elif decl.type in ("array_declarator", "abstract_array_declarator"):
                inner = decl.child_by_field_name("declarator")
                if inner is not None and inner.type in ("array_declarator", "abstract_array_declarator"):
                    return None, f"multi-dimensional array parameter {self.text(node)!r} is not supported yet"
                size = decl.child_by_field_name("size")
                stars += f"[{self.text(size)}]" if size is not None and size.type == "number_literal" else "[]"
            elif decl.type in ("reference_declarator", "abstract_reference_declarator"):
                ref = "&&" if any(c.type == "&&" for c in decl.children) else "&"
                decl = (decl.named_children or [None])[-1] if decl.child_by_field_name("declarator") is None \
                    else decl.child_by_field_name("declarator")
                continue
            else:
                return None, f"parameter {self.text(node)!r} (function pointer?) is not supported yet"
            decl = decl.child_by_field_name("declarator")
        text = base + (f" {stars}" if stars else "") + (f" {ref}" if ref else "")
        return Param(name or f"arg{index + 1}", text), None

    # ---------- types ----------

    def _shape(self, type_text):
        """What a declared type is, for building values: a dict with "kind", or None if unsupported.
        kinds: int, uint, char, bool, float, string (char *), array (T *), strings (char **),
        text (std::string), vector (std::vector<T>)."""
        if not type_text:
            return None
        words, stars, ref, const_base, size = split_type(type_text)
        base = canonical(words)
        if ref and stars:
            return None
        shape = {"base": " ".join(words), "ref": ref, "pointer": stars > 0, "const": const_base, "size": size}
        scalar = self._scalar(base)
        if stars == 0:
            if scalar:
                return {**shape, **scalar}
            if self.cpp and base.removeprefix("std::") in ("string", "string_view"):
                return {**shape, "kind": "text"}
            m = re.fullmatch(r"(?:std::)?vector<(.+)>", base) if self.cpp else None
            inner = self._shape(m.group(1)) if m else None
            if inner and inner["kind"] in ("int", "uint", "char", "bool", "float", "text") and not inner["ref"]:
                elem = "std::string" if inner["kind"] == "text" else inner["base"]
                return {**shape, "kind": "vector", "elem": inner, "elem_type": elem}
            return None
        if stars == 1 and base == "char":
            return {**shape, "kind": "string"}
        if stars == 1 and scalar:
            return {**shape, "kind": "array", "elem": scalar}
        if stars == 2 and base == "char":
            return {**shape, "kind": "strings"}
        return None

    def _scalar(self, base):
        plain = base.removeprefix("std::")
        if plain in ("_Bool", "bool"):
            return {"kind": "bool"}
        if plain == "char":
            return {"kind": "char"}
        if plain in FLOATS:
            return {"kind": "float"}
        if plain in INTS:
            signed, low, high = INTS[plain]
            return {"kind": "int" if signed else "uint", "min": low, "max": high}
        return None

    def nullable(self, type_text):
        return False  # a pointer can hold NULL, but no C type says it may: NULL carries an assumption

    # ---------- inputs ----------

    def edge_values(self, type_text, param_name):
        shape = self._shape(type_text)
        return [(v.expr, v.assumption) for v in self._values(shape, param_name)] if shape else []

    def _values(self, shape, name, use="array"):
        """Values for one parameter, typical first: Value(expr, assumption, size)."""
        kind, null = shape["kind"], Value(self.null, f"{name} may be {self.null}")
        if kind in ("int", "uint"):
            if name.lower() in INDEX_WORDS:
                return [Value(str(n), None, n) for n in ((0, 1, 2, -1, 3) if kind == "int" else (0, 1, 2, 3))]
            values = [Value(str(n), None, n) for n in ((3, 0, -1, 1) if kind == "int" else (3, 0, 1))]
            values.append(Value(shape["max"], f"{name} may be as large as {shape['max']}"))
            if shape.get("min"):
                values.append(Value(shape["min"], f"{name} may be as small as {shape['min']}"))
            return values
        if kind == "char":
            return [Value(v) for v in ("'a'", "'0'", "'\\0'", "' '")]
        if kind == "bool":
            return [Value("true"), Value("false")]
        if kind == "float":
            return [Value(v) for v in ("1.5", "0.0", "-1.0")]
        if kind in ("string", "text"):
            if kind == "string" and not shape["const"] and name.lower() in OUT_WORDS and use != "paired":
                return [Value('(char[64]){""}', f"{name} has room for 63 characters", 63), null]
            words = ["abc", "", " ", "a:b"]
            if kind == "string" and not shape["const"]:
                values = [Value(f'(char[]){{"{w}"}}', None, len(w)) for w in words]
            else:
                values = [Value(f'"{w}"', None, len(w)) for w in words]
            return values + ([null] if kind == "string" else [])
        if kind == "strings":
            items = (('"a"', '"bc"', '"def"'), (), ('""',))
            if shape["const"]:
                values = [Value(f"(const char *[]){{{', '.join(i)}}}", None, len(i)) for i in items]
            else:
                values = [Value(f"(char *[]){{{', '.join(f'(char[]){{{s}}}' for s in i)}}}", None, len(i))
                          for i in items]
            return values + [null]
        if kind == "array":
            elem, base = shape["elem"], shape["base"]
            items = element_items(elem["kind"])
            if shape["size"]:  # int a[4]: exactly that many
                n = shape["size"]
                typical = ", ".join((items[0].split(", ") * n)[:n])
                return [Value(f"({base}[]){{{typical}}}", None, n), null]
            values = [Value(f"({base}[]){{{i}}}", None, len([x for x in i.split(", ") if x])) for i in items]
            if use == "single":  # only ever *p: a pointer to one value is enough
                return [Value(f"({base}[]){{{items[2]}}}", None, 1), null]
            if use == "array":  # indexed, but nothing says how long it is
                return [Value(values[0].expr, f"{name} has at least 3 elements", 3), null]
            return values + [null]
        if kind == "vector":
            items = element_items(shape["elem"]["kind"])
            return [Value(f"std::vector<{shape['elem_type']}>{{{i}}}", None, len([x for x in i.split(", ") if x]))
                    for i in items]
        return []

    def _plan(self, fn):
        """Shapes, array -> length parameter pairs, and how each unpaired pointer is used."""
        shapes = [self._shape(p.type) for p in fn.params]
        names = [p.name for p in fn.params]
        lengths = [i for i, s in enumerate(shapes)
                   if s and s["kind"] in ("int", "uint") and not s["pointer"] and is_length(names[i])]
        pairs = {}
        for i, s in enumerate(shapes):
            if not s or s["kind"] not in ("array", "strings", "string") or s.get("size"):
                continue
            near = [j for j in (i + 1, i - 1) if j in lengths and j not in pairs.values()]
            if s["kind"] == "string":
                near = [j for j in near if is_length(names[j], STRING_LENGTH_WORDS)]
            if near:
                pairs[i] = near[0]
            elif s["kind"] != "string" and len(lengths) == 1:
                pairs[i] = lengths[0]
        uses = {}
        for i, s in enumerate(shapes):
            if s and s["kind"] == "array" and i not in pairs and not s["size"]:
                uses[i] = "array" if indexed(fn.text, names[i]) else "single"
        refs = [i for i, s in enumerate(shapes) if s and s["kind"] in ("array", "string", "strings", "text", "vector")]
        return shapes, pairs, uses, (refs[0] if refs else None)

    def edge_inputs(self, fn):
        shapes, pairs, uses, ref = self._plan(fn)
        if not fn.params:
            return [Input([])]
        if any(s is None for s in shapes):
            return []
        options = [self._values(s, p.name, "paired" if i in pairs else uses.get(i, "array"))
                   for i, (p, s) in enumerate(zip(fn.params, shapes))]
        groups = {}
        for array, length in pairs.items():
            groups.setdefault(length, []).append(array)
        base = [o[0] for o in options]
        for length, members in groups.items():
            base[length] = Value(str(min(base[a].size for a in members)), None, min(base[a].size for a in members))
        rows = [list(base)]
        for length, members in groups.items():  # a pointer and its length change together
            sized = {a: [v for v in options[a] if v.size is not None] for a in members}
            for k in range(1, max(len(v) for v in sized.values())):
                row = list(base)
                for a in members:
                    row[a] = sized[a][min(k, len(sized[a]) - 1)]
                n = min(row[a].size for a in members)
                row[length] = Value(str(n), None, n)
                rows.append(row)
        grouped = set(groups)
        for index, values in enumerate(options):
            for value in values[1:]:
                if index in grouped or (index in pairs and value.size is not None):
                    continue
                row = list(base)
                row[index] = value
                rows.append(row)
        inputs = []
        for row in rows:
            assumptions = [v.assumption for v in row if v.assumption]
            assumptions += self._index_assumptions(fn, shapes, ref, [v.size for v in row])
            inputs.append(Input([v.expr for v in row], assumptions=assumptions))
        unique = {tuple(i.args): i for i in inputs}
        return list(unique.values())[:MAX_INPUTS]

    def _index_assumptions(self, fn, shapes, ref, sizes):
        found = []
        for i, (p, s) in enumerate(zip(fn.params, shapes)):
            if ref is None or s["kind"] not in ("int", "uint") or p.name.lower() not in INDEX_WORDS:
                continue
            value, limit = sizes[i], sizes[ref]
            if value is None or limit is None:
                continue
            top = limit + 1 if shapes[ref]["kind"] in ("string", "text") else limit  # s[strlen(s)] is the NUL
            if not 0 <= value < top:
                found.append(f"{p.name} may be outside {fn.params[ref].name}")
        return found

    def model_input(self, fn, args, hypothesis):
        made = super().model_input(fn, args, hypothesis)
        if made is None:
            return None
        shapes, pairs, uses, ref = self._plan(fn)
        if any(s is None for s in shapes):
            return made
        facts = [self._facts(a) for a in made.args]
        notes = []
        for p, s, a, f in zip(fn.params, shapes, made.args, facts):
            if s["pointer"] and not f["array"]:
                if a not in self.null_words:
                    notes.append(f"{p.name} may be {self.null}")  # 0, {} and friends are null pointers too
            elif s["kind"] == "string" and fn.params.index(p) not in pairs and not f["terminated"]:
                notes.append(f"{p.name} may not be NUL-terminated")
            elif s["kind"] == "array" and uses.get(fn.params.index(p)) == "array":
                notes.append(f"{p.name} may be shorter than the function expects")
            elif s["size"] and f["array"] and (f["size"] or 0) < s["size"]:
                notes.append(f"{p.name} may have fewer than {s['size']} elements")
            if s["kind"] in ("int", "uint"):
                if a.lstrip("+-") in LIMITS:
                    notes.append(f"{p.name} may be {'as small as' if a.endswith('_MIN') else 'as large as'} {a}")
                elif f["value"] is not None and abs(f["value"]) >= 2 ** 30:
                    notes.append(f"{p.name} may be as large as {a}")
        for array, length in pairs.items():
            n, size = facts[length]["value"], facts[array]["size"]
            if not facts[array]["array"]:
                continue
            if n is None:
                notes.append(f"{fn.params[length].name} may not match the length of {fn.params[array].name}")
            elif n < 0:
                notes.append(f"{fn.params[length].name} may be negative")
            elif n > size + (1 if shapes[array]["kind"] == "string" else 0):
                notes.append(f"{fn.params[length].name} may be larger than the length of {fn.params[array].name}")
        notes += self._index_assumptions(fn, shapes, ref, [f["value"] if s["kind"] in ("int", "uint") else f["size"]
                                                           for s, f in zip(shapes, facts)])
        for note in notes:
            if note not in made.assumptions:
                made.assumptions.append(note)
        return made

    def _facts(self, expr):
        """What a literal argument is: array-like (and how many elements / characters), its integer value."""
        _, node = self.expression_tree(expr)
        facts = {"array": False, "size": None, "value": None, "terminated": False}
        while node is not None and node.type == "parenthesized_expression":
            node = node.named_children[0]
        if node is None:
            return facts
        if node.type in ("string_literal", "concatenated_string"):
            return {**facts, "array": True, "size": self._strlen(node), "terminated": True}
        if node.type == "number_literal":
            try:
                return {**facts, "value": int(re.sub(r"[uUlL]+$", "", self.text(node)), 0)}
            except ValueError:
                return facts
        if node.type == "char_literal":
            return {**facts, "value": None}
        if node.type == "unary_expression" and node.named_children[0].type == "number_literal":
            inner = self._facts(self.text(node.named_children[0]))
            return {**inner, "value": -inner["value"] if inner["value"] is not None and self.text(node)[0] == "-"
                    else inner["value"]}
        if node.type == "call_expression":  # std::string("abc")
            arg = node.child_by_field_name("arguments").named_children
            return {**facts, "size": self._strlen(arg[0]) if arg else 0, "terminated": True}
        value = node.child_by_field_name("value") if node.type == "compound_literal_expression" else node
        items = [c for c in value.named_children if c.type != "comment"] if value is not None else []
        array = node.type == "compound_literal_expression" and "[" in self.text(node.child_by_field_name("type"))
        if len(items) == 1 and items[0].type in ("string_literal", "concatenated_string") and (
                array or node.type == "compound_literal_expression" and "string" in self.text(node.child_by_field_name("type"))):
            return {**facts, "array": array, "size": self._strlen(items[0]), "terminated": True}
        size = len(items)
        declared = re.search(r"\[(\d+)\]", self.text(node.child_by_field_name("type")) or "") if array else None
        if declared:
            size = max(size, int(declared.group(1)))
        last = self.text(items[-1]) if items else ""
        terminated = bool(declared and int(declared.group(1)) > len(items)) or last in ("0", "'\\0'")
        return {**facts, "array": array, "size": size, "terminated": terminated}

    def _strlen(self, node):
        if node.type == "concatenated_string":
            return sum(self._strlen(c) for c in node.named_children)
        size = 0
        for child in node.named_children:
            size += 1 if child.type == "escape_sequence" else len(child.text)
        return size

    # ---------- literal checking ----------

    def expression_tree(self, expr):
        tree = self.parse(f"void phantom_f(void) {{ phantom_g({expr}); }}")
        try:
            body = tree.root_node.named_children[0].child_by_field_name("body")
            statement = body.named_children[0]
            arguments = statement.named_children[0].child_by_field_name("arguments")
        except (IndexError, AttributeError):
            return tree, None
        if statement.type != "expression_statement" or len(arguments.named_children) != 1:
            return tree, None  # a comment, or two arguments
        return tree, arguments.named_children[0]

    def literal_ok(self, expr):
        if "??" in (expr or ""):
            return False  # a trigraph (??/ is a backslash in strict ISO mode) would read differently to GCC
        return super().literal_ok(expr)

    def _literal_node(self, node, pointer_items=False):
        kind = node.type
        if kind == "number_literal":
            return bool(NUMBER.fullmatch(self.text(node)))
        if kind == "string_literal":
            return all(c.type in ("string_content", "escape_sequence") for c in node.named_children)
        if kind == "concatenated_string":
            return all(c.type == "string_literal" and self._literal_node(c) for c in node.named_children)
        if pointer_items:  # elements of (const char *[]){...}: strings only, never NULL or 0
            return kind == "compound_literal_expression" and self._compound(node, char_array_only=True)
        if kind == "char_literal":
            return all(c.type in ("character", "escape_sequence") for c in node.named_children)
        if kind in ("true", "false", "null"):
            return True
        if kind == "identifier":
            return self.text(node) in self.literal_names
        if kind == "unary_expression":
            sign, operand = node.children[0].type, node.child_by_field_name("argument")
            return sign in ("-", "+") and operand is not None and operand.type == "number_literal" and \
                self._literal_node(operand)
        if kind == "parenthesized_expression":
            return len(node.named_children) == 1 and self._literal_node(node.named_children[0])
        if kind == "compound_literal_expression":
            return self._compound(node)
        if kind == "initializer_list" and self.cpp:
            return self._items(node)
        if kind == "call_expression" and self.cpp:  # std::string("abc"): one string literal, nothing else
            args = node.child_by_field_name("arguments").named_children
            return self.text(node.child_by_field_name("function")) == "std::string" and len(args) == 1 and \
                args[0].type in ("string_literal", "concatenated_string") and self._literal_node(args[0])
        return False

    def _compound(self, node, char_array_only=False):
        """(T[]){...} / (T){...} of a plain type; C++ also std::vector<T>{...} and std::string{...}."""
        type_node, value = node.child_by_field_name("type"), node.child_by_field_name("value")
        if value is None or value.type != "initializer_list" or type_node is None:
            return False
        if type_node.type != "type_descriptor":
            if not self.cpp or char_array_only:
                return False
            text = " ".join(self.text(type_node).split())
            if text == "std::string":
                items = value.named_children
                return len(items) <= 1 and all(c.type == "string_literal" and self._literal_node(c) for c in items)
            m = re.fullmatch(r"std::vector<(.+)>", text)
            inner = self._shape(m.group(1)) if m else None
            return bool(inner and inner["kind"] in ("int", "uint", "char", "bool", "float", "text")
                        and not inner["pointer"] and not inner["ref"]) and self._items(value)
        base = [c for c in type_node.named_children if c.type in ("primitive_type", "sized_type_specifier")]
        others = [c for c in type_node.named_children if c.type not in ("primitive_type", "sized_type_specifier",
                                                                          "type_qualifier")]
        if len(base) != 1 or len(others) > 1 or not self._scalar(canonical(self.text(base[0]).split())):
            return False
        declarator = others[0] if others else None
        if declarator is None:
            return not char_array_only and len(value.named_children) == 1 and self._items(value)
        pointers = declarator.type == "abstract_pointer_declarator"
        array = declarator.named_children[0] if pointers and declarator.named_children else declarator
        if array.type != "abstract_array_declarator" or array.named_children and (
                len(array.named_children) != 1 or array.named_children[0].type != "number_literal"
                or not self.text(array.named_children[0]).isdigit() or int(self.text(array.named_children[0])) > 4096):
            return False  # T[] or T[N] with a small N; no T[][N], no T *[][N]
        if pointers:  # (const char *[]){"a", "b"}
            return not char_array_only and self.text(base[0]) == "char" and self._items(value, pointer_items=True)
        if char_array_only:
            return self.text(base[0]) == "char" and len(value.named_children) == 1 and \
                value.named_children[0].type in ("string_literal", "concatenated_string")
        return self._items(value)

    def _items(self, node, pointer_items=False):
        return all(c.type != "initializer_pair" and self._literal_node(c, pointer_items) for c in node.named_children)

    # ---------- running ----------

    def program(self, source, fn, inputs):
        shapes = [self._shape(p.type) for p in fn.params]
        bodies, cases = [], []
        for i, inp in enumerate(inputs):
            lines = []
            for k, (p, s, arg) in enumerate(zip(fn.params, shapes, inp.args)):
                lines += self._bind(k, p, s, arg)
            args = ", ".join(f"std::move(phantom_a{k})" if s["ref"] == "&&" else f"phantom_a{k}"
                             for k, s in enumerate(shapes))
            lines += self._show(fn, f"{self._callee(fn)}({args})")
            bodies.append(f"static void phantom_input_{i}(void) {{\n    " + "\n    ".join(lines) + "\n}")
            cases.append(f"    case {i}: phantom_input_{i}(); break;")
        main = self.harness_main.replace("CASES", "\n".join(cases))
        harness = (f'#define main phantom_user_main\n#include "user.{self.ext}"\n#undef main\n'
                   f"{self.harness_head}\n" + "\n".join(bodies) + f"\n{main}")
        files = {f"user.{self.ext}": source, f"phantom_main.{self.ext}": harness, f"phantom_rt.{self.ext}": self.runtime}
        return {"files": files, "build": f"{self.compiler} -o prog phantom_main.{self.ext} phantom_rt.{self.ext} -lm",
                "run": "./prog {i}"}

    def _callee(self, fn):
        return fn.name

    def _bind(self, k, param, shape, arg):
        """Declarations that turn input expression `arg` into local phantom_a<k> of the parameter's type."""
        decl = declare(param.type, f"phantom_a{k}")
        if shape["pointer"] and self._facts(arg)["array"]:  # type-check, then copy to an exact-size heap block
            return [f"{declare(param.type, f'phantom_t{k}')} = {arg};", f"(void)phantom_t{k};",
                    f"{decl} = phantom_copy(phantom_t{k}, sizeof({arg}));"]
        return [f"{decl} = {arg};"]

    def _show(self, fn, call):
        returns = (fn.returns or "int").strip()
        if returns == "void":
            return [f"{call};", "phantom_void();"]
        if "*" in returns:
            words, _, _, _, _ = split_type(returns)
            return [f"phantom_{'string' if canonical(words) == 'char' and returns.count('*') == 1 else 'pointer'}"
                    f"({call});"]
        return [f"__auto_type phantom_r = {call};", "PHANTOM_SHOW(phantom_r);"]

    harness_head = r"""#include <limits.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <float.h>
#ifndef INFINITY
#define INFINITY (__builtin_inff())
#endif
#ifndef NAN
#define NAN (__builtin_nanf(""))
#endif
void *phantom_copy(const void *data, size_t size);
int phantom_begin(int argc, char **argv);
void phantom_void(void);
void phantom_signed(long long value);
void phantom_unsigned(unsigned long long value);
void phantom_float(long double value);
void phantom_bool(int value);
void phantom_char(int value);
void phantom_string(const char *value);
void phantom_pointer(const void *value);
void phantom_value(const void *value);
static void phantom_bool_at(const void *p) { phantom_bool(*(const _Bool *)p); }
static void phantom_char_at(const void *p) { phantom_char(*(const char *)p); }
static void phantom_sc_at(const void *p) { phantom_signed(*(const signed char *)p); }
static void phantom_short_at(const void *p) { phantom_signed(*(const short *)p); }
static void phantom_int_at(const void *p) { phantom_signed(*(const int *)p); }
static void phantom_long_at(const void *p) { phantom_signed(*(const long *)p); }
static void phantom_ll_at(const void *p) { phantom_signed(*(const long long *)p); }
static void phantom_uc_at(const void *p) { phantom_unsigned(*(const unsigned char *)p); }
static void phantom_us_at(const void *p) { phantom_unsigned(*(const unsigned short *)p); }
static void phantom_ui_at(const void *p) { phantom_unsigned(*(const unsigned int *)p); }
static void phantom_ul_at(const void *p) { phantom_unsigned(*(const unsigned long *)p); }
static void phantom_ull_at(const void *p) { phantom_unsigned(*(const unsigned long long *)p); }
static void phantom_f_at(const void *p) { phantom_float(*(const float *)p); }
static void phantom_d_at(const void *p) { phantom_float(*(const double *)p); }
static void phantom_ld_at(const void *p) { phantom_float(*(const long double *)p); }
static void phantom_s_at(const void *p) { phantom_string(*(const char *const *)p); }
#define PHANTOM_SHOW(r) _Generic((r), _Bool: phantom_bool_at, char: phantom_char_at, \
    signed char: phantom_sc_at, short: phantom_short_at, int: phantom_int_at, long: phantom_long_at, \
    long long: phantom_ll_at, unsigned char: phantom_uc_at, unsigned short: phantom_us_at, \
    unsigned int: phantom_ui_at, unsigned long: phantom_ul_at, unsigned long long: phantom_ull_at, \
    float: phantom_f_at, double: phantom_d_at, long double: phantom_ld_at, char *: phantom_s_at, \
    const char *: phantom_s_at, default: phantom_value)(&(r))
"""
    harness_main = """int main(int argc, char **argv) {
    switch (phantom_begin(argc, argv)) {
CASES
    }
    return 0;
}
"""
    runtime = r"""#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int phantom_reported;
static void phantom_json(const char *text, size_t n) {
    putchar('"');
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c == '"' || c == '\\') printf("\\%c", c);
        else if (c < 0x20 || c >= 0x7f) printf("\\u%04x", c);
        else putchar(c);
    }
    putchar('"');
}
void phantom_result(const char *text, size_t n) {
    phantom_reported = 1;
    printf("\n@@PHANTOM {\"ok\": true, \"result\": ");
    phantom_json(text, n > 300 ? 300 : n);
    printf("}\n");
    fflush(stdout);
}
static void phantom_at_exit(void) {
    if (!phantom_reported) phantom_result("exit() was called", 17);  /* deliberate, not a crash */
}
int phantom_begin(int argc, char **argv) {
    atexit(phantom_at_exit);
    return argc > 1 ? atoi(argv[1]) : -1;
}
void *phantom_alloc(size_t size) { return malloc(size); }
void *phantom_copy(const void *data, size_t size) {
    void *copy = malloc(size);  /* exactly the array's size: one element too far is a heap-buffer-overflow */
    if (size) memcpy(copy, data, size);
    return copy;
}
void phantom_void(void) { phantom_result("void", 4); }
void phantom_signed(long long v) { char b[32]; phantom_result(b, (size_t)snprintf(b, sizeof b, "%lld", v)); }
void phantom_unsigned(unsigned long long v) { char b[32]; phantom_result(b, (size_t)snprintf(b, sizeof b, "%llu", v)); }
void phantom_float(long double v) { char b[64]; phantom_result(b, (size_t)snprintf(b, sizeof b, "%.15Lg", v)); }
void phantom_bool(int v) { phantom_result(v ? "true" : "false", v ? 4 : 5); }
static size_t phantom_escape(char *out, unsigned char c, char quote) {
    if (c == (unsigned char)quote || c == '\\') return (size_t)sprintf(out, "\\%c", c);
    if (c == '\n') return (size_t)sprintf(out, "\\n");
    if (c < 0x20 || c >= 0x7f) return (size_t)sprintf(out, "\\x%02x", c);
    out[0] = (char)c;
    return 1;
}
void phantom_char(int c) {
    char b[16]; size_t n = 0;
    b[n++] = '\'';
    n += phantom_escape(b + n, (unsigned char)c, '\'');
    b[n++] = '\'';
    phantom_result(b, n);
}
void phantom_quoted(const char *s, size_t len) {
    char b[1300]; size_t n = 0;
    b[n++] = '"';
    for (size_t i = 0; i < len && i < 300; i++) n += phantom_escape(b + n, (unsigned char)s[i], '"');
    if (len > 300) n += (size_t)sprintf(b + n, "...");
    b[n++] = '"';
    phantom_result(b, n);
}
void phantom_string(const char *s) {
    if (!s) phantom_result("NULL", 4);
    else phantom_quoted(s, strnlen(s, 301));
}
void phantom_pointer(const void *p) { if (p) phantom_result("(pointer)", 9); else phantom_result("NULL", 4); }
void phantom_value(const void *p) { (void)p; phantom_result("(value)", 7); }
"""

    # ---------- crashes ----------

    def crash_error(self, code, err):
        lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
        terminate = re.search(r"terminate called after throwing an instance of '(.+?)'\s*(?:what\(\):\s*(.*))?", err)
        if terminate:
            return terminate.group(1), (terminate.group(2) or "uncaught exception").strip()[:300]
        library = next((ln for ln in lines if "include/c++/" in ln and "Assertion '" in ln), None)
        if library:  # _GLIBCXX_ASSERTIONS: vector/string operator[], front() on an empty container...
            check = re.search(r"Assertion '(.+?)' failed", library).group(1)
            where = re.search(r"(std::\w+)<[^()]*?>::(operator\[\]|\w+)\(", library)
            name = "index out of range" if "size()" in check else \
                "empty container access" if "empty()" in check else "assertion failed"
            return name, f"Assertion '{check}' failed" + (f" in {where.group(1)}::{where.group(2)}" if where else "")
        assertion = next((ln for ln in lines if re.search(r"Assertion `.*' failed", ln)), None)
        if assertion:
            return "assertion failed", assertion[assertion.find("Assertion"):][:300]
        runtime = next((ln for ln in lines if "runtime error: " in ln), None)
        if runtime:
            text = runtime.split("runtime error: ", 1)[1]
            name = next((n for phrase, n in UBSAN if phrase in text), text.split(":")[0][:60])
            return name, text[:300]
        summary = re.search(r"SUMMARY: AddressSanitizer: ([\w-]+)", err)
        report = re.search(r"ERROR: AddressSanitizer: (.*)", err)
        if summary or report:
            kind = summary.group(1) if summary else report.group(1).split()[0]
            detail = re.sub(r"\s*\(pc 0x\w+.*?\)|\s*at pc 0x\w+ bp 0x\w+ sp 0x\w+", "",
                            report.group(1) if report else kind)
            access = next((ln for ln in lines if re.match(r"(READ|WRITE) of size", ln)
                           or ln.startswith("The signal is caused")), "")
            message = f"{detail}; {access}" if access else detail
            if kind == "SEGV" and ("zero page" in err or re.search(r"unknown address 0x0{1,12}\b", err)):
                return "null pointer dereference", message[:300]
            return {"ABRT": "abort", "FPE": "arithmetic exception", "BUS": "bus error"}.get(kind, kind), message[:300]
        return super().crash_error(code, err)


class Cpp(C):
    name = "cpp"
    title = "C++"
    vscode_ids = ("cpp",)
    extensions = (".cpp", ".cc", ".cxx", ".hpp", ".hh")
    grammar = "cpp"
    local_tool = "g++"
    ext = "cpp"
    cpp = True
    null_words = ("nullptr", "NULL")
    null = "nullptr"
    literal_hint = ("numbers (also -1, 1.5, 0x1F), 'c' chars, \"strings\", true/false, nullptr, limit macros "
                    "such as INT_MAX, std::string(\"abc\"), std::vector<int>{1, 2, 3}, braced lists {1, 2, 3}, "
                    "and C arrays as (int[]){1, 2, 3}")
    compiler = ("g++ -std=gnu++20 -g -O0 -fno-omit-frame-pointer -fsanitize=address,undefined "
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
        return [f"if constexpr (std::is_void_v<decltype({call})>) {{ {call}; phantom_void(); }}",
                f"else {{ const auto &phantom_r = {call}; std::string phantom_s = phantom_repr(phantom_r); "
                "phantom_result(phantom_s.data(), phantom_s.size()); }"]

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
std::string phantom_float_text(long double value);
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
    else if constexpr (std::is_floating_point_v<U>) return phantom_float_text(v);
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
    else if constexpr (requires { v.first; v.second; }) return "(" + phantom_repr(v.first) + ", " + phantom_repr(v.second) + ")";
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
#include <string>
#include <typeinfo>
#include <execinfo.h>
#include <dlfcn.h>
#include <sanitizer/common_interface_defs.h>
extern "C" char *__cxa_demangle(const char *, char *, size_t *, int *);
extern "C" std::type_info *__cxa_current_exception_type();
static void *phantom_frames[64];
static int phantom_depth;
extern "C" void __cxa_throw(void *object, void *type, void (*destroy)(void *)) {
    /* remember where the exception was thrown; the harness reports it if nobody catches it */
    phantom_depth = backtrace(phantom_frames, 64);
    typedef void (*throw_fn)(void *, void *, void (*)(void *));
    static throw_fn real = (throw_fn)dlsym(RTLD_NEXT, "__cxa_throw");
    real(object, type, destroy);
    __builtin_unreachable();
}
std::string phantom_float_text(long double v) {
    char b[64];
    snprintf(b, sizeof b, "%.15Lg", v);
    return b;
}
void phantom_exception(const char *type, const char *what) {
    int status = 0;
    char *name = __cxa_demangle(type, 0, 0, &status);
    std::string trace;
    for (int k = 1; k < phantom_depth; k++) {
        char frame[1024];
        __sanitizer_symbolize_pc((char *)phantom_frames[k] - 1, "%f (%s:%l)", frame, sizeof frame);
        trace += std::string("    at ") + frame + "\n";
    }
    phantom_reported = 1;
    printf("\n@@PHANTOM {\"ok\": false, \"kind\": \"exception\", \"error\": ");
    const char *shown = status == 0 && name ? name : type;
    phantom_json(shown, strlen(shown));
    printf(", \"message\": ");
    phantom_json(what, strnlen(what, 1000));
    printf(", \"trace\": ");
    phantom_json(trace.data(), trace.size() > 4000 ? 4000 : trace.size());
    printf("}\n");
    fflush(stdout);
}
void phantom_unknown_exception(void) {
    std::type_info *type = __cxa_current_exception_type();
    phantom_exception(type ? type->name() : "unknown", "");
}
"""


class Value(tuple):
    """(expr, assumption, size): size is an array's element count, a string's length or an int's value."""

    def __new__(cls, expr, assumption=None, size=None):
        return super().__new__(cls, (expr, assumption, size))

    expr, assumption, size = (property(lambda self, i=i: self[i]) for i in range(3))


def split_type(text):
    """'const std::vector<int> &' -> (['std::vector<int>'], 0, '&', True, None): words without
    qualifiers, pointer depth (an array parameter int a[] is a pointer), reference, const pointee,
    declared array length (int a[4] -> 4)."""
    size = re.search(r"\[(\d+)\]", text)
    text = re.sub(r"\[\d*\]", "*", text)
    words, stars, ref, const_base, word, depth = [], 0, "", False, "", 0
    for ch in text + " ":
        if depth or ch.isalnum() or ch in "_:<":
            depth += (ch == "<") - (ch == ">")
            word += ch
            continue
        if word:
            if word == "const" and not stars:
                const_base = True
            if word not in QUALIFIERS:
                words.append(word)
            word = ""
        if ch == "*":
            stars += 1
        elif ch == "&":
            ref += "&"
    return words, stars, ref, const_base, int(size.group(1)) if size else None


def canonical(words):
    """['unsigned', 'long', 'int'] -> 'unsigned long'; other names unchanged."""
    if not words or set(words) - INT_WORDS:
        return " ".join(words)
    if "double" in words:
        return "long double" if "long" in words else "double"
    if "char" in words:
        return "unsigned char" if "unsigned" in words else "signed char" if "signed" in words else "char"
    size = "short" if "short" in words else "long long" if words.count("long") == 2 else \
        "long" if "long" in words else "int"
    return f"unsigned {size}" if "unsigned" in words else size


def declare(type_text, name):
    type_text = re.sub(r"\s*\[\d*\]", " *", type_text).replace("* *", "**")
    return f"{type_text}{'' if type_text.endswith('*') else ' '}{name}"


def element_items(kind):
    """Array contents, typical first: three elements, none, one zero-ish, one negative."""
    return {"int": ["1, 2, 3", "", "0", "-1"], "uint": ["1, 2, 3", "", "0"], "char": ["'a', 'b', 'c'", "", "'\\0'"],
            "bool": ["true, false, true", "", "false"], "float": ["1.5, 2.5, 3.5", "", "0.0", "-1.0"],
            "text": ['"a", "bc", "def"', "", '""']}[kind]


def is_length(name, words=LENGTH_WORDS):
    parts = [p.lower() for p in re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", name) if p]
    return name.lower() in words or (len(parts) > 1 and (parts[-1] in words or parts[0] in ("n", "num", "len")))


def indexed(text, name):
    """Is pointer parameter `name` used as an array (p[i], p + k, p++, passed on) rather than only as *p?"""
    n = re.escape(name)
    stripped = re.sub(rf"\*\s*{n}\b", "", text)
    return bool(re.search(rf"\b{n}\s*(\[|\+|-(?!>)|\+\+)|(\+|-)\s*{n}\b|[(,]\s*{n}\s*[,)]", stripped))


def _walk(node):
    yield node
    for child in node.named_children:
        yield from _walk(child)
