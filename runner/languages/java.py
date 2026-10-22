"""Java: static methods of the file's classes (interfaces, enums and records too, nested types included),
compiled once with javac next to a small harness class and run in a fresh JVM per input (Temurin 25).

A method is called "Class.method" ("Outer.Inner.method" when nested). The user's file keeps its line
numbers: its `package` line is blanked, so every class lands in the default package next to the harness,
and so are imports the sandbox can't satisfy that are only used as annotations (jspecify's @Nullable,
lombok's @NonNull, ...), with those annotations. Private methods (or methods of a private nested type)
are called through reflection. Java 25 syntax tree-sitter doesn't know yet (`import module`, statements
before super(...)) is hidden from the parser only.

Each argument is assigned to a local of the parameter's declared type before the call, so a byte/short
constant compiles, overloads resolve to the declared signature, and a model input of the wrong type
fails to compile: javac names its `case`, which is dropped, and the other inputs are built again.
Right before the call the harness initialises the method's class and creates `.entered` (marks_entry):
an input that throws while it is built (List.of(null), duplicate Map.of keys) or a static initializer
that fails is "not_entered", never a bug. Neither is System.exit (kind "exit", seen by a shutdown hook)
nor an exception the code creates on purpose (deliberate(): throw new IllegalArgumentException(...),
Objects.requireNonNull). A watchdog thread samples the main thread before the time limit, so an
infinite loop is reported at the line of the loop it is stuck in.
Collections are passed as mutable copies, inner lists too (new ArrayList<>(List.of(new ArrayList<>(...)))),
so a method that sorts or appends to its argument isn't "buggy". In the harness, JDK classes are written
in full (java.util.List.of, java.lang.Math), so a user class named Map or Math can't capture them.
"""
import re
from dataclasses import dataclass

from runner.languages.base import EXPECTED, MAX_INPUTS, RESULT_MARK, Function, Input, Language, Param, json_string

HARNESS = "PhantomHarness"
CALL_MARK = "@@PHANTOM-CALL"  # printed just before the call: no mark = the JVM never got that far
WATCHDOG = 1.5  # seconds of running time before an input counts as hanging (call_timeout leaves room for JVM start)
JAVAC_LIMIT = 40  # seconds for javac (an input that doesn't type-check can make overload resolution run for hours)
MAX_DEPTH = 6  # calls/creations nested in one input: javac's work on a nest that doesn't type-check triples per level
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
WIDTH = {"int": 32, "Integer": 32, "short": 16, "Short": 16, "byte": 8, "Byte": 8, "long": 64, "Long": 64}
HOLDERS = {"Integer": 32, "Long": 64, "Short": 16, "Byte": 8}  # classes whose MAX/MIN_VALUE are integer limits

NUMBERS = frozenset({"decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
                     "binary_integer_literal", "decimal_floating_point_literal", "hex_floating_point_literal"})
ARRAY_TYPES = frozenset({"String", "Object", *BOXED})
COLLECTIONS = frozenset({*LISTS.values(), *SETS.values(), *MAPS.values()})
UTIL_CLASSES = frozenset({"List", "Set", "Map", "Arrays"})  # the classes of literal_calls
JDK_NAMES = frozenset({*UTIL_CLASSES, *LISTS, *SETS, *MAPS, *BOXED, "Object"})  # names the inputs take to be the JDK's
NESTING = ("method_invocation", "object_creation_expression", "array_creation_expression")
LOOPS = ("while_statement", "for_statement", "enhanced_for_statement", "do_statement")
TYPE_DECLARATIONS = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
NULLABLE = re.compile(r"@(?:\w+\.)*(?:Nullable|CheckForNull)\b")
NON_NULL = re.compile(r"@(?:\w+\.)*(?:NonNull|Nonnull|NotNull)\b")
ANNOTATION = re.compile(r"@[\w.]+(?:\s*\([^)]*\))?\s*")
FRAME = re.compile(r"^\s*at ([\w$]+)\.[\w$<>]+\(([\w$]+)\.java:(\d+)\)", re.MULTILINE)
ESCAPE = r"\\(?:[btnfrs\"'\\]|[0-3][0-7]{0,2}|[4-7][0-7]?)"  # the escapes javac knows (\u is banned outright)
CHAR = re.compile(rf"'(?:[^'\\\n\r\U00010000-\U0010FFFF]|{ESCAPE})'")  # exactly one UTF-16 unit
STRING = re.compile(rf'"(?:[^"\\\n\r]|{ESCAPE})*"')  # one line: no text blocks
MODULE_IMPORT = re.compile(r"^[ \t]*import[ \t]+module[ \t]+[\w.]+[ \t]*;", re.MULTILINE)
CONSTRUCTOR_CALL = re.compile(rb"\b(?:super|this)\s*\(")
SINGLE_IMPORT = re.compile(r"import\s+([\w.]+)\.([\w$]+)\s*;")
CHECKS = re.compile(r"\bjava\.util\.Objects\.(?:requireNonNull\w*|checkIndex|checkFromToIndex|checkFromIndexSize)\(")
CHECK_CALL = re.compile(r"\b(?:requireNonNull\w*|checkIndex|checkFromToIndex|checkFromIndexSize)\s*\(")
JAVAC_ERROR = re.compile(r"^([\w$]+)\.java:(\d+): error", re.MULTILINE)
CASE = re.compile(r"^\s*case (\d+): \{$")


