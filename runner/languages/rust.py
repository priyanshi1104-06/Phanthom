"""Rust: top-level functions (and associated functions of plain impl blocks) built by rustc, no cargo.

The user's file is written unchanged to user.rs, except that its `fn main` is renamed on the same
line and crate-level inner attributes (`#![...]`, `//!`) are blanked, since they are invalid inside
`include!`. main.rs holds the harness and ends with `include!("user.rs")`, so panic locations and
backtraces name user.rs with the user's own line numbers. It is built with overflow checks on.

Each input runs in a fresh process: the call happens inside catch_unwind on a thread with a 4 MiB
stack, a panic hook records the message, its location and a backtrace, and the panic message is
mapped to a stable error name ("index out of bounds", "attempt to add with overflow", ...). A stack
overflow aborts the whole process; it is reported as a crash at the function's recursive call.

Inputs are Rust literals: numbers, strings, chars, arrays, tuples, plus vec![...], Some/None,
String::from / .to_string(), HashMap::from / HashSet::from. Integer limits (i32::MAX) need an
assumption, and so does an index or count that doesn't fit the collection passed with it.
"""
import ast
import re

from runner.languages.base import RESULT_MARK, Function, Language, Param

SIGNED = ("i8", "i16", "i32", "i64", "i128", "isize")
UNSIGNED = ("u8", "u16", "u32", "u64", "u128", "usize")
FLOATS = ("f32", "f64")
NUMBERS = frozenset({"integer_literal", "float_literal"})
STRINGS = frozenset({"string_literal", "raw_string_literal"})
TEXTS = ['"abc"', '""', '" "', '"a:b"', '"é"']  # "é": byte slicing that isn't on a char boundary panics
ONE_ARG_CALLS = frozenset({"Some", "String::from", "HashMap::from", "HashSet::from",
                           "std::collections::HashMap::from", "std::collections::HashSet::from"})
NO_ARG_CALLS = frozenset({"String::new", "Vec::new", "HashMap::new", "HashSet::new"})
INDEX_WORDS = {"i", "j", "n", "k", "idx", "index", "pos", "position", "offset", "start", "from", "begin", "row",
               "col", "column", "nth"}  # must be < the length of the collection passed with it
COUNT_WORDS = {"count", "len", "length", "size", "num", "take", "skip", "limit", "width", "window", "chunk", "end",
               "to", "stop", "until"}  # must be <= that length
POSITION_WORDS = INDEX_WORDS - {"n", "k"} | {"end", "to", "stop", "until"}
START_WORDS, END_WORDS = {"start", "from", "begin", "lo", "low"}, {"end", "to", "stop", "until", "hi", "high"}
PANICS = [  # the standard library's panic messages -> stable error names (None: the matched text)
    (r"^index out of bounds|^(removal|insertion|swap_remove) index", "index out of bounds"),
    (r"^range (start|end) index .* out of range|^slice index starts at", "slice index out of range"),
    (r"^(start |end )?byte index \d+ is not a char boundary", "byte index is not a char boundary"),
    (r"^(start |end )?byte index \d+ is out of bounds|^begin <= end", "byte index out of bounds"),
    (r"^attempt to [a-z ]+ with overflow", None),
    (r"^attempt to divide by zero", None),
    (r"^attempt to calculate the remainder with a divisor of zero", None),
    (r"^called `(Option|Result)::\w+\(\)` on (a|an) `\w+` value", None),
    (r"^no entry found for key|^key not found", "no entry found for key"),
    (r"^assertion", "assertion failed"),
    (r"^internal error: entered unreachable code", "unreachable code"),
    (r"^not (yet )?implemented", None),
    (r"^capacity overflow", None),
]
SELF_CALL = "@@SELF-CALL "


