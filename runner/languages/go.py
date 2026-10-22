"""Go: top-level functions, built once with the go toolchain, each input run as a fresh process.

The user's file stays main.go with its line numbers intact: its package clause becomes `package main`
and its own main() is renamed (with every call to it), all on the same lines. phantom_harness.go holds
the real main(): it builds input number argv[1] into typed variables, creates `.entered` (marks_entry),
calls the function, recovers a panic (error = a stable name such as "index out of range", or panic(T)
for other panic values; trace = debug.Stack()) and prints the verdict. Returning a non-nil error is
Go's ordinary error handling: the call is ok, the result shows it. The result is shown with %#v when it
is small; a big one is cut while it is formatted, so formatting it can't eat the function's time.

The user's init() and package-level var initializers run in every process, before main(). If they fail
(a panic, log.Fatal on a missing setting), or an argument panics while it is built, `.entered` is
missing and the failure is "not_entered": never a finding (all inputs failing that way = status error).
A program that ends itself with os.Exit / log.Fatal and no panic is kind "exit": never a finding.
A panic(...) or log.Panic(...) call on the failing line is the function rejecting its input on purpose
(deliberate); a runtime error on such a line (an index inside the message) stays a bug.

What can't be recovered is read from stderr: stack overflow (the harness caps the stack at 16 MB, so
infinite recursion fails fast and inside the memory limit), deadlock, concurrent map writes, a panic
in another goroutine. A run that hangs gets SIGQUIT shortly before the hard timeout, so Go prints
every goroutine's stack and the timeout points at the loop it was caught in (its header line).
The package is built without inlining (-gcflags=-l), so frames stay in the user's functions.

Inputs are Go literals of the declared parameter types (untyped constants convert, composite literals
spell out their type). A nil slice is just an empty slice; a nil map or pointer, a struct's zero value
and extreme integers carry an assumption. Index-like parameters (i, idx, start, end, ...) that the body
uses to index or slice a slice/array/string parameter (directly, or through a variable assigned from
them, like a loop counter) stay inside it, or the input says that they may not; unsigned ones are never
negative. A start/end pair (low/high, from/to, ...) given in the wrong order says so too.

The two files are built as a file list without a go.mod, so the language version is the toolchain's.
The phantom-go image holds a prebuilt standard library in a read-only GOCACHE, and a read-only cache
breaks builds: Go indexes any package whose files are over 2 s old into the cache, which happens
when the machine is busy. So the build first links that cache into a writable copy made of symlinks
(~30 ms) instead of rebuilding the standard library (~9 s).
"""
import re
from collections import Counter
from dataclasses import dataclass, field

from runner.languages.base import MAX_INPUTS, RESULT_MARK, Function, Input, Language, Param

INT_BITS = {"int": 64, "int8": 8, "int16": 16, "int32": 32, "int64": 64, "uint": 64, "uint8": 8,
            "uint16": 16, "uint32": 32, "uint64": 64, "uintptr": 64}
BASIC = {  # type: (typical value then boundary values, three varied elements for collections)
    "string": (['"abc"', '""', '" "', '"a:b"', '"é"'], ['"a"', '"b"', '"c"']),
    "bool": (["true", "false"], ["true", "false", "true"]),
    "float64": (["1.5", "0", "-1.5"], ["1.5", "2.5", "3.5"]),
    "float32": (["1.5", "0", "-1.5"], ["1.5", "2.5", "3.5"]),
    "complex128": (["1", "0", "-1"], ["1", "2", "3"]),
    "complex64": (["1", "0", "-1"], ["1", "2", "3"]),
    "byte": (["'a'", "0", "255"], ["'a'", "'b'", "'c'"]),
    "rune": (["'a'", "0", "'é'"], ["'a'", "'b'", "'c'"]),
    "any": (["1", '"abc"', "0", '""', "nil", "[]int{}"], ["1", '"abc"', "nil"]),
}
MATH = {  # names an input may use; the harness gets the number, so main.go needn't import math
    "math.MaxInt": "9223372036854775807", "math.MinInt": "-9223372036854775808",
    "math.MaxInt8": "127", "math.MinInt8": "-128", "math.MaxInt16": "32767", "math.MinInt16": "-32768",
    "math.MaxInt32": "2147483647", "math.MinInt32": "-2147483648",
    "math.MaxInt64": "9223372036854775807", "math.MinInt64": "-9223372036854775808",
    "math.MaxUint": "18446744073709551615", "math.MaxUint8": "255", "math.MaxUint16": "65535",
    "math.MaxUint32": "4294967295", "math.MaxUint64": "18446744073709551615",
    "math.MaxFloat32": "3.40282346638528859811704183484516925440e+38",
    "math.MaxFloat64": "1.79769313486231570814527423731704356798070e+308",
    "math.SmallestNonzeroFloat64": "4.9406564584124654417656879286822137236505980e-324",
    "math.Pi": "3.14159265358979323846264338327950288419716939937510582097494459",
}
ROLES = {  # last word of an int parameter's name -> how it relates to the first slice/array/string parameter
    **dict.fromkeys(("i", "j", "idx", "index", "pos", "position"), "index"),  # 0 <= i < len
    **dict.fromkeys(("start", "begin", "from", "lo", "low"), "start"),  # 0 <= start <= len
    **dict.fromkeys(("end", "stop", "to", "hi", "high"), "end"),  # 0 <= end <= len, start <= end
}
RUNTIME_NAMES = (  # runtime panic message prefix -> stable error name
    ("invalid memory address or nil pointer dereference", "nil pointer dereference"),
    ("assignment to entry in nil map", "nil map assignment"),
    ("index out of range", "index out of range"), ("slice bounds out of range", "slice bounds out of range"),
    ("integer divide by zero", "integer divide by zero"), ("interface conversion", "interface conversion"),
    ("panic called with nil argument", "panic(nil)"), ("makeslice: len out of range", "makeslice: len out of range"),
    ("makeslice: cap out of range", "makeslice: cap out of range"),
)
NUMBERS = ("int_literal", "float_literal", "imaginary_literal")
TYPE_NODES = frozenset({"type_identifier", "slice_type", "array_type", "implicit_length_array_type", "map_type",
                        "pointer_type", "interface_type"})