@dataclass
class JavaMethod(Function):
    owner: str = ""  # binary name of the declaring type: "Stats", "Stats$Inner"
    private: bool = False  # private, or inside a private type: called through reflection
    non_null: bool = False  # annotated @NonNull/@Nonnull/@NotNull: returning null is a bug
    shadowed: frozenset = frozenset()  # JDK names the file gives another meaning (its own class Map, java.awt.List)


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
    marks_entry = True
    rejection_pattern = r"\bthrow\b"  # only a fallback: deliberate() checks what the exception's trace says
    literal_hint = ("Java literals: numbers (3, -1, 2L, 1.5, 0x1F), \"strings\", 'c', true/false, null, arrays like "
                    "new int[]{1, 2} or new String[]{}, List.of(...), Set.of(...), Map.of(k1, v1, ...), "
                    "Arrays.asList(...), new ArrayList<>(List.of(...)), new HashMap<>(Map.of(...)), and constants "
                    "such as Integer.MAX_VALUE, Long.MIN_VALUE, Double.NaN; at most 6 calls nested in each other")
    literal_nodes = NUMBERS | {"true", "false", "character_literal", "string_literal", "string_fragment",
                               "escape_sequence", "null_literal", "argument_list"}
    literal_calls = frozenset({"List.of", "Set.of", "Map.of", "Arrays.asList"})
    literal_names = frozenset(
        [f"{t}.{c}" for t in ("Integer", "Long", "Short", "Byte", "Character") for c in ("MAX_VALUE", "MIN_VALUE")]
        + [f"{t}.{c}" for t in ("Double", "Float")
           for c in ("MAX_VALUE", "MIN_VALUE", "NaN", "POSITIVE_INFINITY", "NEGATIVE_INFINITY")])

    # ---------- parsing ----------

    def parse(self, source):
        # tree-sitter-java doesn't know Java 25 module imports: the parser sees spaces, javac the real line
        hidden = MODULE_IMPORT.sub(lambda m: " " * len(m.group(0).encode()), source)
        return self.parser().parse(hidden.encode())

    def has_errors(self, source):
        """Half-typed code, but not a Java 25 constructor with statements before its super(...)/this(...)
        call, which tree-sitter-java can't parse yet: those calls are read as plain method calls."""
        root = self.parse(source).root_node
        if not root.has_error:
            return False
        data, raw = bytearray(source.encode()), source.encode()
        for node in walk(root):
            if node.type == "constructor_body":
                for match in CONSTRUCTOR_CALL.finditer(raw, node.start_byte, node.end_byte):
                    data[match.start()] = ord("_")  # super(v) -> _uper(v)
        return self.parse(data.decode(errors="replace")).root_node.has_error

    # ---------- functions ----------

    def functions(self, source):
        root = self.parse(source).root_node
        found = []
        for node in root.named_children:
            if node.type in TYPE_DECLARATIONS:
                self._collect(node, [], False, found)
            elif node.type == "method_declaration":  # Java 25 compact source file: its class has no usable name
                fn = self._method(node, [], False)
                if fn is not None:
                    fn.skip = "methods outside a class (compact source file) can't be called from another class"
                    found.append(fn)
        seen, shadowed = set(), self._shadowed(root)
        for fn in found:
            if fn.name in seen:
                fn.skip = fn.skip or f"overloaded: only the first {fn.name} is run"
            seen.add(fn.name)
            fn.shadowed = shadowed
        return found

    def _shadowed(self, root):
        """JDK names the file gives another meaning: a type of its own named Map, or `import java.awt.List;`."""
        names = {self.text(n.child_by_field_name("name")) for n in walk(root)
                 if n.type in TYPE_DECLARATIONS + ("annotation_type_declaration",)}
        for node in root.named_children:
            match = node.type == "import_declaration" and SINGLE_IMPORT.fullmatch(self.text(node))
            if match and match.group(1) not in ("java.util", "java.lang"):
                names.add(match.group(2))
        return frozenset(names & JDK_NAMES)

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
        """The file as javac gets it: without its `package` line (it joins the harness's default package),
        and without imports from outside java.* that are only used as annotations (@Nullable, @NonNull...),
        which the sandbox can't satisfy: they go with every use, as do annotations written in full from such
        a package (@org.jspecify.annotations.Nullable). Blanked code becomes spaces, so line numbers stay."""
        root = self.parse(source).root_node
        blank = [n for n in root.named_children if n.type == "package_declaration"]
        named = {}  # id of each identifier in an annotation's name -> that annotation
        for note in (n for n in walk(root) if n.type in ("marker_annotation", "annotation")):
            name = note.child_by_field_name("name")
            for part in walk(name):
                named[part.id] = note
            if name is not None and name.type == "scoped_identifier" and not self.text(name).startswith("java."):
                blank.append(note)
        imports = {}
        for node in root.named_children:
            match = node.type == "import_declaration" and SINGLE_IMPORT.fullmatch(self.text(node))
            if match and not match.group(1).startswith("java."):
                imports[match.group(2)] = node
        uses = {}
        for top in root.named_children:
            if imports and top.type != "import_declaration":
                for node in walk(top):
                    if node.type in ("identifier", "type_identifier") and self.text(node) in imports:
                        uses.setdefault(self.text(node), []).append(node)
        for simple, node in imports.items():
            if all(use.id in named for use in uses.get(simple, [])):  # only ever an annotation (or unused)
                blank += [node] + [named[use.id] for use in uses.get(simple, [])]
        return blank_nodes(source, blank)

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
        if "\\u" in (expr or "") or not super().literal_ok(expr):
            return False
        # Map.of(Map.of(...)) twenty deep keeps javac busy for hours: the nesting is capped
        return depth(self.expression_tree(expr)[1]) <= MAX_DEPTH

    def _literal_node(self, node):
        if node.type == "character_literal":  # exactly one character: not 'ab', not '"+code+"'
            return CHAR.fullmatch(self.text(node)) is not None
        if node.type == "string_literal":  # one line, escapes javac knows: no text blocks, no "\q"
            return STRING.fullmatch(self.text(node)) is not None
        if node.type in NUMBERS:
            sign = node.parent.child_by_field_name("operator") if node.parent is not None else None
            return number_ok(self.text(node), node.type, negated=sign is not None and self.text(sign) == "-")
        if node.type == "unary_expression":  # only a sign in front of a number: no !, ~
            operator, operand = node.child_by_field_name("operator"), node.child_by_field_name("operand")
            return self.text(operator) in ("-", "+") and operand is not None and (
                operand.type in NUMBERS and self._literal_node(operand) or self.text(operand) in self.literal_names)
        if node.type == "array_creation_expression":
            return self._array_ok(node)
        if node.type == "object_creation_expression":
            return self._collection_ok(node)
        return super()._literal_node(node)

    def _allowed_call(self, node):
        """Exactly List.of, Set.of, Map.of or Arrays.asList: the whole callee, so List.of.foo(1) is not one.
        Map.of takes key/value pairs, at most 10 (anything else never compiles)."""
        obj, name = node.child_by_field_name("object"), node.child_by_field_name("name")
        if node.type != "method_invocation" or obj is None or name is None or obj.type != "identifier":
            return False
        callee = f"{self.text(obj)}.{self.text(name)}"
        if callee not in self.literal_calls:
            return False
        args = node.child_by_field_name("arguments")
        count = len(args.named_children) if args is not None else 0
        return callee != "Map.of" or (count % 2 == 0 and count <= 20)

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

    def _options(self, type_text, param_name, shadowed=frozenset()):
        decl = declared(type_text)
        if not decl or decl.endswith("...") or set(re.findall(r"[\w$.]+", decl)) & shadowed:
            return []  # varargs, or a name such as Map that means the file's own class here
        values = self._typed(decl, param_name)
        if values and decl not in PRIMITIVES:
            values.append(Value("null", None if self.nullable(type_text) else f"{param_name} may be null"))
        if not all(self.literal_ok(v.expr) for v in values):
            return []  # nested deeper than an input may be: it couldn't be replayed
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
        Inside an array initializer, arrays are written {...}; inside List.of(...) they need new T[]{...}.
        Inner lists are mutable copies too: a method may sort or append to the lists it is given."""
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
            if inner is None:
                return None
            concrete = LISTS[name]
            return [f"new {concrete}<>(List.of({', '.join(inner[0])}))"] * 3, f"new {concrete}<>()"
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
        so `get(values, i)` never gets i = 3 with a 1-element array unless that is named as an assumption.
        A count after a start index on the same value (start/count, offset/length) covers the rest of it:
        start + count = its size, whatever start is."""
        params = fn.params
        shadowed = getattr(fn, "shadowed", frozenset())
        options = [self._options(p.type, p.name, shadowed) for p in params]
        if any(not values for values in options):
            return []
        roles = [self._role(p.name, p.type) for p in params]
        ties = {}  # parameter -> (role, target parameter, order among the indexes of that target)
        for index, role in enumerate(roles):
            target = next((j for j, values in enumerate(options)
                           if j != index and values[0].kind in ("array", "list", "string")), None)
            if role == "index" and target is not None:
                ties[index] = ("index", target, sum(1 for _, t, _ in ties.values() if t == target))
        starts = {target: index for index, (_, target, order) in ties.items() if order == 0}
        for index, role in enumerate(roles):
            targets = [j for j, values in enumerate(options)
                       if j != index and (j in starts or values[0].kind in ("array", "list"))]
            if role == "count" and targets:  # a string only has a count with an index: offset/length
                ties[index] = ("count", min(targets, key=lambda j: (j not in starts, j)), 0)

        def follow(row, moved=None):
            """Tied parameters (except the one this row moves) take the values that fit their collection."""
            for index, (role, target, order) in ties.items():
                if index != moved and role == "index" and row[target].size is not None:
                    row[index] = self._tied(params[index], role, params[target].name, row[target].size, order)[0]
            for index, (role, target, order) in ties.items():
                if index != moved and role == "count" and row[target].size is not None:
                    begin = number(row[starts[target]].expr) if target in starts else None
                    size = row[target].size - (begin or 0)
                    suffix = "L" if declared(params[index].type) in ("long", "Long") else ""
                    row[index] = Value(f"{max(0, size)}{suffix}")
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
                rows.append(follow(row, index))
        inputs = {}
        for row in rows:
            assumptions = [v.assumption for v in row if v.assumption]
            sequences = [(p.name, v.size) for p, v in zip(params, row)
                         if v.kind in ("array", "list") and v.size is not None]
            if len({size for _, size in sequences}) > 1:
                assumptions.append(f"{' and '.join(name for name, _ in sequences)} may have different lengths")
            for index, (role, target, _) in ties.items():
                start = starts.get(target)
                if role != "count" or start is None or row[target].size is None or row[start].assumption:
                    continue
                begin, count = number(row[start].expr), number(row[index].expr)
                if begin is not None and count is not None and begin + count > row[target].size:
                    assumptions.append(f"{params[start].name} + {params[index].name} may be past the end of "
                                       f"{params[target].name}")
            inputs.setdefault(tuple(v.expr for v in row), Input([v.expr for v in row], assumptions=assumptions))
        return list(inputs.values())[:MAX_INPUTS]

    def expected_function(self, fn):
        returns = (fn.returns or "").strip()
        if not returns or returns == "void":
            return None
        return (f"\n\nclass PhantomExpected {{\n    static {returns} expected({returns} value) {{ return value; }}\n}}\n",
                "PhantomExpected.expected")

    def model_input(self, fn, args, hypothesis):
        shadowed = getattr(fn, "shadowed", frozenset())
        if len(args) != len(fn.params) or any(not self._options(p.type, p.name, shadowed) for p in fn.params):
            return None  # a type no edge value can be built for can't be declared in the harness either
        made = super().model_input(fn, args, hypothesis)
        if made is None:
            return None
        for index, (param, arg) in enumerate(zip(fn.params, made.args)):
            node = self.expression_tree(arg)[1]
            nodes = list(walk(node))
            if arg != "null" and any(n.type == "null_literal" for n in nodes) and not self.nullable(param.type):
                made.assumptions.append(f"{param.name} may contain null")
            width = int_width(param.type)
            extreme = next(((self.text(n), v) for n in nodes if (v := self._extreme(n, width)) is not None), None)
            if extreme:
                text, value = extreme
                size = "small" if value < 0 or text.endswith("MIN_VALUE") else "large"
                made.assumptions.append(f"{param.name} may be as {size} as {text}")
            mutable = self._mutable(param.type, node)
            if not self.literal_ok(mutable):
                return None  # its mutable copy is too long or too deep to replay
            made.args[index] = mutable
        return made

    def _extreme(self, node, width):
        """The value of an integer at the limits of the type that receives it (`width` bits: the parameter's
        integer type, or its elements'; else the literal's own type), or None. Overflow is often outside a
        method's contract, so such inputs carry an assumption: Integer.MAX_VALUE for an int, but not
        1700000000000L (epoch millis) for a long, nor 0xFFFFFFFF (the int -1)."""
        sign = 1
        if node.type == "unary_expression":
            sign = -1 if self.text(node.child_by_field_name("operator")) == "-" else 1
            node = node.child_by_field_name("operand")
            if node is None:
                return None
        if node.type == "field_access":
            holder, _, constant = self.text(node).rpartition(".")
            if constant not in ("MAX_VALUE", "MIN_VALUE"):
                return None
            if holder == "Character":
                return sign * (0xFFFF if constant == "MAX_VALUE" else 0)
            if holder not in HOLDERS or (width is not None and HOLDERS[holder] < width):
                return None  # Double.MAX_VALUE; Integer.MAX_VALUE for a long is an ordinary long
            bits = HOLDERS[holder]
            return sign * (2 ** (bits - 1) - 1 if constant == "MAX_VALUE" else -2 ** (bits - 1))
        if node.type not in NUMBERS or "floating" in node.type:
            return None
        digits = self.text(node).replace("_", "")
        bits = 64 if digits[-1] in "lL" else 32
        digits = digits.rstrip("lL")
        octal = re.fullmatch(r"0[0-7]+", digits)
        try:
            value = int(digits, 8) if octal else int(digits, 0)
        except ValueError:
            return None
        if (octal or not digits.isdigit()) and value >= 2 ** (bits - 1):
            value -= 2 ** bits  # hex/octal/binary literals wrap: 0xFFFFFFFF is -1
        value *= sign
        top = 2 ** ((width or bits) - 1)
        margin = 2 ** 10 if (width or bits) == 64 else 1
        return value if value >= top - margin or value < -top + margin else None

    def _mutable(self, type_text, node):
        """The argument with each List.of/Set.of/Arrays.asList/Map.of that fills a collection parameter, or a
        collection inside one, copied into a mutable one (List.of(List.of(1)) -> new ArrayList<>(List.of(
        new ArrayList<>(List.of(1))))): an immutable argument would make any method that sorts or adds to it
        look broken."""
        return self._rewrite(node, declared(type_text))

    def _rewrite(self, node, decl):
        name, args, dims = shape(decl)
        concrete = LISTS.get(name) or SETS.get(name) or MAPS.get(name)
        if dims or not concrete:
            return self.text(node)
        if node.type == "object_creation_expression":  # new ArrayList<>(List.of(...)): its elements may not be
            items = node.child_by_field_name("arguments").named_children
            if len(items) == 1 and items[0].type == "method_invocation":
                data, start, end = node.text, items[0].start_byte - node.start_byte, items[0].end_byte - node.start_byte
                return data[:start].decode() + self._elements(items[0], name, args) + data[end:].decode()
        elif node.type == "method_invocation":
            callee = f"{self.text(node.child_by_field_name('object'))}.{self.text(node.child_by_field_name('name'))}"
            if (callee == "Map.of") == (name in MAPS):
                return f"new {concrete}<>({self._elements(node, name, args)})"
        return self.text(node)

    def _elements(self, call, name, args):
        """List.of(a, b) with each element rewritten for the element type (Map.of: keys, then values)."""
        callee = f"{self.text(call.child_by_field_name('object'))}.{self.text(call.child_by_field_name('name'))}"
        items = call.child_by_field_name("arguments").named_children
        if name in MAPS and len(args) == 2:
            kinds = [args[k % 2] for k in range(len(items))]
        elif name not in MAPS and len(args) == 1:
            kinds = [args[0]] * len(items)
        else:
            return self.text(call)
        return f"{callee}({', '.join(self._rewrite(item, kind) for item, kind in zip(items, kinds))})"

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
        build = (f"timeout -k 2 {JAVAC_LIMIT} javac {JAVAC} -d . '{user}.java' {HARNESS}.java"
                 f" || {{ c=$?; [ $c -eq 124 ] && echo 'javac did not finish in {JAVAC_LIMIT} s'; exit $c; }}")
        return {"files": {f"{user}.java": source, f"{HARNESS}.java": harness}, "build": build, "run": RUN}

    def _case(self, fn, index, inp, owner):
        names = [f"a{k}" for k in range(len(fn.params))]
        lines = [f"            case {index}: {{"]
        lines += [f"                {declared(p.type)} {n} = {self._qualified(a)};"
                  for p, n, a in zip(fn.params, names, inp.args)]
        if getattr(fn, "private", False):
            classes = ", ".join(f"{erase(p.type)}.class" for p in fn.params)
            lines.append(f"                java.lang.reflect.Method m = method({json_string(owner)}, "
                         f"{json_string(fn.name.rsplit('.', 1)[-1])}, new java.lang.Class<?>[]{{{classes}}});")
            call = f"invoke(m, new java.lang.Object[]{{{', '.join(names)}}})"
        else:
            call = f"{fn.name}({', '.join(names)})"
        lines.append(f"                enter({json_string(owner)});")  # inputs built: the function is called next
        lines.append(" " * 16 + (f"{call}; return VOID;" if fn.returns == "void" else f"return {call};"))
        return "\n".join(lines + ["            }"])

    def _qualified(self, expr):
        """An input with its JDK classes written in full (java.util.List.of, new java.util.ArrayList<>,
        java.lang.Integer.MAX_VALUE): a class of the user's named Map or List can't capture them."""
        expr = expr.strip()
        node = self.expression_tree(expr)[1]
        if node is None:
            return expr
        inserts = []
        for n in walk(node):
            if n.type == "method_invocation":
                target, package = n.child_by_field_name("object"), "java.util."
                if target is None or target.type != "identifier" or self.text(target) not in UTIL_CLASSES:
                    continue
            elif n.type in ("object_creation_expression", "array_creation_expression"):
                target = n.child_by_field_name("type")
                target = target.named_children[0] if target is not None and target.type == "generic_type" else target
                if target is None or target.type != "type_identifier":
                    continue
                package = "java.util." if self.text(target) in COLLECTIONS else "java.lang."
                if self.text(target) not in (COLLECTIONS if n.type == "object_creation_expression" else ARRAY_TYPES):
                    continue
            elif n.type == "field_access" and self.text(n) in self.literal_names:
                target, package = n, "java.lang."
            else:
                continue
            inserts.append((target.start_byte - node.start_byte, package.encode()))
        data = expr.encode()
        for position, text in sorted(inserts, reverse=True):
            data = data[:position] + text + data[position:]
        return data.decode()

    def run_inputs(self, source, fn, inputs, sandbox, passes=3):
        """All inputs in one sandbox. When some don't compile (a model input of the wrong type), javac names
        their lines: those cases keep the build error, and the others are built and run again without them."""
        results = super().run_inputs(source, fn, inputs, sandbox)
        if passes <= 0 or len(inputs) < 2 or not all(r.get("kind") == "build_error" for r in results):
            return results
        harness = self.program(self.context(source, fn), fn, inputs)["files"][f"{HARNESS}.java"]
        bad = bad_cases(harness, results[0].get("message") or "")
        if not bad or len(bad) >= len(inputs):
            return results
        keep = [k for k in range(len(inputs)) if k not in bad]
        merged = list(results)
        for k, result in zip(keep, self.run_inputs(source, fn, [inputs[k] for k in keep], sandbox, passes - 1)):
            merged[k] = result
        return merged

    def parse_output(self, stdout, count):
        """No @@BUILD line: the run was stopped before javac finished, so nothing ran (a build failure, which
        Specter retries without the model's inputs). An input with no result is a sandbox problem, not a crash."""
        if "@@BUILD " not in stdout:
            message = "the sandbox stopped before the build finished (javac too slow, or the sandbox failed to start)"
            return [{"ok": False, "kind": "build_error", "error": "BuildError", "message": message}] * count
        return [{**r, "kind": "sandbox_error"} if r.get("kind") == "crash" and r.get("error") == "SandboxError" else r
                for r in super().parse_output(stdout, count)]

    def classify(self, code, out, err):
        result = super().classify(code, out, err)
        if CALL_MARK not in out and result.get("kind") in ("timeout", "crash"):
            # the JVM never reached the call (failed to start, or the machine is too busy): not the code's fault
            first = next((ln.strip() for ln in err.splitlines() if ln.strip()), "")
            return {"ok": False, "kind": "sandbox_error", "error": "SandboxError",
                    "message": f"the JVM did not reach the call: {first[:300] or f'exit {code}'}"}
        if result.get("kind") == "exit":  # the shutdown hook saw System.exit: on purpose, never a finding
            return {**result, "message": f"the code ended the program itself: System.exit({code})"}
        if result.get("kind") == "crash" and result.get("error") == "SystemExit":  # Runtime.halt runs no hooks
            return {**result, "kind": "exit"}
        return result

    def crash_error(self, code, err):
        """Reached the call, then died without a verdict: the JVM crashed, a signal, or Runtime.halt."""
        if code == 134 or "fatal error has been detected" in err:
            first = next((ln.strip("# ").strip() for ln in err.splitlines() if ln.strip("# ").strip()), "")
            return "JVMCrash", first[:300] or "the JVM aborted"
        if code >= 128:
            return super().crash_error(code, err)
        return "SystemExit", f"the code ended the JVM with exit code {code} (Runtime.halt)"

    def deliberate(self, source, failure):
        """The method threw on purpose: the exception object was created by the user's code on the failing
        line (`throw new IllegalArgumentException(...)`, `orElseThrow(() -> new ...)`, its own exception class)
        and is of the class created there, or it came from Objects.requireNonNull / checkIndex called on that
        line. An exception the JVM or a library raised from an ordinary line (/ by zero, an index, parseInt)
        stays a bug, and so does a different exception thrown while building the one meant to be thrown."""
        line, error = failure.get("line"), failure.get("error")
        if failure.get("kind") != "exception" or not line or not error:
            return False
        head = (failure.get("traceback") or "").split("Caused by:")[0]
        frames = [ln.strip() for ln in head.splitlines() if ln.strip().startswith("at ")]
        if not frames:  # no trace to go by: a `throw` of that class on the line
            return super().deliberate(source, failure) and self._creates(source, line, error)
        first = next((k for k, frame in enumerate(frames) if (m := FRAME.match(frame)) and m.group(2) != HARNESS), None)
        if first is None or int(FRAME.match(frames[first]).group(3)) != line:
            return False
        if first == 0:  # the JVM fills in a trace where the exception is created: in the user's code, here
            return self._creates(source, line, error)
        lines = source.splitlines()
        return any(CHECKS.search(frame) for frame in frames[:first]) and 0 < line <= len(lines) \
            and CHECK_CALL.search(lines[line - 1]) is not None

    def _creates(self, source, line, error):
        """Does `new <error>(...)` (any qualifier or type arguments) cover that line of the source?"""
        for node in walk(self.parse(source).root_node):
            if node.type == "object_creation_expression" and node.start_point[0] + 1 <= line <= node.end_point[0] + 1:
                kind = re.sub(r"<.*", "", self.text(node.child_by_field_name("type")) or "")
                if kind.rsplit(".", 1)[-1].strip() == error:
                    return True
        return False

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


