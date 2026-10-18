"""Java: static methods of the file's classes (interfaces, enums and records too, nested types included),
compiled once with javac next to a small harness class and run in a fresh JVM per input (Temurin 25).

A method is called "Class.method" ("Outer.Inner.method" when nested). The user's file keeps its line
numbers: only the `package` line is blanked, so every class lands in the default package next to the
harness. Private methods (or methods of a private nested type) are called through reflection.

Each argument is assigned to a local of the parameter's declared type before the call, so a byte/short
constant compiles, overloads resolve to the declared signature, and a model input of the wrong type
fails the build (Specter then drops it). Building an argument that throws (List.of(null), duplicate
Map.of keys) is reported as "bad_input", never as a bug. A watchdog thread samples the main thread
before the time limit, so an infinite loop is reported at the line of the loop it is stuck in.
Collections are passed as mutable copies (new ArrayList<>(List.of(...))), so a method that sorts its
argument isn't "buggy".
"""
import re
from dataclasses import dataclass

from runner.languages.base import MAX_INPUTS, RESULT_MARK, Function, Input, Language, Param, json_string

HARNESS = "PhantomHarness"
CALL_MARK = "@@PHANTOM-CALL"  # printed just before the call: no mark = the JVM never got that far
WATCHDOG = 1.5  # seconds of running time before an input counts as hanging (call_timeout leaves room for JVM start)
JVM = "-XX:TieredStopAtLevel=1 -XX:+UseSerialGC -Xshare:auto -XX:-UsePerfData -XX:-OmitStackTraceInFastThrow"
RUN = f"java {JVM} -Xmx256m -Xss1m -cp . {HARNESS} {{i}}"
JAVAC = " ".join(f"-J{flag}" for flag in JVM.split()) + " -XDstringConcat=inline -proc:none -nowarn -g -encoding UTF-8"

SCALARS = {  # type: (typical value then boundary values, three collection elements, class holding MAX/MIN_VALUE)
    "int": (["3", "0", "1", "-1"], ["1", "2", "3"], "Integer"),
    "long": (["3L", "0L", "1L", "-1L"], ["1L", "2L", "3L"], "Long"),
    "short": (["3", "0", "1", "-1"], ["1", "2", "3"], "Short"),
    "byte": (["3", "0", "1", "-1"], ["1", "2", "3"], "Byte"),
    "double": (["1.5", "0.0", "-1.0"], ["1.5", "2.0", "3.0"], None),
    "float": (["1.5f", "0.0f", "-1.0f"], ["1.5f", "2.0f", "3.0f"], None),
    "boolean": (["true", "false"], ["true", "false", "true"], None),
    "char": (["'a'", "' '"], ["'a'", "'b'", "'c'"], None),
    "String": (['"abc"', '""', '" "', '"a:b"'], ['"abc"', '"de"', '"f"'], None),
}
BOXED = {"Integer": "int", "Long": "long", "Short": "short", "Byte": "byte", "Double": "double", "Float": "float",
         "Boolean": "boolean", "Character": "char", "String": "String", "CharSequence": "String"}
LISTS = {"List": "ArrayList", "ArrayList": "ArrayList", "LinkedList": "LinkedList", "Collection": "ArrayList",
         "Iterable": "ArrayList", "Queue": "ArrayDeque", "Deque": "ArrayDeque", "ArrayDeque": "ArrayDeque"}
SETS = {"Set": "HashSet", "HashSet": "HashSet", "LinkedHashSet": "LinkedHashSet", "SortedSet": "TreeSet",
        "NavigableSet": "TreeSet", "TreeSet": "TreeSet"}
MAPS = {"Map": "HashMap", "HashMap": "HashMap", "LinkedHashMap": "LinkedHashMap", "SortedMap": "TreeMap",
        "NavigableMap": "TreeMap", "TreeMap": "TreeMap"}
INDEXED = ("List", "ArrayList", "LinkedList")  # lists whose elements have positions (pair with index parameters)
INDEX_WORDS = ("i", "j", "index", "idx", "pos", "position", "offset", "start", "end", "from", "to", "begin", "at")
COUNT_WORDS = ("n", "count", "len", "length", "size", "num")
INT_TYPES = ("int", "long", "short", "Integer", "Long", "Short")
PRIMITIVES = ("int", "long", "short", "byte", "double", "float", "boolean", "char")

NUMBERS = frozenset({"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
                     "binary_integer_literal", "decimal_floating_point_literal", "hex_floating_point_literal"})