WRAPPER = "package p\nvar _ = "
USER_FRAME = re.compile(r"(?:^|[\s/])main\.go:(\d+)", re.MULTILINE)
MAX_ARRAY = 1 << 20  # elements in all the array types of one input together: new([1 << 40]byte) is not a literal
ESCAPE = re.compile(r'\\(?:[abfnrtv\\"]|x[0-9a-fA-F]{2}|[0-3][0-7]{2}|u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8}))')
UNHASHABLE = ("slice_type", "map_type")  # as a key of a map[any]... they panic while the input is built
BUILD = ('if [ -z "${GOCACHE:-}" ]; then export GOCACHE=/tmp/phantom-go-cache; '
         'elif [ ! -w "$GOCACHE" ]; then cp -rs "$GOCACHE" .gocache && date +%s > .gocache/trim.new '
         '&& mv -f .gocache/trim.new .gocache/trim.txt && export GOCACHE="$PWD/.gocache"; fi; '
         'go build -gcflags=-l -ldflags="-s -w" -o prog main.go phantom_harness.go')

HARNESS = """package main

import (
	phantomJSON "encoding/json"
	phantomFmt "fmt"
	phantomOS "os"
	phantomReflect "reflect"
	phantomRuntime "runtime"
	phantomDebug "runtime/debug"
	phantomStrconv "strconv"
)

var phantomCalls = []func() string{
	@CALLS@
}

// phantomEnter runs after the arguments are built, right before the call: a failure without the file
// happened before the function was entered (package init, a var initializer, an argument).
func phantomEnter() {
	if file, err := phantomOS.Create(".entered"); err == nil {
		file.Close()
	}
}

const phantomLimit = 300

// phantomText formats like %#v but stops after phantomLimit bytes, so a huge result costs nothing.
type phantomText struct {
	buf  []byte
	full bool
}

func (t *phantomText) add(s string) {
	t.buf = append(t.buf, s...)
	if len(t.buf) > phantomLimit {
		t.full = true
	}
}

func (t *phantomText) value(v phantomReflect.Value, depth int) {
	if t.full {
		return
	}
	switch v.Kind() {
	case phantomReflect.Invalid:
		t.add("<nil>")
	case phantomReflect.String:
		s := v.String()
		if len(s) > phantomLimit {
			s = s[:phantomLimit]
		}
		t.add(phantomStrconv.Quote(s))
	case phantomReflect.Slice, phantomReflect.Array, phantomReflect.Map:
		if v.Kind() != phantomReflect.Array && v.IsNil() {
			t.add(v.Type().String() + "(nil)")
			return
		}
		t.add(v.Type().String() + "{")
		if v.Kind() == phantomReflect.Map {
			entries := v.MapRange()
			for n := 0; !t.full && entries.Next(); n++ {
				if n > 0 {
					t.add(", ")
				}
				t.value(entries.Key(), depth+1)
				t.add(":")
				t.value(entries.Value(), depth+1)
			}
		} else {
			for n := 0; !t.full && n < v.Len(); n++ {
				if n > 0 {
					t.add(", ")
				}
				t.value(v.Index(n), depth+1)
			}
		}
		t.add("}")
	case phantomReflect.Struct:
		t.add(v.Type().String() + "{")
		for n := 0; !t.full && n < v.NumField(); n++ {
			if n > 0 {
				t.add(", ")
			}
			t.add(v.Type().Field(n).Name + ":")
			t.value(v.Field(n), depth+1)
		}
		t.add("}")
	case phantomReflect.Interface:
		if v.IsNil() {
			t.add(v.Type().String() + "(nil)")
			return
		}
		t.value(v.Elem(), depth+1)
	case phantomReflect.Pointer:
		if v.IsNil() {
			t.add("(" + v.Type().String() + ")(nil)")
			return
		}
		switch v.Elem().Kind() {
		case phantomReflect.Array, phantomReflect.Slice, phantomReflect.Struct, phantomReflect.Map:
			if depth == 0 { // like %#v: only the outermost pointer is followed
				t.add("&")
				t.value(v.Elem(), depth+1)
				return
			}
		}
		t.add(phantomFmt.Sprintf("(%s)(%#x)", v.Type().String(), v.Pointer()))
	default:
		t.add(phantomFmt.Sprintf("%#v", v))
	}
}

func phantomFormat(value any) string {
	t := &phantomText{}
	t.value(phantomReflect.ValueOf(value), 0)
	if !t.full {
		return phantomFmt.Sprintf("%#v", value) // small: Go's own formatting
	}
	return string(t.buf)
}

func phantomShow(values ...any) string {
	text := ""
	for i, value := range values {
		if i > 0 {
			text += ", "
		}
		switch v := value.(type) {
		case nil:
			text += "nil"
		case error:
			message := v.Error()
			if len(message) > phantomLimit {
				message = message[:phantomLimit]
			}
			text += "error(" + phantomStrconv.Quote(message) + ")"
		default:
			text += phantomFormat(v)
		}
		if len(text) > phantomLimit {
			return text[:phantomLimit-3] + "..."
		}
	}
	if len(values) > 1 {
		text = "(" + text + ")"
	}
	return text
}

func phantomRun(i int) (verdict map[string]any) {
	defer func() {
		if r := recover(); r != nil {
			name := phantomFmt.Sprintf("panic(%T)", r)
			if _, ok := r.(phantomRuntime.Error); ok {
				name = "runtime error"
			}
			verdict = map[string]any{"ok": false, "kind": "exception", "error": name,
				"message": phantomFmt.Sprint(r), "trace": string(phantomDebug.Stack())}
		}
	}()
	return map[string]any{"ok": true, "result": phantomCalls[i]()}
}

func main() {
	phantomDebug.SetMaxStack(16 << 20)
	stdout := phantomOS.Stdout
	if devNull, err := phantomOS.OpenFile(phantomOS.DevNull, phantomOS.O_WRONLY, 0); err == nil {
		phantomOS.Stdout = devNull // the function's own output can't push the verdict out of the captured part
	}
	i, _ := phantomStrconv.Atoi(phantomOS.Args[1])
	data, _ := phantomJSON.Marshal(phantomRun(i))
	stdout.WriteString("\\n@MARK@" + string(data) + "\\n")
	phantomOS.Exit(0)
}
"""