def int_width(type_text):
    """Bits of the integer type a parameter holds (its elements', for an array or collection), or None
    when it holds no integer type or more than one (Map<Long, Integer>)."""
    widths = {WIDTH[t] for t in re.findall(r"\w+", declared(type_text)) if t in WIDTH}
    return widths.pop() if len(widths) == 1 else None


def number(expr):
    """The integer an index/count value expression stands for: "2" -> 2, "-1L" -> -1."""
    try:
        return int(expr.rstrip("lL"))
    except ValueError:
        return None


def number_ok(text, kind, negated=False):
    """Would javac accept this number literal? 2147483648 needs an L (only -2147483648 is an int), 1e400
    is too large for a double, 1e-50f too small for a float."""
    digits = text.replace("_", "")
    if kind == "hex_floating_point_literal":
        return True
    if "floating" in kind:
        single = digits[-1] in "fF"
        try:
            value = float(digits.rstrip("fFdD"))
        except ValueError:
            return False
        nonzero = any(c in "123456789" for c in re.split(r"[eE]", digits)[0])
        return value <= (3.4028235e38 if single else 1.7976931348623157e308) and \
            not (nonzero and value < (7.0e-46 if single else 2.5e-324))
    bits = 64 if digits[-1] in "lL" else 32
    digits = digits.rstrip("lL")
    octal = re.fullmatch(r"0[0-7]+", digits)
    try:
        value = int(digits, 8) if octal else int(digits, 0)
    except ValueError:
        return False
    return value <= (2 ** (bits - 1) - (not negated) if digits.isdigit() and not octal else 2 ** bits - 1)