ARRAY_TYPES = frozenset({"String", "Object", *BOXED})
COLLECTIONS = frozenset({*LISTS.values(), *SETS.values(), *MAPS.values()})
LOOPS = ("while_statement", "for_statement", "enhanced_for_statement", "do_statement")
TYPE_DECLARATIONS = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
NULLABLE = re.compile(r"@(?:\w+\.)*(?:Nullable|CheckForNull)\b")
NON_NULL = re.compile(r"@(?:\w+\.)*(?:NonNull|Nonnull|NotNull)\b")
ANNOTATION = re.compile(r"@[\w.]+(?:\s*\([^)]*\))?\s*")
FRAME = re.compile(r"^\s*at ([\w$]+)\.[\w$<>]+\(([\w$]+)\.java:(\d+)\)", re.MULTILINE)


@dataclass
class JavaMethod(Function):
    owner: str = ""  # binary name of the declaring type: "Stats", "Stats$Inner"
    private: bool = False  # private, or inside a private type: called through reflection
    non_null: bool = False  # annotated @NonNull/@Nonnull/@NotNull: returning null is a bug


@dataclass
class Value:
    expr: str
    assumption: str | None = None
    size: int | None = None  # elements (or characters) in a collection, array or string value
    kind: str = "scalar"  # "array", "list" (indexed), "collection", "map", "string" or "scalar"