@dataclass
class GoFunction(Function):
    types: dict = field(default_factory=dict)  # types declared in the file: name -> type text


class Go(Language):
    name = "go"
    title = "Go"
    vscode_ids = ("go",)
    extensions = (".go",)
    grammar = "go"
    image = "phantom-go"
    local_tool = "go"
    memory = "512m"
    tmpfs = "256m"
    env = {"CGO_ENABLED": "0", "GOFLAGS": "-buildvcs=false"}
    build_timeout = 40.0
    call_timeout = 1.5
    user_file = "main"
    null_words = ("nil",)
    marks_entry = True  # phantomEnter() creates .entered between building the arguments and the call
    rejection_pattern = r"(?<![\w.])panic\s*\(|\blog\.Panic(?:f|ln)?\s*\("  # see deliberate()
    literal_hint = ('Go literals: numbers (a leading minus is fine), strings ("..." or `...`), runes (\'a\'), '
                    'true/false, nil, composite literals with their type: []int{1, 2}, [2]string{"a", "b"}, '
                    'map[string]int{"a": 1}, [][]int{{1}, {}}, Point{1, 2} (fields by position), &Point{1, 2}, '
                    'plus new(int), []byte("abc") and math.MaxInt64, math.MinInt64 and the other math limits')
    literal_nodes = frozenset({
        *NUMBERS, "rune_literal", "interpreted_string_literal", "interpreted_string_literal_content",
        "escape_sequence", "raw_string_literal", "raw_string_literal_content", "true", "false", "nil",
        "composite_literal", "literal_value", "literal_element", "keyed_element", "parenthesized_expression",
        "type_conversion_expression", *TYPE_NODES,
    })
    literal_names = frozenset(MATH)

    # ---------- functions ----------

    def functions(self, source):
        root = self.parse(source).root_node
        types = {}
        for decl in (n for n in root.named_children if n.type == "type_declaration"):
            for spec in decl.named_children:
                name, kind = spec.child_by_field_name("name"), spec.child_by_field_name("type")
                if spec.type in ("type_spec", "type_alias") and name and kind \
                        and spec.child_by_field_name("type_parameters") is None:
                    types[self.text(name)] = self.text(kind)
        return [self._function(node, types) for node in root.named_children
                if node.type in ("function_declaration", "method_declaration")]

    def _function(self, node, types):
        name, skip, params = self.text(node.child_by_field_name("name")), None, []
        holder = node.child_by_field_name("parameters")
        for decl in holder.named_children if holder is not None else []:
            kind = self.text(decl.child_by_field_name("type"))
            if decl.type == "variadic_parameter_declaration":
                skip = skip or "variadic parameters are not supported yet"
                kind = f"...{kind}"
            elif decl.type != "parameter_declaration":
                continue  # a comment
            for n in decl.children_by_field_name("name") or [None]:
                params.append(Param(self.text(n) if n else f"arg{len(params) + 1}", kind))
        if node.type == "method_declaration":
            receiver = [n for n in node.child_by_field_name("receiver").named_children if n.type != "comment"]
            kind = receiver[0].child_by_field_name("type") if receiver else None
            while kind is not None and kind.type in ("pointer_type", "generic_type", "parenthesized_type"):
                kind = kind.child_by_field_name("type") if kind.type == "generic_type" else kind.named_children[-1]
            name, skip = f"{self.text(kind)}.{name}", "methods are not run yet"
        elif node.child_by_field_name("type_parameters") is not None:
            skip = "generic functions are not run yet"
        elif name in ("main", "init", "_"):
            skip = "main and init run by themselves, they aren't called with inputs"
        elif node.child_by_field_name("body") is None:
            skip = "declared without a body"
        for p in params if skip is None else []:
            if not self.edge_values(p.type, p.name, types):
                skip = f"parameter {p.name} has type {p.type}, which Specter can't build values for yet"
                break
        result = node.child_by_field_name("result")
        return GoFunction(name=name, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, params=params,
                          returns=self.text(result), text=self.text(node), skip=skip, types=types)

    # ---------- types ----------

    def _type_node(self, type_text):
        if not type_text:
            return None
        tree = self.parse(f"package p\nvar _ {type_text}\n")
        decl = tree.root_node.named_children[-1] if not tree.root_node.has_error else None
        spec = decl.named_children[0] if decl is not None and decl.type == "var_declaration" else None
        return spec.child_by_field_name("type") if spec is not None else None

    def _resolve(self, node, types):
        """Follow type names declared in the file: (underlying type node, first declared name or None)."""
        named = None
        for _ in range(5):
            if node is not None and node.type == "parenthesized_type":
                node = node.named_children[0] if node.named_children else None
            elif node is not None and node.type == "type_identifier" and self.text(node) in types:
                named = named or self.text(node)
                node = self._type_node(types[self.text(node)])
            else:
                break
        return node, named

    def _int_type(self, type_text, types):
        node, _ = self._resolve(self._type_node(type_text), types)
        return node is not None and node.type == "type_identifier" and self.text(node) in INT_BITS

    # ---------- inputs ----------

    def edge_values(self, type_text, param_name, types=None):
        return self._values(self._type_node(type_text), param_name, types or {})

    def _values(self, node, name, types, depth=0):
        node, named = self._resolve(node, types)
        if node is None or depth > 3:
            return []
        kind, text = node.type, self.text(node)
        typ = named or text  # the type a composite literal names
        if kind == "type_identifier":
            return self._basic(text, name)
        if kind == "interface_type":
            return self._basic("any", name) if not node.named_children else []
        if kind == "struct_type":
            return [(f"{named}{{}}", f"{name} may be the zero value {named}{{}}")] if named else []
        if kind == "pointer_type":
            target = node.named_children[-1]
            values = self._values(target, name, types, depth + 1)
            if not values:
                return []
            first, note = values[0]
            if first.endswith("}") and not first.startswith(("&", "new(")):
                pointer = (f"&{first}", note)  # &Point{}, &[]int{1, 2, 3}
            else:  # new(int); new(*Node) for **Node, which points to a nil *Node: &&Node{} isn't Go
                inner, _ = self._resolve(target, types)
                points_to_nil = inner is not None and inner.type in ("pointer_type", "map_type")
                what = f"*{name}" if name.isidentifier() else f"what {name} points to"
                pointer = (f"new({self.text(target)})", f"{what} may be nil" if points_to_nil else None)
            return [pointer, ("nil", f"{name} may be nil")]
        if kind == "slice_type":
            elem = node.child_by_field_name("element")
            if typ == "[]byte":
                return [('[]byte("abc")', None), ("[]byte{}", None), ('[]byte("a")', None), ("[]byte{0}", None)]
            values = self._values(elem, f"an element of {name}", types, depth + 1)
            if not values:
                return []
            items = [self._elide(v, elem) for v in self._variety(elem, types, values)]
            return [(f"{typ}{{{', '.join(items)}}}", values[0][1]), (f"{typ}{{}}", None),
                    (f"{typ}{{{items[0]}}}", values[0][1])] + \
                [(f"{typ}{{{self._elide(v, elem)}}}", a) for v, a in values[1:2]]
        if kind == "array_type":
            elem, size = node.child_by_field_name("element"), self._size(node)
            values = self._values(elem, f"an element of {name}", types, depth + 1)
            if not values:
                return []
            result = []
            if size and size <= 8:
                variety = [self._elide(v, elem) for v in self._variety(elem, types, values)]
                result.append((f"{typ}{{{', '.join(variety[k % 3] for k in range(size))}}}", values[0][1]))
            base, _ = self._resolve(elem, types)
            zero = f"an element of {name} may be nil" if base is not None and base.type in ("pointer_type", "map_type") \
                else values[0][1] if base is not None and base.type == "struct_type" else None
            return result + [(f"{typ}{{}}", zero)]
        if kind == "map_type":
            key, value = node.child_by_field_name("key"), node.child_by_field_name("value")
            keys = self._values(key, f"a key of {name}", types, depth + 1)
            vals = self._values(value, f"a value in {name}", types, depth + 1)
            if not keys or not vals:
                return []
            distinct = list(dict.fromkeys(self._variety(key, types, keys)))[:2]
            entries = ", ".join(f"{self._elide(k, key)}: {self._elide(v, value)}"
                                for k, v in zip(distinct, self._variety(value, types, vals)))
            return [(f"{typ}{{{entries}}}", keys[0][1] or vals[0][1]), (f"{typ}{{}}", None),
                    ("nil", f"{name} may be nil")]
        return []  # functions, channels, inline structs, other packages' types, generics: not yet

    def _basic(self, text, name):
        if text in INT_BITS:
            signed, big = text.startswith("int"), INT_BITS[text] >= 32  # 8/16-bit limits are everyday values
            top = "math.MaxUint" if text == "uintptr" else \
                f"math.MaxInt{text[3:]}" if signed else f"math.MaxUint{text[4:]}"
            values = [("3", None), ("0", None), ("-1" if signed else "1", None),
                      (top, f"{name} may be as large as {top}" if big else None)]
            if signed:
                bottom = top.replace("Max", "Min")
                values.append((bottom, f"{name} may be as small as {bottom}" if big else None))
            return values
        return [(v, None) for v in BASIC[text][0]] if text in BASIC else []

    def _variety(self, node, types, values):
        base, _ = self._resolve(node, types)
        text = self.text(base) if base is not None and base.type == "type_identifier" else ""
        if text in INT_BITS:
            return ["1", "2", "3"]
        return BASIC[text][1] if text in BASIC else [values[0][0]] * 3

    def _elide(self, value, elem):
        """[]T{T{1}} -> []T{{1}}: Go lets a composite literal leave out its elements' type."""
        prefix = self.text(elem)
        if elem.type == "pointer_type":
            prefix = "&" + self.text(elem.named_children[-1])
        return value[len(prefix):] if value.startswith(prefix + "{") else value

    def _size(self, array_node):
        length = array_node.child_by_field_name("length")
        text = (self.text(length) or "").replace("_", "")
        try:
            return int(text, 8) if re.fullmatch(r"0[0-7]+", text) else int(text, 0)
        except ValueError:
            return None

    # ----- index-like parameters stay inside the sequence they index (no false positives) -----

    def _sequences(self, fn):
        """{position: fixed length or None} of every slice/array/string parameter."""
        found = {}
        for k, p in enumerate(fn.params):
            node, _ = self._resolve(self._type_node(p.type), fn.types)
            if node is not None and (node.type in ("slice_type", "array_type") or self.text(node) == "string"):
                found[k] = self._size(node) if node.type == "array_type" else None
        return found

    def _names(self, node):
        """Every identifier in node's subtree."""
        names, stack = set(), [node] if node is not None else []
        while stack:
            current = stack.pop()
            if current.type == "identifier":
                names.add(self.text(current))
            stack.extend(current.named_children)
        return names

    def _indexing(self, fn):
        """{name: identifiers its index and slice expressions use}, following assignments back two steps,
        so a loop counter's bounds count too: `for k := start; k < end; k++ { v[k] }` uses start and end."""
        direct, flows, stack = {}, [], [self.parse(f"package p\n{fn.text}\n").root_node]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            field = node.child_by_field_name
            if node.type in ("index_expression", "slice_expression"):
                operand = field("operand")
                if operand is not None and operand.type == "identifier":
                    used = direct.setdefault(self.text(operand), set())
                    for part in ("index", "start", "end", "capacity"):
                        used |= self._names(field(part))
            elif node.type in ("short_var_declaration", "assignment_statement", "range_clause"):
                flows.append((self._names(field("left")), self._names(field("right"))))
            elif node.type == "var_spec":
                flows.append(({self.text(n) for n in node.children_by_field_name("name")}, self._names(field("value"))))
            elif node.type == "for_clause" and field("initializer") is not None \
                    and field("initializer").type == "short_var_declaration":
                flows.append((self._names(field("initializer").child_by_field_name("left")), self._names(field("condition"))))
        for used in direct.values():
            for _ in range(2):
                for left, right in flows:
                    if left & used:
                        used |= right
        return direct

    def _roles(self, fn):
        """{position: (role, position of the sequence it indexes, or None)} of the int parameters named
        like an index (i, start, end, ...). The sequence is a slice/array/string parameter that the body
        indexes or slices with the parameter; a value bound like `low` in `r >= low` has none."""
        named = {}
        for i, p in enumerate(fn.params):
            word = re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", p.name)[-1].lower()
            if word in ROLES and self._int_type(p.type, fn.types):
                named[i] = ROLES[word]
        if not named:
            return {}
        sequences, used = self._sequences(fn), self._indexing(fn)
        return {i: (role, next((k for k in sequences if k != i and fn.params[i].name in used.get(fn.params[k].name, ())),
                               None)) for i, role in named.items()}

    def _unsigned(self, type_text, types):
        node, _ = self._resolve(self._type_node(type_text), types)
        return node is not None and self.text(node) in INT_BITS and self.text(node).startswith("u")

    def _length(self, expr, fixed):
        if fixed is not None:
            return fixed
        try:
            _, node = self.expression_tree(expr)
        except Exception:
            return None
        while node is not None and node.type in ("parenthesized_expression", "type_conversion_expression"):
            node = node.child_by_field_name("operand") or (node.named_children or [None])[-1]
        if node is None:
            return None
        if node.type == "nil":
            return 0
        if node.type == "composite_literal":
            body = node.child_by_field_name("body")
            return sum(c.type in ("literal_element", "keyed_element") for c in body.named_children)
        if node.type == "raw_string_literal":
            return len(self.text(node)[1:-1].encode())
        if node.type == "interpreted_string_literal":
            size = 0
            for part in node.named_children:
                piece = self.text(part)
                if part.type != "escape_sequence":
                    size += len(piece.encode())
                elif piece[1] in "uU" and re.fullmatch(r"[0-9a-fA-F]+", piece[2:]):
                    size += len(chr(min(int(piece[2:], 16), 0x10FFFF)).encode(errors="replace"))
                else:
                    size += 1
            return size
        return None

    def _bounds(self, fn, args, roles=None):
        """Assumptions an input needs because an index-like argument is outside the sequence it indexes,
        or a start/end pair is in the wrong order."""
        roles = self._roles(fn) if roles is None else roles
        sequences, notes, seen = self._sequences(fn), [], {}
        for i, (role, seq) in roles.items():
            text = MATH.get(args[i].strip(), args[i].strip())
            if not re.fullmatch(r"-?\d+", text):
                continue
            value = seen[role] = int(text)
            length = self._length(args[seq], sequences[seq]) if seq is not None else None
            if length is not None and not 0 <= value <= (length - 1 if role == "index" else length):
                notes.append(f"{fn.params[i].name} may be outside the bounds of {fn.params[seq].name}")
        if "start" in seen and "end" in seen and seen["start"] > seen["end"]:
            names = {role: fn.params[i].name for i, (role, _) in roles.items()}
            notes.append(f"{names['start']} may be greater than {names['end']}")
        return notes

    def edge_inputs(self, fn):
        """Like Language.edge_inputs, but index-like parameters get values relative to the length of the
        sequence they index, and every input whose index ends up outside it says so."""
        types = getattr(fn, "types", {})
        options = [self.edge_values(p.type, p.name, types) for p in fn.params]
        if any(not values for values in options):
            return []
        roles = self._roles(fn) if isinstance(fn, GoFunction) else {}
        sequences = self._sequences(fn) if roles else {}
        for i, (role, seq) in roles.items():
            if seq is not None:
                size = self._length(options[seq][0][0], sequences[seq]) or 0
                values = {"index": [min(1, size - 1), 0, size - 1, -1, size], "start": [0, 1, size, -1, size + 1],
                          "end": [size, 0, 1, -1, size + 1]}[role]
                options[i] = [(str(v), None) for v in dict.fromkeys(values)]
            if self._unsigned(fn.params[i].type, types):  # -1 doesn't even compile for a uint
                options[i] = [(v, a) for v, a in options[i] if not v.startswith("-")]
        base = [values[0] for values in options]
        inputs = [Input([value for value, _ in base], assumptions=[a for _, a in base if a])]
        for index, values in enumerate(options):
            for value, assumption in values[1:]:
                args = [b for b, _ in base]
                args[index] = value
                notes = [a for i, (_, a) in enumerate(base) if a and i != index] + ([assumption] if assumption else [])
                inputs.append(Input(args, assumptions=notes))
        unique = list({tuple(i.args): i for i in inputs}.values())[:MAX_INPUTS]
        for item in unique if roles else []:
            item.assumptions += [n for n in self._bounds(fn, item.args, roles) if n not in item.assumptions]
        return unique

    def expected_function(self, fn):
        returns = (fn.returns or "").strip()
        if not returns or returns.startswith("("):  # nothing, or several results
            return None
        return f"\n\nfunc phantomExpected(value {returns}) {returns} {{ return value }}\n", "phantomExpected"

    def model_input(self, fn, args, hypothesis):
        result = super().model_input(fn, args, hypothesis)
        if result is not None and isinstance(fn, GoFunction):
            result.assumptions += [n for n in self._bounds(fn, result.args) if n not in result.assumptions]
        return result

    def nullable(self, type_text):
        node = self._type_node(type_text)
        return node is not None and (node.type in ("slice_type", "interface_type", "function_type")
                                     or self.text(node) in ("any", "error"))

    # ---------- literal checking ----------

    def expression_tree(self, expr):
        tree = self.parse(f"{WRAPPER}{expr}\n")
        decls = tree.root_node.named_children
        spec = decls[1].named_children[0] if len(decls) > 1 and decls[1].type == "var_declaration" else None
        value = spec.child_by_field_name("value") if spec is not None else None
        return tree, value.named_children[0] if value is not None and value.named_children else None

    def literal_ok(self, expr):
        """Plain literals, and none that fails while the harness builds it: no huge arrays (out of memory),
        no slice-to-array conversions, no slice or map as a map[any] key (the checks in _literal_node).
        Those would only be "not_entered" now (marks_entry), but they also cost builds."""
        if not super().literal_ok(expr):
            return False
        _, node = self.expression_tree(expr)
        total, stack = 1, [node]
        while stack:
            current = stack.pop()
            if current.type == "array_type":
                total *= max(self._size(current) or 1, 1)
                if total > MAX_ARRAY:
                    return False
            stack.extend(current.named_children)
        return True

    def _literal_node(self, node):
        kind = node.type
        if kind == "selector_expression":
            return self.text(node) in self.literal_names  # math.MaxInt64: replaced by its number in the harness
        if kind == "unary_expression":  # a sign before a number, & before a composite literal; no <-ch, ^x, !b
            op, operand = node.child_by_field_name("operator"), node.child_by_field_name("operand")
            if op.type in ("-", "+"):
                return operand.type in NUMBERS and self._literal_node(operand)
            return op.type == "&" and operand.type == "composite_literal" and self._literal_node(operand)
        if kind == "call_expression":  # only new(T): a type can't be a value, so no user function can hide here
            callee, args = node.child_by_field_name("function"), node.child_by_field_name("arguments")
            return self.text(callee) == "new" and len(args.named_children) == 1 \
                and args.named_children[0].type in TYPE_NODES and self._literal_node(args.named_children[0])
        if kind == "type_conversion_expression":  # []byte("abc"), []rune("abc"), []int(nil); not [3]int(xs)
            typ, operand = node.child_by_field_name("type"), node.child_by_field_name("operand")
            if typ is None or operand is None:
                return False
            if operand.type in ("interpreted_string_literal", "raw_string_literal"):
                ok = re.sub(r"\s", "", self.text(typ)) in ("[]byte", "[]rune")
            else:
                ok = operand.type == "nil" and typ.type in ("slice_type", "map_type", "pointer_type", "interface_type")
            return ok and self._literal_node(typ) and self._literal_node(operand)
        if kind == "array_type" and node.child_by_field_name("length").type != "int_literal":
            return False
        if kind == "interface_type" and node.named_children:
            return False  # interface{} only
        if kind == "type_identifier" and self.text(node).startswith("phantom"):
            return False  # the harness's own names
        if kind == "literal_value" and not self._keys_ok(node):
            return False
        if kind in NUMBERS and not self._number_ok(node):
            return False
        if kind == "escape_sequence":
            match = ESCAPE.fullmatch(self.text(node))
            code = int(match.group(1) or match.group(2) or "0", 16) if match else 0
            if match is None or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                return False
        if node.is_named and kind not in self.literal_nodes:
            return False  # identifiers, calls, conversions like int8(x), operators, func literals, comments...
        return all(self._literal_node(child) for child in node.children)

    def _keys_ok(self, body):
        """No duplicate keys, no slice or map as a key, and slice/array keys inside the slice/array."""
        parent = body.parent
        typ = parent.child_by_field_name("type") if parent is not None and parent.type == "composite_literal" else None
        size = self._size(typ) if typ is not None and typ.type == "array_type" else None
        keys = [e.child_by_field_name("key") for e in body.named_children if e.type == "keyed_element"]
        texts = [re.sub(r"\s", "", self.text(k)) for k in keys if k is not None]
        if len(texts) != len(set(texts)):
            return False  # map[string]int{"a": 1, "a": 2} doesn't compile
        for key in keys:
            inner = key.named_children[0] if key is not None and key.named_children else None
            if inner is None:
                continue
            stack = [inner]
            while stack:  # anywhere in the key: map[any]int{[1]any{[]int{}}: 1} fails the same way
                part = stack.pop()
                if part.type in ("composite_literal", "type_conversion_expression") \
                        and (part.child_by_field_name("type") or part).type in UNHASHABLE:
                    return False  # map[any]int{[]int{}: 1}: "hash of unhashable type" while it is built
                stack.extend(part.named_children)
            if typ is not None and typ.type in ("slice_type", "array_type", "implicit_length_array_type"):
                if inner.type == "unary_expression":
                    return False  # []int{-1: 1}
                number = self._integer(inner) if inner.type == "int_literal" else None
                if size is not None and number is not None and number >= size:
                    return False  # [3]int{5: 1}
        return True

    @staticmethod
    def _integer(node):
        text = node.text.decode().replace("_", "")
        try:
            return int(text, 8) if re.fullmatch(r"0[0-7]+", text) else int(text, 0)
        except ValueError:
            return None

    def _number_ok(self, node):
        """A number Go accepts as a constant in some type: no 1e1000, no 08, nothing past 64 bits."""
        if node.type == "int_literal":
            number = self._integer(node)
            return number is not None and number < 1 << 64
        text = self.text(node).replace("_", "").removesuffix("i")
        try:
            value = float.fromhex(text) if text.lower().startswith("0x") else float(text if text[:1] != "." else "0" + text)
        except ValueError:
            return node.type == "imaginary_literal"  # 0x1p-2i, 017i: rare forms, harmless
        return value not in (float("inf"), float("-inf"))

    def _go_expr(self, expr):
        """The expression for the harness: math limits replaced by their numbers."""
        if "math." not in expr:
            return expr
        _, node = self.expression_tree(expr)
        data, offset, edits, stack = expr.encode(), len(WRAPPER.encode()), [], [node] if node else []
        while stack:
            current = stack.pop()
            if current.type == "selector_expression" and self.text(current) in MATH:
                number = MATH[self.text(current)]
                edits.append((current.start_byte - offset, current.end_byte - offset,
                              f"({number})" if number.startswith("-") else number))
            else:
                stack.extend(current.children)
        for start, end, number in sorted(edits, reverse=True):
            data = data[:start] + number.encode() + data[end:]
        return data.decode()

    # ---------- running ----------

    def _user_file(self, source):
        """main.go: the package clause says main, the user's own main() and every use of the name are
        renamed (a call to main() would otherwise reach the harness's); same lines."""
        data, root = source.encode(), self.parse(source).root_node
        package = next((n for n in root.named_children if n.type == "package_clause"), None)
        edits = [] if package else [(0, 0, b"package main; ")]
        for node in root.named_children:
            if node.type == "comment" and package is not None and node.end_byte <= package.start_byte \
                    and re.match(r"//\s*(go:build|\+build)", self.text(node)):
                edits.append((node.start_byte, node.end_byte, b" " * (node.end_byte - node.start_byte)))
        stack = [root]
        while stack:  # identifiers only: not fields, methods or the package name
            node = stack.pop()
            if node.type == "identifier" and node.text == b"main":
                edits.append((node.start_byte, node.end_byte, b"phantomUserMain"))
            stack.extend(node.named_children)
        if package is not None:
            ident = package.named_children[-1]
            edits.append((ident.start_byte, ident.end_byte, b"main"))
        for start, end, text in sorted(edits, reverse=True):
            data = data[:start] + text + data[end:]
        return data.decode()

    def program(self, source, fn, inputs):
        """Each input becomes typed variables (untyped constants convert as in the call), then phantomEnter(),
        then the call: a panic while an argument is built happens before .entered exists."""
        calls = []
        for item in inputs:
            built = "".join(f"var phantomA{k} {' '.join(p.type.split())} = {self._go_expr(a)}; "
                            for k, (p, a) in enumerate(zip(fn.params, item.args)))
            call = f"{fn.name}({', '.join(f'phantomA{k}' for k in range(len(item.args)))})"
            body = f"return phantomShow({call})" if (fn.returns or "()") != "()" else f'{call}; return ""'
            calls.append(f"func() string {{ {built}phantomEnter(); {body} }},")
        harness = HARNESS.replace("@CALLS@", "\n\t".join(calls)).replace("@MARK@", RESULT_MARK)
        return {"files": {"main.go": self._user_file(source), "phantom_harness.go": harness}, "build": BUILD,
                # SIGQUIT before the hard timeout: Go then prints where every goroutine is (the looping line)
                "run": f"timeout -s QUIT {self.call_timeout - 0.5:g} ./prog {{i}}"}

    def run_inputs(self, source, fn, inputs, sandbox):
        """A hang is reported at the header of the innermost loop around the line SIGQUIT caught (where in
        the loop body the process happened to be is chance). Now and then Go can't show the looping
        goroutine's stack ("running on other thread"): such a hang gets the batch's usual line, or the
        function's first loop."""
        results = super().run_inputs(source, fn, inputs, sandbox)
        hangs = [r for r in results if r.get("kind") == "timeout"]
        if hangs:
            loops, stack = [], [self.parse(source).root_node]
            while stack:
                node = stack.pop()
                if node.type == "for_statement":
                    loops.append((node.start_point[0] + 1, node.end_point[0] + 1))
                stack.extend(node.named_children)
            for r in hangs:
                r["line"] = max((s for s, e in loops if s <= (r.get("line") or 0) <= e), default=r.get("line"))
            usual = Counter(r["line"] for r in hangs if r.get("line")).most_common(1)
            fallback = usual[0][0] if usual else min((s for s, _ in loops if fn.line <= s <= fn.end_line), default=None)
            for r in hangs:
                r["line"] = r.get("line") or fallback
        return results

    def parse_output(self, stdout, count):
        """An exit before .entered (log.Fatal in init() on a missing setting) is not_entered too, so a
        function that never got called reads as status error, not as "no bug found"."""
        results = super().parse_output(stdout, count)
        for line in stdout.splitlines():
            parts = line.split()
            if line.startswith("@@RUN ") and len(parts) > 4 and parts[4] == "0" and parts[1].isdigit() \
                    and int(parts[1]) < count and results[int(parts[1])].get("kind") == "exit":
                result = results[int(parts[1])]
                results[int(parts[1])] = {**result, "kind": "not_entered", "message":
                                          f"before the function was called: {result['error']}: {result['message']}"}
        return results

    def classify(self, code, out, err):
        result = super().classify(code, out, err)
        if result.get("kind") == "no_verdict":  # the harness always reports before its own exit: the code exited
            result = {"ok": False, "kind": "exit", "error": "os.Exit(0)", "message": "the program called os.Exit(0)"}
        if result.get("kind") == "crash" and str(result.get("error")).startswith("os.Exit("):
            result["kind"] = "exit"  # os.Exit(n) or log.Fatal on purpose: not a failure
        if result.get("kind") == "exception" and result.get("error") == "runtime error":
            result["error"] = runtime_name(result.get("message", ""))
        if result.get("kind") == "timeout" and err:
            result["traceback"] = err[:4000]
        return result

    def deliberate(self, source, failure):
        """A panic value raised by an explicit panic(...) / log.Panic(...) on the failing line: the function
        rejects the input on purpose. A runtime error there (an index or nil inside the message) and a
        library's panic reached from an ordinary line (strings.Repeat, regexp.MustCompile) stay bugs."""
        error = str(failure.get("error") or "")
        if failure.get("kind") not in ("exception", "crash") or not (error == "panic" or error.startswith("panic(")):
            return False
        return super().deliberate(source, failure)

    def crash_error(self, code, err):
        fatal = re.search(r"^fatal error: (.+)$", err, re.MULTILINE)
        if fatal:
            text = fatal.group(1).strip()
            if "stack overflow" in text:
                limit = re.search(r"goroutine stack exceeds (\d+)-byte limit", err)
                size = f"{int(limit.group(1)) >> 20} MB" if limit else "its"
                return "stack overflow", f"the goroutine stack outgrew {size} limit (unbounded recursion?)"
            if "deadlock" in text:
                return "deadlock", text
            return ("out of memory" if "out of memory" in text else text), text
        panic = re.search(r"^panic: (.+)$", err, re.MULTILINE)  # a panic in another goroutine: not recoverable
        if panic:
            text = re.sub(r"\s*\[recovered.*$", "", panic.group(1)).strip()
            known = text.startswith("runtime error: ") or any(text.startswith(p) for p, _ in RUNTIME_NAMES)
            return (runtime_name(text) if known else "panic"), text
        if 0 < code < 124 and not re.search(r"^goroutine \d+ \[", err, re.MULTILINE):
            # no panic, no fatal error, no goroutine dump (whose panic line may be cut off): os.Exit / log.Fatal
            first = next((ln.strip() for ln in err.splitlines() if ln.strip()), "")
            return f"os.Exit({code})", first[:300] or f"the program called os.Exit({code})"
        return super().crash_error(code, err)

    def trace_line(self, text):
        """Innermost main.go frame; for a stack overflow, the recursive call (the most repeated frame)."""
        lines = [int(n) for n in USER_FRAME.findall(text or "")]
        if not lines:
            return None
        if "goroutine stack exceeds" in text:
            return Counter(lines).most_common(1)[0][0]
        return lines[0]


def runtime_name(message):
    """A stable name for a runtime panic: 'runtime error: index out of range [3] with length 3' -> 'index out of range'."""
    text = message.removeprefix("runtime error: ")
    for prefix, name in RUNTIME_NAMES:
        if text.startswith(prefix):
            return name
    return re.split(r"\s*[\[:]", text, maxsplit=1)[0].strip() or "runtime error"