def depth(node):
    """Calls and creations nested in each other in an expression: List.of(List.of(1)) is 2."""
    if node is None:
        return 0
    return max((depth(c) for c in node.children), default=0) + (node.type in NESTING)


def bad_cases(harness, message):
    """The inputs whose `case` block in the harness holds javac's errors, or None when an error is
    anywhere else (the user's file, the harness itself) or there is none to go by."""
    if len(message) >= 2000:  # clipped at the front: the first line may be a fragment
        message = message.split("\n", 1)[-1]
    errors = JAVAC_ERROR.findall(message)
    if not errors or any(file != HARNESS for file, _ in errors):
        return None
    starts, end = [], None
    for number_, text in enumerate(harness.splitlines(), 1):
        match = CASE.match(text)
        if match:
            starts.append((number_, int(match.group(1))))
        elif text.strip().startswith("default: throw"):
            end = number_
    bad = set()
    for _, line in errors:
        owners = [case for start, case in starts if start <= int(line)]
        if not owners or end is None or int(line) >= end:
            return None
        bad.add(owners[-1])
    return bad


def blank_nodes(source, nodes):
    """source with each node's text replaced by spaces (newlines stay, so line numbers do too)."""
    data = bytearray(source.encode())
    for node in nodes:
        for i in range(node.start_byte, node.end_byte):
            if data[i] not in (10, 13):
                data[i] = 32
    return data.decode(errors="replace")