class Java(Language):
    name = "java"
    title = "Java"
    vscode_ids = ("java",)
    extensions = (".java",)
    grammar = "java"
    image = "eclipse-temurin:25-jdk"
    memory = "1g"  # javac and the JVM; each run caps its own heap at 256 MB
    local_tool = "javac"
    build_timeout = 60.0
    call_timeout = 4  # JVM start is ~0.1 s idle and well under 1 s under load; the watchdog stops hangs at 1.5 s
    user_file = "Main"  # only when the file declares no type; otherwise the file is named after its (public) class
    null_words = ("null",)
    literal_hint = ("Java literals: numbers (3, -1, 2L, 1.5, 0x1F), \"strings\", 'c', true/false, null, arrays like "
                    "new int[]{1, 2} or new String[]{}, List.of(...), Set.of(...), Map.of(k1, v1, ...), "
                    "Arrays.asList(...), new ArrayList<>(List.of(...)), new HashMap<>(Map.of(...)), and constants "
                    "such as Integer.MAX_VALUE, Long.MIN_VALUE, Double.NaN")
    literal_nodes = NUMBERS | {"true", "false", "character_literal", "string_literal", "string_fragment",
                               "escape_sequence", "null_literal", "argument_list"}
    literal_calls = frozenset({"List.of", "Set.of", "Map.of", "Arrays.asList"})
    literal_names = frozenset(
        [f"{t}.{c}" for t in ("Integer", "Long", "Short", "Byte", "Character") for c in ("MAX_VALUE", "MIN_VALUE")]
        + [f"{t}.{c}" for t in ("Double", "Float")
           for c in ("MAX_VALUE", "MIN_VALUE", "NaN", "POSITIVE_INFINITY", "NEGATIVE_INFINITY")])

    # ---------- functions ----------

    def functions(self, source):
        found = []
        for node in self.parse(source).root_node.named_children:
            if node.type in TYPE_DECLARATIONS:
                self._collect(node, [], False, found)
            elif node.type == "method_declaration":  # Java 25 compact source file: its class has no usable name
                fn = self._method(node, [], False)
                if fn is not None:
                    fn.skip = "methods outside a class (compact source file) can't be called from another class"
                    found.append(fn)
        seen = set()
        for fn in found:
            if fn.name in seen:
                fn.skip = fn.skip or f"overloaded: only the first {fn.name} is run"
            seen.add(fn.name)
        return found

    def _collect(self, node, outer, outer_private, found):
        names = outer + [self.text(node.child_by_field_name("name"))]
        private = outer_private or "private" in self._modifiers(node)
        body = node.child_by_field_name("body")
        members = body.named_children if body is not None else []
        for member in list(members):
            if member.type == "enum_body_declarations":
                members += member.named_children
        for member in members:
            if member.type in TYPE_DECLARATIONS:
                self._collect(member, names, private, found)
            elif member.type in ("method_declaration", "constructor_declaration", "compact_constructor_declaration"):
                fn = self._method(member, names, private)
                if fn is not None:
                    found.append(fn)

    def _modifiers(self, node):
        mods = next((c for c in node.named_children if c.type == "modifiers"), None)
        return [self.text(c) for c in mods.children] if mods is not None else []

    def _method(self, node, names, private):
        name_node = node.child_by_field_name("name")
        body = node.child_by_field_name("body")
        if name_node is None or body is None:
            return None  # abstract and native methods have no code to run
        mods = self._modifiers(node)
        params, skip = [], None
        holder = node.child_by_field_name("parameters")
        for child in holder.named_children if holder is not None else []:
            if child.type == "formal_parameter":
                kind, dims = child.child_by_field_name("type"), child.child_by_field_name("dimensions")
                text = self.text(kind) + (self.text(dims) if dims is not None else "")  # int values[]
                notes = [m for m in self._modifiers(child) if NULLABLE.fullmatch(m)]  # kept: they allow null
                params.append(Param(self.text(child.child_by_field_name("name")), " ".join(notes[:1] + [text])))
            elif child.type == "spread_parameter":
                skip = "varargs methods are not run yet"
                declarator = next((c for c in child.named_children if c.type == "variable_declarator"), None)
                kind = next((c for c in child.named_children
                             if c.type not in ("modifiers", "variable_declarator")), None)
                name = declarator.child_by_field_name("name") if declarator is not None else None
                params.append(Param(self.text(name) or "args", f"{self.text(kind)}..."))
        returns = node.child_by_field_name("type")
        method = self.text(name_node)
        if node.type != "method_declaration":
            skip = "constructors are not run yet"
        elif "static" not in mods:
            skip = "instance methods are not run yet"
        elif node.child_by_field_name("type_parameters") is not None:
            skip = "generic methods are not run yet"
        elif method == "main" and not skip:
            skip = "main is the program's entry point: it isn't run"
        return JavaMethod(
            name=".".join(names + [method]), line=name_node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            params=params, returns=self.text(returns), text=self.text(node), skip=skip,
            owner="$".join(names), private=private or "private" in mods,
            non_null=any(NON_NULL.fullmatch(m) for m in mods))

    def context(self, source, fn):
        """The file without its `package` line (blanked, so line numbers stay): it joins the harness's package."""
        kinds = {node.type for node in self.parse(source).root_node.named_children} - {"package_declaration"}
        return self.blank_top_level(source, kinds)

    def file_class(self, source):
        """The class the user's file must be named after: the public top-level type, else the first one."""
        types = [n for n in self.parse(source).root_node.named_children
                 if n.type in TYPE_DECLARATIONS + ("annotation_type_declaration",)]
        public = [n for n in types if "public" in self._modifiers(n)]
        chosen = (public or types or [None])[0]
        return self.text(chosen.child_by_field_name("name")) if chosen is not None else self.user_file

    # ---------- literal checking ----------

    def expression_tree(self, expr):
        tree = self.parse(f"class __P {{ void __m() {{ __x = {expr}; }} }}")
        try:
            method = tree.root_node.named_children[0].child_by_field_name("body").named_children[0]
            statement = method.child_by_field_name("body").named_children[0]
            return tree, statement.named_children[0].child_by_field_name("right")
        except (AttributeError, IndexError):
            return tree, None

    def literal_ok(self, expr):
        # javac turns \u0022 into a quote before it reads a string, so "\u0022+System.exit(1)+\u0022" is
        # a string to tree-sitter but code to javac: no unicode escapes at all.
        return "\\u" not in (expr or "") and super().literal_ok(expr)

    def _literal_node(self, node):
        if node.type == "unary_expression":  # only a sign in front of a number: no !, ~
            operator, operand = node.child_by_field_name("operator"), node.child_by_field_name("operand")
            return self.text(operator) in ("-", "+") and operand is not None and \
                (operand.type in NUMBERS or self.text(operand) in self.literal_names)
        if node.type == "array_creation_expression":
            return self._array_ok(node)
        if node.type == "object_creation_expression":
            return self._collection_ok(node)
        return super()._literal_node(node)

    def _array_ok(self, node):
        """new int[]{...} / new String[][]{{...}}: an initializer is required (new int[1 << 30] is not a literal)."""
        kind, dims, value = (node.child_by_field_name(f) for f in ("type", "dimensions", "value"))
        if None in (kind, dims, value) or [c.id for c in node.named_children] != [kind.id, dims.id, value.id]:
            return False
        if dims.type != "dimensions" or dims.named_children:
            return False
        if kind.type not in ("integral_type", "floating_point_type", "boolean_type") and \
                not (kind.type == "type_identifier" and self.text(kind) in ARRAY_TYPES):
            return False
        return self._initializer_ok(value)

    def _initializer_ok(self, node):
        return node.type == "array_initializer" and all(
            self._initializer_ok(c) if c.type == "array_initializer" else self._literal_node(c)
            for c in node.named_children)

    def _collection_ok(self, node):
        """new ArrayList<>(List.of(...)): a JDK collection, copied from at most one collection literal.
        No capacity numbers (memory), no anonymous class bodies (code), no outer instances."""
        kind, args = node.child_by_field_name("type"), node.child_by_field_name("arguments")
        if kind is None or args is None or [c.id for c in node.named_children] != [kind.id, args.id]:
            return False
        if kind.type == "generic_type" and not self._plain_type(kind):
            return False
        base = kind.named_children[0] if kind.type == "generic_type" else kind
        if base.type != "type_identifier" or self.text(base) not in COLLECTIONS:
            return False
        items = args.named_children
        return len(items) <= 1 and all(c.type == "method_invocation" and self._literal_node(c) for c in items)

    def _plain_type(self, node):
        if node.type == "type_identifier":
            return True
        return node.type in ("generic_type", "type_arguments") and all(self._plain_type(c) for c in node.named_children)

    # ---------- inputs ----------

    def nullable(self, type_text):
        return bool(type_text) and bool(NULLABLE.search(type_text))

    def edge_values(self, type_text, param_name):
        return [(v.expr, v.assumption) for v in self._options(type_text, param_name)]

    def _options(self, type_text, param_name):
        decl = declared(type_text)
        values = self._typed(decl, param_name) if decl and not decl.endswith("...") else []
        if values and decl not in PRIMITIVES:
            values.append(Value("null", None if self.nullable(type_text) else f"{param_name} may be null"))
        return values

    def _typed(self, decl, param_name):
        name, args, dims = shape(decl)
        if dims:
            items = self._items(decl[:decl.rindex("[")].strip(), True)
            if items is None:
                return []
            samples, single = items
            new = f"new {decl}"
            return [Value(f"{new}{{{', '.join(samples)}}}", None, len(samples), "array"),
                    Value(f"{new}{{}}", None, 0, "array"), Value(f"{new}{{{single}}}", None, 1, "array")]
        scalar = None if args else name if name in SCALARS else BOXED.get(name)
        if scalar is not None:
            values, _, holder = SCALARS[scalar]
            kind = "string" if scalar == "String" else "scalar"
            found = [Value(v, None, len(v) - 2 if kind == "string" else None, kind) for v in values]
            if holder:
                found += [Value(f"{holder}.MAX_VALUE", f"{param_name} may be as large as {holder}.MAX_VALUE"),
                          Value(f"{holder}.MIN_VALUE", f"{param_name} may be as small as {holder}.MIN_VALUE")]
            return found
        concrete = LISTS.get(name) or SETS.get(name)
        if concrete and len(args) == 1:
            items = self._items(args[0], False)
            if items is None or (name in SETS and BOXED.get(shape(args[0])[0]) is None):
                return []  # set elements: plain values only (equal or incomparable elements would fail to build)
            samples, single = items
            kind = "list" if name in INDEXED else "collection"
            return [Value(f"new {concrete}<>(List.of({', '.join(samples)}))", None, len(samples), kind),
                    Value(f"new {concrete}<>()", None, 0, kind),
                    Value(f"new {concrete}<>(List.of({single}))", None, 1, kind)]
        if name in MAPS and len(args) == 2:
            keys, items = self._items(args[0], False), self._items(args[1], False)
            if keys is None or items is None or BOXED.get(shape(args[0])[0]) is None:
                return []
            concrete = MAPS[name]
            pairs = f"{keys[0][0]}, {items[0][0]}, {keys[0][1]}, {items[0][1]}"
            return [Value(f"new {concrete}<>(Map.of({pairs}))", None, 2, "map"),
                    Value(f"new {concrete}<>()", None, 0, "map"),
                    Value(f"new {concrete}<>(Map.of({keys[1]}, {items[1]}))", None, 1, "map")]
        return []  # classes, interfaces, wildcards, type variables: their values can't be written as literals

    def _items(self, decl, in_array):
        """(three element expressions, one boundary element) for a collection or array of `decl`, or None.
        Inside an array initializer, arrays are written {...}; inside List.of(...) they need new T[]{...}."""
        name, args, dims = shape(decl)
        if dims:
            inner = self._items(decl[:decl.rindex("[")].strip(), True)
            if inner is None:
                return None
            prefix = "" if in_array else f"new {decl}"
            row = f"{prefix}{{{', '.join(inner[0])}}}"
            return [row] * 3, f"{prefix}{{}}"  # rectangular: 3 x 3, and one empty row
        if not args and (name in BOXED or in_array and name in SCALARS):
            scalar = BOXED.get(name, name)
            if not in_array and scalar in ("short", "byte"):
                return None  # List.of(1) is a List<Integer>: no literal makes a List<Short>
            values, samples, _ = SCALARS[scalar]
            return samples, values[1]
        if not in_array and name in LISTS and len(args) == 1:
            inner = self._items(args[0], False)
            return ([f"List.of({', '.join(inner[0])})"] * 3, "List.of()") if inner is not None else None
        return None

    def _role(self, name, type_text):
        """"index" or "count" when an integer parameter's name says it refers to a collection parameter."""
        if not type_text or declared(type_text) not in INT_TYPES:
            return None
        last = re.split(r"_|(?<=[a-z0-9])(?=[A-Z])", name)[-1].lower()
        return "index" if last in INDEX_WORDS else "count" if last in COUNT_WORDS else None

    def _tied(self, param, role, target, size, order):
        """Values of an index/count parameter that agree with the size of the collection it refers to."""
        suffix = "L" if declared(param.type) in ("long", "Long") else ""
        if role == "count":  # values[0..n) must exist: n = its length, or 0
            return [Value(f"{n}{suffix}") for n in dict.fromkeys((size, 0))]
        if size == 0:
            return [Value(f"0{suffix}", f"{param.name} may be 0 when {target} is empty")]
        first, other = (0, size - 1) if order == 0 else (size - 1, 0)  # start <= end for (start, end) pairs
        return [Value(f"{n}{suffix}") for n in dict.fromkeys((first, other))] + [
            Value(f"-1{suffix}", f"{param.name} may be -1, outside {target}"),
            Value(f"{size}{suffix}", f"{param.name} may be {size}, past the end of {target}")]

    def edge_inputs(self, fn):
        """A typical input, then one parameter at a time at its boundary values. An index or count
        parameter (i, index, start, n, count, ...) next to an array/list/string follows that value's size,
        so `get(values, i)` never gets i = 3 with a 1-element array unless that is named as an assumption."""
        params = fn.params
        options = [self._options(p.type, p.name) for p in params]
        if any(not values for values in options):
            return []
        ties = {}
        for index, param in enumerate(params):
            role = self._role(param.name, param.type)
            kinds = ("array", "list", "string") if role == "index" else ("array", "list")
            target = next((j for j, values in enumerate(options) if j != index and values[0].kind in kinds), None)
            if role and target is not None:
                order = sum(1 for r, t, _ in ties.values() if (r, t) == ("index", target))
                ties[index] = (role, target, order)

        def follow(row):
            for index, (role, target, order) in ties.items():
                if row[target].size is not None:
                    row[index] = self._tied(params[index], role, params[target].name, row[target].size, order)[0]
            return row

        base = follow([values[0] for values in options])
        rows = [base]
        for index in range(len(params)):
            if index in ties:
                role, target, order = ties[index]
                others = self._tied(params[index], role, params[target].name, base[target].size, order)[1:]
            else:
                others = options[index][1:]
            for value in others:
                row = list(base)
                row[index] = value
                rows.append(row if index in ties else follow(row))
        inputs = {}
        for row in rows:
            assumptions = [v.assumption for v in row if v.assumption]
            sequences = [(p.name, v.size) for p, v in zip(params, row)
                         if v.kind in ("array", "list") and v.size is not None]
            if len({size for _, size in sequences}) > 1:
                assumptions.append(f"{' and '.join(name for name, _ in sequences)} may have different lengths")
            inputs.setdefault(tuple(v.expr for v in row), Input([v.expr for v in row], assumptions=assumptions))
        return list(inputs.values())[:MAX_INPUTS]

    def model_input(self, fn, args, hypothesis):
        if len(args) != len(fn.params) or any(not self._options(p.type, p.name) for p in fn.params):
            return None  # a type no edge value can be built for can't be declared in the harness either
        made = super().model_input(fn, args, hypothesis)
        if made is None:
            return None
        for index, (param, arg) in enumerate(zip(fn.params, made.args)):
            nodes = list(walk(self.expression_tree(arg)[1]))
            if arg != "null" and any(n.type == "null_literal" for n in nodes) and not self.nullable(param.type):
                made.assumptions.append(f"{param.name} may contain null")
            extreme = next((self.text(n) for n in nodes if self._extreme(n)), None)
            if extreme:
                size = "small" if "MIN_VALUE" in extreme or extreme.startswith("-") else "large"
                made.assumptions.append(f"{param.name} may be as {size} as {extreme}")
            made.args[index] = self._mutable(param.type, arg)
        return made

    def _extreme(self, node):
        """Integer.MAX_VALUE and friends, or an integer literal at the int/long limits: overflow is often
        outside a method's contract, so such inputs carry an assumption."""
        if node.type == "unary_expression":
            return self._extreme(node.child_by_field_name("operand"))
        if node.type == "field_access":
            text = self.text(node)
            return text.endswith(("MAX_VALUE", "MIN_VALUE")) and not text.startswith(("Double", "Float"))
        if node.type in NUMBERS and "floating" not in node.type:
            digits = self.text(node).replace("_", "").rstrip("lL")
            try:
                value = int(digits, 0) if not re.fullmatch(r"0\d+", digits) else int(digits, 8)
            except ValueError:
                return False
            return value >= 2 ** 31 - 1
        return False

    def _mutable(self, type_text, arg):
        """List.of(...) for a List parameter becomes new ArrayList<>(List.of(...)): an immutable argument
        would make any method that sorts or adds to its list look broken."""
        name, _, dims = shape(declared(type_text))
        concrete = LISTS.get(name) or SETS.get(name) or MAPS.get(name)
        call = re.match(r"(List\.of|Set\.of|Arrays\.asList|Map\.of)\(", arg)
        if dims or not concrete or not call or (call.group(1) == "Map.of") != (name in MAPS):
            return arg
        return f"new {concrete}<>({arg})"

    # ---------- running ----------

    def program(self, source, fn, inputs):
        user = self.file_class(source)
        owner = getattr(fn, "owner", "") or fn.name.rsplit(".", 1)[0].replace(".", "$")
        cases = "\n".join(self._case(fn, index, inp, owner) for index, inp in enumerate(inputs))
        loops = ",".join(f"{n.start_point[0] + 1}-{n.end_point[0] + 1}"
                         for n in walk(self.parse(source).root_node) if n.type in LOOPS)
        values = {"FILE": json_string(f"{user}.java"), "LIMIT": str(int(WATCHDOG * 1000)), "LOOPS": json_string(loops),
                  "TIMEOUT": json_string(f"no result after {WATCHDOG:g} s (possible infinite loop)"),
                  "NONNULL": "true" if getattr(fn, "non_null", False) else "false",
                  "MARK": json_string(f"\n{CALL_MARK}\n"), "RESULT": json_string(f"\n{RESULT_MARK}"), "CASES": cases}
        harness = re.sub(r"__([A-Z]+)__", lambda m: values[m.group(1)], TEMPLATE)  # one pass: inputs aren't rescanned
        return {"files": {f"{user}.java": source, f"{HARNESS}.java": harness},
                "build": f"javac {JAVAC} -d . '{user}.java' {HARNESS}.java", "run": RUN}

    def _case(self, fn, index, inp, owner):
        names = [f"a{k}" for k in range(len(fn.params))]
        lines = [f"            case {index}: {{"]
        if names:
            lines.append("                " + " ".join(f"{declared(p.type)} {n};" for p, n in zip(fn.params, names)))
            lines.append("                try { " + " ".join(f"{n} = {a};" for n, a in zip(names, inp.args))
                         + " } catch (Throwable e) { throw new BadInput(e); }")
        if getattr(fn, "private", False):
            classes = ", ".join(f"{erase(p.type)}.class" for p in fn.params)
            call = (f"invoke({json_string(owner)}, {json_string(fn.name.rsplit('.', 1)[-1])}, "
                    f"new Class<?>[]{{{classes}}}, new Object[]{{{', '.join(names)}}})")
        else:
            call = f"{fn.name}({', '.join(names)})"
        lines.append(" " * 16 + (f"{call}; return VOID;" if fn.returns == "void" else f"return {call};"))
        return "\n".join(lines + ["            }"])

    def classify(self, code, out, err):
        result = super().classify(code, out, err)
        if CALL_MARK not in out and result.get("kind") in ("timeout", "crash"):
            # the JVM never reached the call (failed to start, or the machine is too busy): not the code's fault
            first = next((ln.strip() for ln in err.splitlines() if ln.strip()), "")
            return {"ok": False, "kind": "sandbox_error", "error": "SandboxError",
                    "message": f"the JVM did not reach the call: {first[:300] or f'exit {code}'}"}
        return result

    def crash_error(self, code, err):
        """Reached the call, then died without a verdict: the JVM crashed, or the code ended the process."""
        if code == 134 or "fatal error has been detected" in err:
            first = next((ln.strip("# ").strip() for ln in err.splitlines() if ln.strip("# ").strip()), "")
            return "JVMCrash", first[:300] or "the JVM aborted"
        return "SystemExit", f"the code ended the JVM with exit code {code} (System.exit or Runtime.halt)"

    def trace_line(self, text):
        """The innermost frame of a class in the default package (the user's file), not the harness."""
        for _, file, line in FRAME.findall(text or ""):
            if file != HARNESS:
                return int(line)
        return None