def limits(type_name):
    bits = 64 if type_name.endswith("size") else int(type_name[1:])
    return (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if type_name in SIGNED else (0, 2 ** bits - 1)


def word(name):
    return re.split(r"_|(?<=[a-z])(?=[A-Z])", name)[-1].lower()


class Rust(Language):
    name = "rust"
    title = "Rust"
    vscode_ids = ("rust",)
    extensions = (".rs",)
    grammar = "rust"
    image = "rust:1-slim-bookworm"
    local_tool = "rustc"
    memory = "512m"
    tmpfs = "256m"
    user_file = "user"
    null_words = ("None",)
    literal_hint = ("Rust literals: 3, -1, 2.5, 255u8, \"text\" (for &str), 'c', true/false, (1, \"a\"), "
                    "[1, 2] or &[1, 2] (slices), vec![1, 2], &mut vec![1], String::from(\"a\") or \"a\".to_string(), "
                    "Some(1), None, HashMap::from([(\"k\", 1)]), HashSet::from([1]), i32::MAX-style constants")
    literal_nodes = frozenset({
        "integer_literal", "float_literal", "string_literal", "raw_string_literal", "string_content",
        "escape_sequence", "char_literal", "boolean_literal", "array_expression", "tuple_expression",
        "unit_expression", "parenthesized_expression", "reference_expression", "mutable_specifier", "arguments",
    })
    literal_calls = ONE_ARG_CALLS | NO_ARG_CALLS | {"vec", ".to_string", ".to_owned"}
    literal_names = frozenset(
        {"None", "char::MAX"} | {f"{t}::{c}" for t in SIGNED for c in ("MAX", "MIN")}
        | {f"{t}::MAX" for t in UNSIGNED}
        | {f"{t}::{c}" for t in FLOATS for c in ("MAX", "MIN", "NAN", "INFINITY", "NEG_INFINITY", "EPSILON")})

    # ---------- functions ----------

    def functions(self, source):
        found = []
        for node in self.parse(source).root_node.named_children:
            if node.type == "function_item":
                found.append(self._function(node))
            elif node.type == "impl_item" and node.child_by_field_name("trait") is None:
                owner, body = node.child_by_field_name("type"), node.child_by_field_name("body")
                generic = self._generic(node.child_by_field_name("type_parameters")) or (
                    owner.type == "generic_type" and any(c.type != "lifetime" for c in
                                                         owner.child_by_field_name("type_arguments").named_children))
                base = owner.child_by_field_name("type") if owner.type == "generic_type" else owner
                for item in body.named_children if body is not None else []:
                    if item.type == "function_item":
                        found.append(self._function(item, self.text(base), generic))
        return found

    def _function(self, node, owner=None, generic_owner=False):
        name = self.text(node.child_by_field_name("name"))
        params, skip = [], None
        for child in node.child_by_field_name("parameters").named_children:
            if child.type == "self_parameter":
                skip = skip or "methods that take self are not run yet"
            elif child.type == "variadic_parameter":
                skip = skip or "variadic functions are not run yet"
            elif child.type == "parameter":
                params.append(Param(self.text(child.child_by_field_name("pattern")),
                                    self.text(child.child_by_field_name("type"))))
        modifiers = next((self.text(c) for c in node.children if c.type == "function_modifiers"), "")
        attributes = []
        before = node.prev_named_sibling
        while before is not None and before.type in ("attribute_item", "line_comment", "block_comment"):
            attributes.append(self.text(before))
            before = before.prev_named_sibling
        if "async" in modifiers.split():
            skip = "async functions are not run yet"
        elif "unsafe" in modifiers.split():
            skip = "unsafe functions are not run yet"
        elif generic_owner:
            skip = "functions of generic types are not run yet"
        elif self._generic(node.child_by_field_name("type_parameters")) or any(
                "impl " in (p.type or "") or (p.type or "").startswith("impl") for p in params):
            skip = "generic functions are not run yet"
        elif any(re.match(r"#\[\s*test\b", a) for a in attributes):
            skip = "tests are not run"
        elif any(re.match(r"#\[\s*cfg\b", a) for a in attributes):
            skip = "conditionally compiled functions are not run"
        elif owner is None and name == "main":
            skip = "the program's entry point is not run"
        if skip is None:
            unbuilt = next((p for p in params if not self.edge_values(p.type, p.name)), None)
            if unbuilt is not None:
                skip = f"no values can be built for {unbuilt.name}: {unbuilt.type} yet"
        returns = node.child_by_field_name("return_type")
        return Function(name=f"{owner}::{name}" if owner else name, line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1, params=params, returns=self.text(returns),
                        text=self.text(node), skip=skip)

    @staticmethod
    def _generic(type_parameters):
        return type_parameters is not None and any(c.type != "lifetime_parameter"
                                                   for c in type_parameters.named_children)

    # ---------- literals ----------

    def expression_tree(self, expr):
        tree = self.parse(f"fn __phantom() {{ let _ = {expr}; }}")
        items = tree.root_node.named_children
        body = items[0].child_by_field_name("body") if items and items[0].type == "function_item" else None
        statement = body.named_children[0] if body is not None and body.named_children else None
        if statement is None or statement.type != "let_declaration":
            return tree, None
        return tree, statement.child_by_field_name("value")

    def _literal_node(self, node):
        kind = node.type
        if kind in ("identifier", "scoped_identifier"):
            return self.text(node) in self.literal_names
        if kind == "unary_expression":  # only a minus in front of a number: no !, *, -i32::MIN
            return self.text(node).startswith("-") and [c.type for c in node.named_children] in (
                ["integer_literal"], ["float_literal"])
        if kind == "type_cast_expression":  # 1 as u8, -1 as u32
            value, target = node.child_by_field_name("value"), node.child_by_field_name("type")
            return target is not None and target.type == "primitive_type" and value is not None and (
                value.type in NUMBERS or (value.type == "unary_expression" and self._literal_node(value)))
        if kind == "array_expression" and any(c.type == ";" for c in node.children):
            return False  # [x; n]: a repeat can ask for any amount of memory
        if kind == "call_expression":
            return self._literal_call(node)
        if kind == "macro_invocation":
            return self._vec_items(node) is not None
        if node.is_named and kind not in self.literal_nodes:
            return False
        return all(self._literal_node(child) for child in node.children)

    def _literal_call(self, node):
        callee, holder = node.child_by_field_name("function"), node.child_by_field_name("arguments")
        if callee is None or holder is None:
            return False
        args = holder.named_children
        if callee.type == "field_expression":  # "text".to_string()
            value, field = callee.child_by_field_name("value"), callee.child_by_field_name("field")
            return (value is not None and value.type in STRINGS and f".{self.text(field)}" in self.literal_calls
                    and not args and self._literal_node(value))
        if self.text(callee) in ONE_ARG_CALLS:
            return len(args) == 1 and self._literal_node(args[0])
        return self.text(callee) in NO_ARG_CALLS and not args

    def _vec_items(self, node):
        """vec![a, b]: its token tree parsed again as the array [a, b] and checked; None if not allowed."""
        macro = node.child_by_field_name("macro")
        tokens = next((c for c in node.named_children if c.type == "token_tree"), None)
        if macro is None or self.text(macro) != "vec" or tokens is None or not self.text(tokens).startswith("["):
            return None
        tree, array = self.expression_tree(self.text(tokens))
        if array is None or tree.root_node.has_error or array.type != "array_expression" \
                or self.text(array) != self.text(tokens) or not self._literal_node(array):
            return None
        return array

    # ---------- inputs ----------

    def type_node(self, type_text):
        tree = self.parse(f"type __T = {type_text};")
        item = tree.root_node.named_children[0] if tree.root_node.named_children else None
        node = item.child_by_field_name("type") if item is not None and item.type == "type_item" else None
        if tree.root_node.has_error or node is None or self.text(node) != type_text.strip():
            return None
        return node

    def edge_values(self, type_text, param_name):
        node = self.type_node(type_text) if type_text else None
        values = self._values(node, param_name) if node is not None else []
        return list(dict.fromkeys(values))

    def _values(self, node, name):
        """(expression, assumption) pairs for a type node: typical first, then boundary values."""
        kind, text = node.type, self.text(node)
        if kind == "primitive_type":
            if text in SIGNED or text in UNSIGNED:
                values = ["3", "0", "1"] + (["-1"] if text in SIGNED else [])
                if word(name) in INDEX_WORDS:  # an index: in range of a typical 3-element collection
                    values = ["1", "0", "3"] + values[3:]
                return [(v, None) for v in values] + [(f"{text}::MAX", f"{name} may be as large as {text}::MAX")] + (
                    [(f"{text}::MIN", f"{name} may be as small as {text}::MIN")] if text in SIGNED else [])
            if text in FLOATS:
                return [(v, None) for v in ("1.5", "0.0", "-1.0")] + [(f"{text}::NAN", f"{name} may be NaN")]
            return {"bool": [("true", None), ("false", None)], "char": [("'a'", None), ("' '", None), ("'é'", None)],
                    "str": []}.get(text, [])
        if kind == "unit_type":
            return [("()", None)]
        if kind in ("type_identifier", "scoped_type_identifier"):
            last = text.split("::")[-1].strip()
            return [(f"String::from({t})", None) for t in TEXTS] if last == "String" else []
        if kind == "reference_type":
            inner = node.child_by_field_name("type")
            mutable = any(c.type == "mutable_specifier" for c in node.children)
            prefix = "&mut " if mutable else "&"
            if inner.type == "primitive_type" and self.text(inner) == "str":
                return [] if mutable else [(t, None) for t in TEXTS]
            if inner.type == "array_type" and inner.child_by_field_name("length") is None:  # a slice
                return self._sequence(inner.child_by_field_name("element"), name, lambda items: f"{prefix}[{items}]")
            return [(prefix + v, a) for v, a in self._values(inner, name)]
        if kind == "array_type":
            length = node.child_by_field_name("length")
            if length is None or length.type != "integer_literal" or not self.text(length).isdigit() \
                    or int(self.text(length)) > 64:
                return []
            return self._sequence(node.child_by_field_name("element"), name, lambda items: f"[{items}]",
                                  int(self.text(length)))
        if kind == "tuple_type":
            parts = [self._values(c, name) for c in node.named_children]
            if not all(parts):
                return []
            tuple_text = lambda items: f"({', '.join(items)}{',' if len(items) == 1 else ''})"
            typical = [p[0][0] for p in parts]
            values = [(tuple_text(typical), None)]
            for index, part in enumerate(parts):
                for value, assumption in part[1:3]:
                    values.append((tuple_text(typical[:index] + [value] + typical[index + 1:]), assumption))
            return values
        if kind == "generic_type":
            head = self.text(node.child_by_field_name("type")).split("::")[-1].strip()
            args = [c for c in node.child_by_field_name("type_arguments").named_children if c.type != "lifetime"]
            if head == "Vec" and len(args) == 1:
                return self._sequence(args[0], name, lambda items: f"vec![{items}]")
            if head == "Option" and len(args) == 1:
                inner = self._values(args[0], name)
                return ([(f"Some({inner[0][0]})", None), ("None", None)]
                        + [(f"Some({v})", a) for v, a in inner[1:]]) if inner else []
            if head == "HashSet" and len(args) == 1:
                return self._sequence(args[0], name, lambda items: f"HashSet::from([{items}])")
            if head == "HashMap" and len(args) == 2:
                keys, vals = self._samples(args[0], name), self._samples(args[1], name)
                if not keys or not vals:
                    return []
                one = f"({self._boundary(args[0], name)}, {self._boundary(args[1], name)})"
                return [(f"HashMap::from([({keys[0]}, {vals[0]}), ({keys[1]}, {vals[1]})])", None),
                        ("HashMap::from([])", None), (f"HashMap::from([{one}])", None)]
        return []  # structs, enums, trait objects, Box, Rc, fn types...: not built yet

    def _samples(self, node, name):
        """Three ordinary elements for building a collection, or None when the type can't be built."""
        text = self.text(node)
        fixed = {"bool": ["true", "false", "true"], "char": ["'a'", "'b'", "'c'"], "&str": ['"abc"', '"de"', '"f"'],
                 "String": ['String::from("abc")', 'String::from("de")', 'String::from("f")']}
        if text in SIGNED or text in UNSIGNED:
            return ["1", "2", "3"]
        if text in FLOATS:
            return ["1.5", "2.5", "0.5"]
        if re.sub(r"'\w+\s+", "", text) in fixed:
            return fixed[re.sub(r"'\w+\s+", "", text)]
        values = self._values(node, name)
        return [values[0][0]] * 3 if values else None

    def _boundary(self, node, name):
        values = self._values(node, name)
        return values[1][0] if len(values) > 1 and values[1][1] is None else values[0][0]

    def _sequence(self, element, name, wrap, length=None):
        """A collection of `element`s: a few typical items, empty, and one boundary item."""
        samples = self._samples(element, name)
        if not samples:
            return []
        if length is not None:  # a fixed-size array: exactly `length` items
            typical = [samples[i % 3] for i in range(length)]
            return list(dict.fromkeys([(wrap(", ".join(typical)), None),
                                       (wrap(", ".join([self._boundary(element, name)] * length)), None)]))
        return [(wrap(", ".join(samples)), None), (wrap(""), None), (wrap(self._boundary(element, name)), None)]

    def nullable(self, type_text):
        return bool(type_text) and re.match(r"\s*(::)?(std::option::)?Option\s*<", type_text) is not None

    def edge_inputs(self, fn):
        inputs = super().edge_inputs(fn)
        for made in inputs:
            made.assumptions += [a for a in self._contract(fn, made.args) if a not in made.assumptions]
        return inputs

    def model_input(self, fn, args, hypothesis):
        made = super().model_input(fn, args, hypothesis)
        if made is not None:
            extra = self._contract(fn, args)
            for param, arg in zip(fn.params, args):
                kind = (param.type or "").strip()
                value = self._int_value(arg, kind)
                if value is not None:
                    low, high = limits(kind)
                    wide = kind not in ("i8", "u8", "i16", "u16")  # past half the range: overflow is near
                    if value >= high or value <= low < 0 or wide and (value > high // 2 or value < low // 2):
                        extra.append(f"{param.name} may be as {'large' if value > 0 else 'small'} as {arg}")
            made.assumptions += [a for a in extra if a not in made.assumptions]
        return made

    def _contract(self, fn, args):
        """Assumptions an input needs because its parameters don't fit together: an index past the end
        of the collection passed with it, a count larger than it, a start after the end. A string only
        pairs with position-like names: `n` next to a string is usually not a position in it."""
        lengths, numbers = {}, {}
        for param, arg in zip(fn.params, args):
            kind = re.sub(r"^&(mut\s+)?('\w+\s+)?", "", (param.type or "").strip())
            if kind in SIGNED or kind in UNSIGNED:
                numbers[param.name] = self._int_value(arg, kind)
            elif re.match(r"(Vec|HashMap|HashSet)\s*<|\[|str$|String$", kind):
                length = self._length(arg)
                if length is not None:
                    lengths[param.name] = (length, kind in ("str", "String"))
        notes = []
        for name, value in numbers.items():
            strict, loose = word(name) in INDEX_WORDS, word(name) in COUNT_WORDS
            sizes = {k: n for k, (n, text) in lengths.items() if not text or word(name) in POSITION_WORDS}
            if value is None or not sizes or not (strict or loose):
                continue
            shortest = min(sizes, key=sizes.get)
            if not 0 <= value < sizes[shortest] + (0 if strict else 1):
                notes.append(f"{name} may be out of range for {shortest}")
        starts = [n for n in numbers if word(n) in START_WORDS and numbers[n] is not None]
        ends = [n for n in numbers if word(n) in END_WORDS and numbers[n] is not None]
        if starts and ends and numbers[starts[0]] > numbers[ends[0]]:
            notes.append(f"{starts[0]} may be after {ends[0]}")
        return notes

    def _int_value(self, expr, kind):
        if kind not in SIGNED and kind not in UNSIGNED:
            return None
        text = expr.strip()
        if text.endswith(("::MAX", "::MIN")) and text.split("::")[0] in SIGNED + UNSIGNED:
            low, high = limits(text.split("::")[0])
            return high if text.endswith("MAX") else low
        match = re.fullmatch(r"(-?)\s*(0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[0-9][0-9_]*)([iu](8|16|32|64|128|size))?",
                             text)
        return int(match[1] + match[2].replace("_", ""), 0) if match else None

    def _length(self, expr):
        """Number of items in a collection literal (or chars in a string), or None."""
        try:
            tree, node = self.expression_tree(expr)
        except Exception:
            return None
        while node is not None and not tree.root_node.has_error:
            kind = node.type
            if kind in ("reference_expression", "parenthesized_expression"):
                node = node.child_by_field_name("value") or (node.named_children or [None])[-1]
            elif kind == "macro_invocation":
                node = self._vec_items(node)
            elif kind == "call_expression":
                callee = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments").named_children
                if callee.type == "field_expression":
                    node = callee.child_by_field_name("value")
                elif self.text(callee) in NO_ARG_CALLS:
                    return 0
                elif self.text(callee) in ONE_ARG_CALLS and len(args) == 1:
                    node = args[0]
                else:
                    return None
            elif kind == "array_expression":
                return len([c for c in node.named_children if "comment" not in c.type])
            elif kind in STRINGS:
                try:
                    return len(ast.literal_eval(self.text(node).lstrip("r#")))
                except (ValueError, SyntaxError):
                    return None
            else:
                return None
        return None

    # ---------- running ----------

    def user_source(self, source):
        """user.rs: the user's code with `fn main` renamed and crate-level inner attributes blanked."""
        data = bytearray(source.encode())
        rename = None
        for node in self.parse(source).root_node.children:
            inner_doc = node.type in ("line_comment", "block_comment") and node.child_by_field_name("inner") is not None
            if node.type in ("inner_attribute_item", "shebang") or inner_doc:
                for i in range(node.start_byte, node.end_byte):
                    if data[i] not in (10, 13):
                        data[i] = 32
            elif node.type == "function_item" and self.text(node.child_by_field_name("name")) == "main":
                rename = node.child_by_field_name("name")
        if rename is not None:
            data[rename.start_byte:rename.end_byte] = b"__phantom_user_main"
        return data.decode(errors="replace")

    def self_call_line(self, source, fn):
        """Line of the function's first call to itself: where a stack overflow is reported."""
        short = fn.name.split("::")[-1]
        names = {fn.name, short, f"Self::{short}"}
        stack = [self.parse(source).root_node]
        while stack:
            node = stack.pop()
            if node.end_point[0] + 1 < fn.line or node.start_point[0] + 1 > fn.end_line:
                continue
            if node.type == "call_expression" and self.text(node.child_by_field_name("function")) in names:
                return node.start_point[0] + 1
            stack.extend(reversed(node.children))
        return None

    def program(self, source, fn, inputs):
        arms = "\n".join(f"            {i} => (&&Show(&super::{fn.name}({', '.join(inp.args)}))).__phantom_show(),"
                         for i, inp in enumerate(inputs))
        line = self.self_call_line(source, fn)
        harness = HARNESS.replace("__ARMS__", arms).replace("__MARK__", RESULT_MARK).replace(
            "__SELF_CALL__", f'eprintln!("{SELF_CALL}{line}");' if line else "")
        return {"files": {"user.rs": self.user_source(source), "main.rs": harness},
                "build": "rustc --edition 2021 -C overflow-checks=on -C debuginfo=1 -C opt-level=0 -o prog main.rs",
                "run": "./prog {i}"}

    def classify(self, code, out, err):
        mark = re.search(rf"^{SELF_CALL}(\d+)$", err or "", re.MULTILINE)
        err = re.sub(rf"^{SELF_CALL}\d+\n?", "", err or "", flags=re.MULTILINE)
        result = super().classify(code, out, err)
        if result.get("kind") == "exception" and result.get("error") == "panic":
            result["error"] = panic_name(result.get("message") or "")
        if result.get("error") == "stack overflow":
            result["line"] = int(mark[1]) if mark else None
        return result

    def crash_error(self, code, err):
        if "has overflowed its stack" in err:
            return "stack overflow", next(ln.strip() for ln in err.splitlines() if "overflowed" in ln)
        alloc = re.search(r"memory allocation of \d+ bytes failed", err)
        if alloc:
            return "allocation failed", alloc[0]
        return super().crash_error(code, err)


def panic_name(message):
    for pattern, name in PANICS:
        match = re.search(pattern, message)
        if match:
            return name or match[0]
    return "panic"


HARNESS = r"""#![allow(warnings)]
fn main() { __phantom::run() }

mod __phantom {
    use std::collections::{HashMap, HashSet};
    use std::fmt::Write as _;
    use std::io::Write as _;
    use std::sync::Mutex;

    type Panic = (String, Option<(String, u32, u32)>, String);
    static PANIC: Mutex<Option<Panic>> = Mutex::new(None);

    // Debug output when the result type has it, a placeholder otherwise (autoref specialization).
    pub struct Show<'a, T>(pub &'a T);
    pub trait ViaDebug { fn __phantom_show(&self) -> String; }
    impl<'a, T: std::fmt::Debug> ViaDebug for &Show<'a, T> {
        fn __phantom_show(&self) -> String {
            let mut out = Limited(String::new());
            if write!(out, "{:?}", self.0).is_err() { out.0.push_str("..."); }
            out.0
        }
    }
    pub trait ViaAny { fn __phantom_show(&self) -> String; }
    impl<'a, T> ViaAny for Show<'a, T> {
        fn __phantom_show(&self) -> String { String::from("(a value without Debug)") }
    }

    struct Limited(String);  // stops formatting after 300 bytes
    impl std::fmt::Write for Limited {
        fn write_str(&mut self, s: &str) -> std::fmt::Result {
            for c in s.chars() {
                if self.0.len() >= 300 { return Err(std::fmt::Error); }
                self.0.push(c);
            }
            Ok(())
        }
    }

    fn json(s: &str) -> String {
        let mut out = String::from("\"");
        for c in s.chars() {
            match c {
                '"' => out.push_str("\\\""),
                '\\' => out.push_str("\\\\"),
                '\n' => out.push_str("\\n"),
                c if (c as u32) < 0x20 => { let _ = write!(out, "\\u{:04x}", c as u32); }
                c => out.push(c),
            }
        }
        out.push('"');
        out
    }

    fn call(i: usize) -> String {
        match i {
__ARMS__
            _ => String::from("no such input"),
        }
    }

    pub fn run() {
        let i = std::env::args().nth(1).and_then(|a| a.parse::<usize>().ok()).unwrap_or(usize::MAX);
        __SELF_CALL__
        std::panic::set_hook(Box::new(|info| {
            let payload = info.payload();
            let message = match (payload.downcast_ref::<&str>(), payload.downcast_ref::<String>()) {
                (Some(s), _) => s.to_string(),
                (_, Some(s)) => s.clone(),
                _ => String::from("Box<dyn Any>"),
            };
            let location = info.location().map(|l| (l.file().to_string(), l.line(), l.column()));
            let trace = std::backtrace::Backtrace::force_capture().to_string();
            if let Ok(mut slot) = PANIC.lock() { *slot = Some((message, location, trace)); }
        }));
        let worker = std::thread::Builder::new().name(String::from("main")).stack_size(4 << 20)
            .spawn(move || std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| call(i))))
            .expect("phantom: no thread for the call");
        let verdict = match worker.join() {
            Ok(Ok(text)) => format!("{{\"ok\": true, \"result\": {}}}", json(&text)),
            _ => failure(),
        };
        let mut out = std::io::stdout().lock();
        let _ = write!(out, "\n__MARK__{}\n", verdict);
        let _ = out.flush();
    }

    fn failure() -> String {
        let (message, location, backtrace) = PANIC.lock().ok().and_then(|mut slot| slot.take())
            .unwrap_or_else(|| (String::from("panic"), None, String::new()));
        let mut line = location.as_ref().filter(|l| l.0.ends_with("user.rs")).map(|l| l.1);
        let mut trace = match &location {
            Some((file, l, c)) => format!("panicked at {}:{}:{}\n", file, l, c),
            None => String::new(),
        };
        let mut frame = String::new();
        for row in backtrace.lines().map(str::trim) {
            if let Some(at) = row.strip_prefix("at ") {
                if let Some(pos) = at.find("user.rs:") {
                    let _ = write!(trace, "  at {} ({})\n", frame, &at[pos..]);
                    if line.is_none() { line = at[pos..].split(':').nth(1).and_then(|n| n.parse().ok()); }
                }
            } else if let Some((_, name)) = row.split_once(": ") {
                frame = name.trim_start_matches("main::").to_string();
            }
        }
        let message: String = message.chars().take(1000).collect();
        format!("{{\"ok\": false, \"kind\": \"exception\", \"error\": \"panic\", \"message\": {}, \"trace\": {}, \"line\": {}}}",
                json(&message), json(&trace), line.map_or(String::from("null"), |n| n.to_string()))
    }
}

include!("user.rs");
"""