def walk(node):
    if node is not None:
        yield node
        for child in node.children:
            yield from walk(child)


TEMPLATE = r"""import java.util.*; // for parameter types as the user's file writes them; the rest names JDK classes in full

public final class PhantomHarness {
    static final java.lang.String USER_FILE = __FILE__;
    static final long LIMIT_MS = __LIMIT__;
    static final boolean NON_NULL = __NONNULL__;
    static final java.lang.String LOOPS = __LOOPS__; // first-last line of every loop in the user's file
    static final java.io.PrintStream OUT = java.lang.System.out;
    static final java.lang.Object VOID = new java.lang.Object();
    static boolean reported;

    public static void main(java.lang.String[] args) {
        int index = java.lang.Integer.parseInt(args[0]);
        java.lang.System.setOut(new java.io.PrintStream(java.io.OutputStream.nullOutputStream()));
        java.lang.Runtime.getRuntime().addShutdownHook(new java.lang.Thread(PhantomHarness::exited));
        new Watchdog(java.lang.Thread.currentThread()).start();
        OUT.print(__MARK__);
        OUT.flush();
        java.lang.String verdict;
        try {
            java.lang.Object result = call(index);
            if (NON_NULL && result == null) {
                verdict = "{\"ok\":false,\"kind\":\"return_type\",\"error\":\"ReturnTypeError\",\"message\":"
                    + q("returned null, but the method is annotated as never returning null") + "}";
            } else {
                verdict = "{\"ok\":true,\"result\":" + q(show(result)) + "}";
            }
        } catch (java.lang.Throwable e) {
            int line = userLine(e);
            boolean init = initializer(e); // a class the method uses failed to load: the code's setup, not its logic
            java.lang.String message = e.getMessage() == null ? "" : e.getMessage();
            verdict = "{\"ok\":false,\"kind\":" + q(init ? "not_entered" : "exception") + ",\"error\":" + q(name(e))
                + ",\"message\":" + q(clip(init ? "a static initializer failed: " + cause(e) : message, 1000))
                + ",\"trace\":" + q(trace(e)) + (line > 0 ? ",\"line\":" + line : "") + "}";
        }
        report(verdict);
    }

    static java.lang.Object call(int index) throws java.lang.Throwable {
        switch (index) {
__CASES__
            default: throw new java.lang.IllegalArgumentException("no input " + index);
        }
    }

    // Right before the call, after the inputs are built: the method's class is initialised (a static
    // initializer that fails is not the method's bug), then .entered tells the runner the call happens.
    static void enter(java.lang.String owner) throws java.lang.Throwable {
        java.lang.Class.forName(owner, true, PhantomHarness.class.getClassLoader());
        java.nio.file.Files.write(java.nio.file.Paths.get(".entered"), new byte[0]);
    }

    static java.lang.reflect.Method method(java.lang.String owner, java.lang.String name, java.lang.Class<?>[] types)
            throws java.lang.Throwable {
        java.lang.reflect.Method m = java.lang.Class.forName(owner, false, PhantomHarness.class.getClassLoader())
            .getDeclaredMethod(name, types);
        m.setAccessible(true);
        return m;
    }

    static java.lang.Object invoke(java.lang.reflect.Method m, java.lang.Object[] values) throws java.lang.Throwable {
        try {
            return m.invoke(null, values);
        } catch (java.lang.reflect.InvocationTargetException e) {
            throw e.getCause();
        }
    }

    static boolean initializer(java.lang.Throwable e) {
        return e instanceof java.lang.ExceptionInInitializerError || e instanceof java.lang.NoClassDefFoundError
            && java.lang.String.valueOf(e.getMessage()).startsWith("Could not initialize class");
    }

    static java.lang.String cause(java.lang.Throwable e) {
        java.lang.Throwable root = e;
        for (int k = 0; k < 5 && root.getCause() != null; k++) root = root.getCause();
        return root.toString();
    }

    static synchronized void report(java.lang.String verdict) {
        if (reported) return;
        reported = true;
        OUT.print(__RESULT__ + verdict + "\n");
        OUT.flush();
        java.lang.Runtime.getRuntime().halt(0);
    }

    // Shutdown hooks only run when the code ends the JVM itself (System.exit): report() halts without them.
    static synchronized void exited() {
        if (reported) return;
        reported = true;
        OUT.print(__RESULT__ + "{\"ok\":false,\"kind\":\"exit\",\"error\":\"SystemExit\",\"message\":\"System.exit\"}\n");
        OUT.flush();
    }

    static final class Watchdog extends java.lang.Thread {
        final java.lang.Thread target;

        Watchdog(java.lang.Thread target) {
            super("phantom-watchdog");
            this.target = target;
            setDaemon(true);
        }

        public void run() {
            try {
                java.lang.Thread.sleep(LIMIT_MS - 200);
                java.lang.StackTraceElement[][] samples = new java.lang.StackTraceElement[5][];
                for (int k = 0; k < samples.length; k++) {
                    samples[k] = target.getStackTrace();
                    java.lang.Thread.sleep(40);
                }
                java.lang.StringBuilder b = new java.lang.StringBuilder("main thread when the time ran out:\n");
                java.lang.StackTraceElement[] last = samples[samples.length - 1];
                for (int k = 0; k < java.lang.Math.min(last.length, 30); k++) b.append("\tat ").append(last[k]).append('\n');
                int line = loopLine(samples);
                report("{\"ok\":false,\"kind\":\"timeout\",\"error\":\"Timeout\",\"message\":" + q(__TIMEOUT__)
                    + ",\"trace\":" + q(b.toString()) + (line > 0 ? ",\"line\":" + line : "") + "}");
            } catch (java.lang.Throwable e) {
                // interrupted: nothing to report
            }
        }
    }

    // The line to blame for a hang: in the deepest user frame that every sample shares (the method that
    // loops, not the helpers it calls), the first line of the innermost loop around every sampled line.
    static int loopLine(java.lang.StackTraceElement[][] samples) {
        java.util.List<java.util.List<java.lang.StackTraceElement>> stacks = new java.util.ArrayList<>();
        for (java.lang.StackTraceElement[] sample : samples) {
            java.util.List<java.lang.StackTraceElement> frames = new java.util.ArrayList<>();
            for (int k = sample.length - 1; k >= 0; k--) if (user(sample[k])) frames.add(sample[k]);
            stacks.add(frames);
        }
        int depth = 0;
        search:
        while (true) {
            for (java.util.List<java.lang.StackTraceElement> frames : stacks) {
                if (frames.size() <= depth) break search;
                java.lang.StackTraceElement a = frames.get(depth), b = stacks.get(0).get(depth);
                boolean same = a.getClassName().equals(b.getClassName()) && a.getMethodName().equals(b.getMethodName());
                if (!same) break search;
            }
            depth++;
        }
        if (depth == 0) return -1;
        int low = java.lang.Integer.MAX_VALUE, high = 0, best = -1, width = java.lang.Integer.MAX_VALUE;
        for (java.util.List<java.lang.StackTraceElement> frames : stacks) {
            low = java.lang.Math.min(low, frames.get(depth - 1).getLineNumber());
            high = java.lang.Math.max(high, frames.get(depth - 1).getLineNumber());
        }
        for (java.lang.String range : LOOPS.split(",")) {
            int dash = range.indexOf('-');
            if (dash < 0) continue;
            int first = java.lang.Integer.parseInt(range.substring(0, dash));
            int last = java.lang.Integer.parseInt(range.substring(dash + 1));
            if (first <= low && high <= last && last - first < width) {
                best = first;
                width = last - first;
            }
        }
        return best > 0 ? best : low > 0 && low < java.lang.Integer.MAX_VALUE ? low : -1;
    }

    static boolean user(java.lang.StackTraceElement frame) {
        return USER_FILE.equals(frame.getFileName()) && frame.getClassName().indexOf('.') < 0;
    }

    static int userLine(java.lang.Throwable e) {
        if (e instanceof java.lang.StackOverflowError) {
            // the top frame lands on the call or on the callee's first line at random: blame the recursive call,
            // the user line most of the 1024 recorded frames are at
            java.util.Map<java.lang.Integer, java.lang.Integer> counts = new java.util.HashMap<>();
            int best = -1;
            for (java.lang.StackTraceElement frame : e.getStackTrace()) {
                if (!user(frame) || frame.getLineNumber() <= 0) continue;
                int seen = counts.merge(frame.getLineNumber(), 1, java.lang.Integer::sum);
                if (best < 0 || seen > counts.get(best)) best = frame.getLineNumber();
            }
            if (best > 0) return best;
        }
        int depth = 0;
        for (java.lang.Throwable t = e; t != null && depth < 5; t = t.getCause(), depth++) {
            for (java.lang.StackTraceElement frame : t.getStackTrace()) {
                if (user(frame) && frame.getLineNumber() > 0) return frame.getLineNumber();
            }
        }
        return -1;
    }

    static java.lang.String trace(java.lang.Throwable e) {
        java.lang.StringBuilder b = new java.lang.StringBuilder();
        int depth = 0;
        for (java.lang.Throwable t = e; t != null && depth < 4; t = t.getCause(), depth++) {
            if (depth > 0) b.append("Caused by: ");
            b.append(t.getClass().getName());
            if (t.getMessage() != null) b.append(": ").append(clip(t.getMessage(), 300));
            b.append('\n');
            java.lang.StackTraceElement[] frames = t.getStackTrace();
            int shown = java.lang.Math.min(frames.length, 30);
            for (int k = 0; k < shown; k++) b.append("\tat ").append(frames[k]).append('\n');
            if (frames.length > shown) b.append("\t... ").append(frames.length - shown).append(" more\n");
        }
        return b.toString();
    }

    static java.lang.String name(java.lang.Throwable e) {
        java.lang.String simple = e.getClass().getSimpleName();
        return simple.isEmpty() ? e.getClass().getName() : simple;
    }

    static java.lang.String show(java.lang.Object v) {
        try {
            java.lang.String s;
            if (v == VOID) s = "void";
            else if (v == null) s = "null";
            else if (v instanceof java.lang.String) s = quote((java.lang.String) v, false);
            else if (v instanceof java.lang.Character) s = "'" + v + "'";
            else if (v instanceof java.lang.Object[]) s = java.util.Arrays.deepToString((java.lang.Object[]) v);
            else if (v instanceof int[]) s = java.util.Arrays.toString((int[]) v);
            else if (v instanceof long[]) s = java.util.Arrays.toString((long[]) v);
            else if (v instanceof double[]) s = java.util.Arrays.toString((double[]) v);
            else if (v instanceof float[]) s = java.util.Arrays.toString((float[]) v);
            else if (v instanceof short[]) s = java.util.Arrays.toString((short[]) v);
            else if (v instanceof byte[]) s = java.util.Arrays.toString((byte[]) v);
            else if (v instanceof char[]) s = java.util.Arrays.toString((char[]) v);
            else if (v instanceof boolean[]) s = java.util.Arrays.toString((boolean[]) v);
            else s = java.lang.String.valueOf(v);
            return clip(s, 300);
        } catch (java.lang.Throwable e) {
            return "<" + v.getClass().getName() + ">";
        }
    }

    static java.lang.String clip(java.lang.String s, int max) {
        return s.length() > max ? s.substring(0, max - 3) + "..." : s;
    }

    static java.lang.String q(java.lang.String s) {
        return quote(s, true); // pure ASCII on stdout, whatever the container's locale
    }

    static java.lang.String quote(java.lang.String s, boolean ascii) {
        java.lang.StringBuilder b = new java.lang.StringBuilder("\"");
        for (int k = 0; k < s.length(); k++) {
            char c = s.charAt(k);
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else if (c == '\t') b.append("\\t");
            else if (c < 0x20 || c == 0x7f || ascii && c > 0x7e) b.append(java.lang.String.format("\\%s%04x", "u", (int) c));
            else b.append(c);
        }
        return b.append('"').toString();
    }
}
"""