def declared(type_text):
    """The type as the harness declares it: annotations dropped, spacing normalised."""
    text = ANNOTATION.sub("", type_text or "")
    return re.sub(r"\s*([<>,\[\]])\s*", r"\1", re.sub(r"\s+", " ", text)).replace(",", ", ").strip()


def shape(decl):
    """'Map<String, List<Integer>>[]' -> ('Map', ['String', 'List<Integer>'], 1)."""
    text, dims = decl.replace(" ", ""), 0
    while text.endswith("[]"):
        text, dims = text[:-2], dims + 1
    args = []
    if text.endswith(">") and "<" in text:
        text, inner = text.split("<", 1)
        args = split_args(inner[:-1])
    for prefix in ("java.util.", "java.lang."):
        text = text[len(prefix):] if text.startswith(prefix) else text
    return text, args, dims


def split_args(text):
    """'String,List<Integer>' -> ['String', 'List<Integer>'], ignoring commas inside <...>."""
    parts, depth, current = [], 0, ""
    for ch in text:
        depth += (ch == "<") - (ch == ">")
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [p for p in parts + [current] if p]


def erase(type_text):
    """The class literal's type for reflection: List<Integer> -> List, int[][] stays."""
    text = declared(type_text)
    while "<" in text:
        text = re.sub(r"<[^<>]*>", "", text)
    return text


def walk(node):
    if node is not None:
        yield node
        for child in node.children:
            yield from walk(child)


TEMPLATE = r"""import java.util.*; // for the inputs; the harness itself names java.util types in full

public final class PhantomHarness {
    static final String USER_FILE = __FILE__;
    static final long LIMIT_MS = __LIMIT__;
    static final boolean NON_NULL = __NONNULL__;
    static final String LOOPS = __LOOPS__; // first-last line of every loop in the user's file
    static final java.io.PrintStream OUT = System.out;
    static final Object VOID = new Object();
    static boolean reported;

    public static void main(String[] args) {
        int index = Integer.parseInt(args[0]);
        System.setOut(new java.io.PrintStream(java.io.OutputStream.nullOutputStream()));
        new Watchdog(Thread.currentThread()).start();
        OUT.print(__MARK__);
        OUT.flush();
        String verdict;
        try {
            Object result = call(index);
            if (NON_NULL && result == null) {
                verdict = "{\"ok\":false,\"kind\":\"return_type\",\"error\":\"ReturnTypeError\",\"message\":"
                    + q("returned null, but the method is annotated as never returning null") + "}";
            } else {
                verdict = "{\"ok\":true,\"result\":" + q(show(result)) + "}";
            }
        } catch (BadInput e) {
            verdict = "{\"ok\":false,\"kind\":\"bad_input\",\"error\":" + q(name(e.getCause()))
                + ",\"message\":" + q(clip("building the input failed: " + e.getCause(), 1000)) + "}";
        } catch (Throwable e) {
            int line = userLine(e);
            verdict = "{\"ok\":false,\"kind\":\"exception\",\"error\":" + q(name(e))
                + ",\"message\":" + q(clip(e.getMessage() == null ? "" : e.getMessage(), 1000))
                + ",\"trace\":" + q(trace(e)) + (line > 0 ? ",\"line\":" + line : "") + "}";
        }
        report(verdict);
    }

    static Object call(int index) throws Throwable {
        switch (index) {
__CASES__
            default: throw new BadInput(new IllegalArgumentException("no input " + index));
        }
    }

    static final class BadInput extends RuntimeException {
        BadInput(Throwable cause) { super(cause); }
    }

    static Object invoke(String owner, String method, Class<?>[] types, Object[] values) throws Throwable {
        java.lang.reflect.Method m;
        try {
            m = Class.forName(owner, false, PhantomHarness.class.getClassLoader()).getDeclaredMethod(method, types);
            m.setAccessible(true);
        } catch (Throwable e) {
            throw new BadInput(e);
        }
        try {
            return m.invoke(null, values);
        } catch (java.lang.reflect.InvocationTargetException e) {
            throw e.getCause();
        }
    }

    static synchronized void report(String verdict) {
        if (reported) return;
        reported = true;
        OUT.print(__RESULT__ + verdict + "\n");
        OUT.flush();
        Runtime.getRuntime().halt(0);
    }

    static final class Watchdog extends Thread {
        final Thread target;

        Watchdog(Thread target) {
            super("phantom-watchdog");
            this.target = target;
            setDaemon(true);
        }

        @Override
        public void run() {
            try {
                Thread.sleep(LIMIT_MS - 200);
                StackTraceElement[][] samples = new StackTraceElement[5][];
                for (int k = 0; k < samples.length; k++) {
                    samples[k] = target.getStackTrace();
                    Thread.sleep(40);
                }
                StringBuilder b = new StringBuilder("main thread when the time ran out:\n");
                StackTraceElement[] last = samples[samples.length - 1];
                for (int k = 0; k < Math.min(last.length, 30); k++) b.append("\tat ").append(last[k]).append('\n');
                int line = loopLine(samples);
                report("{\"ok\":false,\"kind\":\"timeout\",\"error\":\"Timeout\",\"message\":" + q(__TIMEOUT__)
                    + ",\"trace\":" + q(b.toString()) + (line > 0 ? ",\"line\":" + line : "") + "}");
            } catch (Throwable e) {
                // interrupted: nothing to report
            }
        }
    }

    // The line to blame for a hang: in the deepest user frame that every sample shares (the method that
    // loops, not the helpers it calls), the first line of the innermost loop around every sampled line.
    static int loopLine(StackTraceElement[][] samples) {
        java.util.List<java.util.List<StackTraceElement>> stacks = new java.util.ArrayList<>();
        for (StackTraceElement[] sample : samples) {
            java.util.List<StackTraceElement> frames = new java.util.ArrayList<>();
            for (int k = sample.length - 1; k >= 0; k--) if (user(sample[k])) frames.add(sample[k]);
            stacks.add(frames);
        }
        int depth = 0;
        search:
        while (true) {
            for (java.util.List<StackTraceElement> frames : stacks) {
                if (frames.size() <= depth) break search;
                StackTraceElement a = frames.get(depth), b = stacks.get(0).get(depth);
                boolean same = a.getClassName().equals(b.getClassName()) && a.getMethodName().equals(b.getMethodName());
                if (!same) break search;
            }
            depth++;
        }
        if (depth == 0) return -1;
        int low = Integer.MAX_VALUE, high = 0, best = -1, width = Integer.MAX_VALUE;
        for (java.util.List<StackTraceElement> frames : stacks) {
            low = Math.min(low, frames.get(depth - 1).getLineNumber());
            high = Math.max(high, frames.get(depth - 1).getLineNumber());
        }
        for (String range : LOOPS.split(",")) {
            int dash = range.indexOf('-');
            if (dash < 0) continue;
            int first = Integer.parseInt(range.substring(0, dash)), last = Integer.parseInt(range.substring(dash + 1));
            if (first <= low && high <= last && last - first < width) {
                best = first;
                width = last - first;
            }
        }
        return best > 0 ? best : low > 0 && low < Integer.MAX_VALUE ? low : -1;
    }

    static boolean user(StackTraceElement frame) {
        return USER_FILE.equals(frame.getFileName()) && frame.getClassName().indexOf('.') < 0;
    }

    static int userLine(Throwable e) {
        if (e instanceof StackOverflowError) {
            // the top frame lands on the call or on the callee's first line at random: blame the recursive call,
            // the user line most of the 1024 recorded frames are at
            java.util.Map<Integer, Integer> counts = new java.util.HashMap<>();
            int best = -1;
            for (StackTraceElement frame : e.getStackTrace()) {
                if (!user(frame) || frame.getLineNumber() <= 0) continue;
                int seen = counts.merge(frame.getLineNumber(), 1, Integer::sum);
                if (best < 0 || seen > counts.get(best)) best = frame.getLineNumber();
            }
            if (best > 0) return best;
        }
        int depth = 0;
        for (Throwable t = e; t != null && depth < 5; t = t.getCause(), depth++) {
            for (StackTraceElement frame : t.getStackTrace()) {
                if (user(frame) && frame.getLineNumber() > 0) return frame.getLineNumber();
            }
        }
        return -1;
    }

    static String trace(Throwable e) {
        StringBuilder b = new StringBuilder();
        int depth = 0;
        for (Throwable t = e; t != null && depth < 4; t = t.getCause(), depth++) {
            if (depth > 0) b.append("Caused by: ");
            b.append(t.getClass().getName());
            if (t.getMessage() != null) b.append(": ").append(clip(t.getMessage(), 300));
            b.append('\n');
            StackTraceElement[] frames = t.getStackTrace();
            int shown = Math.min(frames.length, 30);
            for (int k = 0; k < shown; k++) b.append("\tat ").append(frames[k]).append('\n');
            if (frames.length > shown) b.append("\t... ").append(frames.length - shown).append(" more\n");
        }
        return b.toString();
    }

    static String name(Throwable e) {
        String simple = e.getClass().getSimpleName();
        return simple.isEmpty() ? e.getClass().getName() : simple;
    }

    static String show(Object v) {
        try {
            String s;
            if (v == VOID) s = "void";
            else if (v == null) s = "null";
            else if (v instanceof String) s = quote((String) v, false);
            else if (v instanceof Character) s = "'" + v + "'";
            else if (v instanceof Object[]) s = java.util.Arrays.deepToString((Object[]) v);
            else if (v instanceof int[]) s = java.util.Arrays.toString((int[]) v);
            else if (v instanceof long[]) s = java.util.Arrays.toString((long[]) v);
            else if (v instanceof double[]) s = java.util.Arrays.toString((double[]) v);
            else if (v instanceof float[]) s = java.util.Arrays.toString((float[]) v);
            else if (v instanceof short[]) s = java.util.Arrays.toString((short[]) v);
            else if (v instanceof byte[]) s = java.util.Arrays.toString((byte[]) v);
            else if (v instanceof char[]) s = java.util.Arrays.toString((char[]) v);
            else if (v instanceof boolean[]) s = java.util.Arrays.toString((boolean[]) v);
            else s = String.valueOf(v);
            return clip(s, 300);
        } catch (Throwable e) {
            return "<" + v.getClass().getName() + ">";
        }
    }

    static String clip(String s, int max) {
        return s.length() > max ? s.substring(0, max - 3) + "..." : s;
    }

    static String q(String s) {
        return quote(s, true); // pure ASCII on stdout, whatever the container's locale
    }

    static String quote(String s, boolean ascii) {
        StringBuilder b = new StringBuilder("\"");
        for (int k = 0; k < s.length(); k++) {
            char c = s.charAt(k);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else if (c == '\t') b.append("\\t");
            else if (c < 0x20 || c == 0x7f || ascii && c > 0x7e) b.append(String.format("\\%s%04x", "u", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }
}
"""
