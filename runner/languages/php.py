"""PHP: top-level functions (also inside namespaces) and public static methods, run by PHP 8.4 CLI.

Inputs are PHP literals: numbers, strings without interpolation, true/false/null and arrays.
Declared types decide the values: PHP enforces them, so a typed parameter never sees null unless
its type allows it. `array` says nothing about its contents, so a docblock decides (@param int[]
$values, int[][] $grid, array<string, int> $counts, list<string>|null $names, array{id: int} $row).
Without one the element type is a guess from the name, so a non-empty array carries the assumption
("values holds ints (guessed from its name)"); only [] fits every array. An array whose keys the
code reads ($user['name'], or $row['id'] for each $row of it) is a map, or a list of maps, with
keys nobody documented, so its values are assumptions too. Untyped parameters: a kind given by the
docblock or a default value counts as declared; a kind guessed from the name is an assumption
("count is an int (guessed from its name)").

What runs when the file loads: declarations and setup stay (assignments, define(), include, loops
that fill tables, declare, use, global). Blanked, with line numbers kept: bare calls that run the
program (main(), $app->run()), echo/print, exit/die, top-level return/throw, inline HTML, entry
guards (`if (PHP_SAPI === 'cli')`, `if (__FILE__ === ...)`), and require/require_once: the sandbox
holds only this file, so a required file is always missing, which is a fatal error that would stop
every function from running, while a call into it from a function is just "unavailable". A
function named main is the program's entry point and isn't run. A shebang line stays (PHP skips it).

The harness is a second file, harness.php, with strict_types: an input of the wrong type fails at
the call ("called in harness.php") and is dropped as an invalid input, never reported. Each input
gets its own copy of main.php in its own directory (also the working directory and TMPDIR), so
files written by one input can't change the next. The harness creates .entered right before the
call (marks_entry): a failure while loading the file is never a finding. It turns warnings,
notices and deprecations about the data ("Passing null to parameter", "Implicit conversion") into
ErrorException and reports the innermost line in main.php; the code's own trigger_error() notices,
warnings and deprecations, and deprecations about the code (a deprecated function), let it go on.
Not bugs (deliberate()): an exception the code creates on purpose on the failing line (`throw new
X`, `$e = new X(); throw $e;`, a factory's `return new X`), a failed assert(), trigger_error() with
E_USER_ERROR, a JsonException where the line asks for JSON_THROW_ON_ERROR, and an exception listed
in the @throws of a function on the failing path. exit()/die() is kind "exit"; calls to functions,
classes or constants from other files are "unavailable". Fatal errors (memory, a recursion that
exhausts it, the 1 s CPU limit) come from stderr. The build step compiles the harness and loads
main.php once, so a broken file or a literal PHP rejects (like "\\u{110000}") fails there.
"""
import functools
import re
from dataclasses import dataclass, field

from runner.languages.base import EXPECTED, MAX_INPUTS, RESULT_MARK, Function, Input, Language, Param
from runner.languages.javascript import TEXT_WORDS, JavaScript, split_union

PHP = "php -n -d memory_limit=128M -d display_errors=stderr -d log_errors=0"
CPU_LIMIT = 1  # max_execution_time: CPU seconds, so a loop is stopped (with its line) before the wall timeout
BUILD_LIMIT = 15  # seconds to compile the harness and load main.php once (its top-level code runs)
E_USER_ERROR = 256
CLASS_LIKE = ("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration")
DECLARATIONS = frozenset({"php_tag", "function_definition", "namespace_definition", "namespace_use_declaration",
                          "const_declaration", "comment", "global_declaration", "unset_statement",
                          "function_static_declaration"})
SETUP = frozenset({"assignment_expression", "augmented_assignment_expression", "reference_assignment_expression",
                   "update_expression", "include_expression", "include_once_expression"})
SETUP_CALLS = frozenset({"define", "ini_set", "error_reporting", "date_default_timezone_set", "spl_autoload_register",
                         "mb_internal_encoding", "setlocale", "class_alias", "set_include_path", "mt_srand", "srand",
                         "bcscale", "putenv", "assert_options"})
BLOCKS = frozenset({"if_statement", "foreach_statement", "for_statement", "while_statement", "do_statement",
                    "switch_statement", "try_statement", "compound_statement"})
RUNS_PROGRAM = frozenset({"echo_statement", "exit_statement", "print_intrinsic", "return_statement", "text",
                          "text_interpolation", "require_expression", "require_once_expression"})
DEFERRED = frozenset({"anonymous_function", "arrow_function", "function_definition", "method_declaration",
                      "anonymous_class", *CLASS_LIKE})  # code inside these doesn't run while the file loads
ENTRY_GUARD = re.compile(r"__FILE__|SCRIPT_FILENAME|PHP_SELF|\bPHP_SAPI\b|php_sapi_name|debug_backtrace"
                         r"|get_included_files|\$argv\b|\$argc\b")
INDEX_WORDS = ("index", "idx", "i", "j", "pos", "position", "offset")
SCALARS = {  # typical value first, then boundary values of the same type
    "int": ["1", "0", "-1"],
    "float": ["1.5", "0.0", "-1.0"],
    "string": ["'abc'", "''", "' '", "'a:b'"],
    "bool": ["true", "false"],
}
ONE = {"int": "1", "float": "1.5", "string": "'abc'", "bool": "true"}  # one typical element
ELEMENTS = {"int": "1, 2, 3", "float": "1.5, 2.5", "string": "'a', 'b', 'c'", "bool": "true, false"}
ARTICLE = {"int": "an int", "float": "a float", "string": "a string", "bool": "a bool"}
ALIASES = {"integer": "int", "double": "float", "boolean": "bool", "list": "array"}
EXTREMES = [("PHP_INT_MAX", "may be as large as PHP_INT_MAX"), ("PHP_INT_MIN", "may be as small as PHP_INT_MIN")]
GUARD = re.compile(r"\(\s*!\s*function_exists\s*\(\s*(['\"])[\\\w]+\1\s*\)\s*\)")
FATAL = re.compile(r"(?:PHP )?Fatal error:\s+(.+?) in (\S+) on line (\d+)")
GENERIC_ARRAY = re.compile(r"(non-empty-)?(array|list|iterable)\s*<(.+)>", re.I | re.S)
SHAPE = re.compile(r"(?:array|list)\s*\{(.*)\}", re.I | re.S)
SHAPE_FIELD = re.compile(r"(['\"]?)(\w+)\1(\?)?\s*:\s*(.+)", re.S)
BUILTIN_PARENTS = {name.lower(): parent.lower() for name, parent in (  # PHP's own exception classes
    ("Exception", "Throwable"), ("Error", "Throwable"), ("ErrorException", "Exception"), ("TypeError", "Error"),
    ("ArgumentCountError", "TypeError"), ("ValueError", "Error"), ("ArithmeticError", "Error"),
    ("DivisionByZeroError", "ArithmeticError"), ("AssertionError", "Error"), ("UnhandledMatchError", "Error"),
    ("JsonException", "Exception"), ("LogicException", "Exception"), ("BadFunctionCallException", "LogicException"),
    ("BadMethodCallException", "BadFunctionCallException"), ("DomainException", "LogicException"),
    ("InvalidArgumentException", "LogicException"), ("LengthException", "LogicException"),
    ("OutOfRangeException", "LogicException"), ("RuntimeException", "Exception"),
    ("OutOfBoundsException", "RuntimeException"), ("OverflowException", "RuntimeException"),
    ("RangeException", "RuntimeException"), ("UnderflowException", "RuntimeException"),
    ("UnexpectedValueException", "RuntimeException"), ("DateException", "Exception"),
    ("DateMalformedStringException", "DateException"), ("RandomException", "Exception"))}
BUILTIN_TYPES = frozenset(name.lower() for name in (  # parents that exist without other files
    "Exception Error ErrorException TypeError ValueError ArithmeticError DivisionByZeroError ArgumentCountError "
    "AssertionError UnhandledMatchError JsonException LogicException BadFunctionCallException "
    "BadMethodCallException DomainException InvalidArgumentException LengthException OutOfRangeException "
    "RuntimeException OutOfBoundsException OverflowException RangeException UnderflowException "
    "UnexpectedValueException Throwable Stringable Countable ArrayAccess Traversable Iterator IteratorAggregate "
    "JsonSerializable Serializable UnitEnum BackedEnum ArrayObject ArrayIterator IteratorIterator FilterIterator "
    "SplObjectStorage SplStack SplQueue SplDoublyLinkedList SplFixedArray SplHeap SplMinHeap SplMaxHeap "
    "SplPriorityQueue SplSubject SplObserver DateTime DateTimeImmutable DateTimeInterface stdClass").split())


@dataclass
class PhpFunction(Function):
    docs: dict = field(default_factory=dict)  # parameter name -> type from the docblock's @param
    throws: list = field(default_factory=list)  # exception classes from the docblock's @throws (short, lowercase)


class Php(Language):
    name = "php"
    title = "PHP"
    vscode_ids = ("php",)
    extensions = (".php",)
    grammar = "php"
    image = "php:8.4-cli"
    local_tool = "php"
    memory = "256m"
    tmpfs = "64m"
    build_timeout = 30.0
    call_timeout = 3
    null_words = ("null",)
    user_file = "main"
    marks_entry = True
    literal_hint = ("integers, floats, 'single-quoted' or \"double-quoted\" strings without $ or {$ inside, "
                    "true/false/null, arrays [1, 2] / ['key' => 'value'] / array(1, 2), PHP_INT_MAX, PHP_INT_MIN, "
                    "PHP_FLOAT_EPSILON, INF, NAN")
    literal_nodes = frozenset({
        "integer", "float", "string", "string_content", "escape_sequence", "encapsed_string", "boolean", "null",
        "array_creation_expression", "array_element_initializer", "unary_op_expression",
    })
    literal_names = frozenset({"PHP_INT_MAX", "PHP_INT_MIN", "PHP_INT_SIZE", "PHP_FLOAT_EPSILON", "PHP_FLOAT_MAX",
                               "PHP_FLOAT_MIN", "INF", "NAN"})

    # ---------- literals ----------

    def expression_tree(self, expr):
        tree = self.parse(f"<?php ({expr});")
        top = tree.root_node.named_children
        if len(top) != 2 or top[1].type != "expression_statement" or not top[1].named_children:
            return tree, None
        inner = top[1].named_children[0]
        if inner.type != "parenthesized_expression" or len(inner.named_children) != 1:
            return tree, None
        return tree, inner.named_children[0]

    def _literal_node(self, node):
        if node.type == "unary_op_expression":  # only a sign in front of a number: no !, ~, @
            op, arg = node.child_by_field_name("operator"), node.child_by_field_name("argument")
            return self.text(op) in ("-", "+") and arg is not None and \
                (arg.type in ("integer", "float") or self.text(arg) in self.literal_names)
        if node.type == "encapsed_string":  # "..." without $x, {$x} or ${x}: no interpolation at all
            return all(c.type in ('"', "string_content", "escape_sequence") for c in node.children) and \
                not any("$" in self.text(c) for c in node.children if c.type == "string_content")
        return super()._literal_node(node)

    # ---------- functions ----------

    def _statements(self, root):
        """(node, namespace, previous sibling) for each top-level statement, braced namespace bodies included."""
        def walk(children, namespace):
            previous = None
            for node in children:
                yield node, namespace, previous
                if node.type == "namespace_definition":
                    name = self.text(node.child_by_field_name("name")) or ""
                    body = node.child_by_field_name("body")
                    if body is None:
                        namespace = name  # `namespace App;` applies to everything after it
                    else:
                        yield from walk(body.named_children, name)
                previous = node
        return walk(root.named_children, "")

    def _guarded(self, node):
        """The functions of an `if (!function_exists('f')) { function f() {...} }` block, else []."""
        body = node.child_by_field_name("body")
        if node.type != "if_statement" or body is None or len(node.named_children) != 2 \
                or not GUARD.fullmatch(self.text(node.child_by_field_name("condition")) or ""):
            return []
        inner = [c for c in body.named_children if c.type != "comment"]
        return inner if inner and all(c.type == "function_definition" for c in inner) else []

    def functions(self, source):
        root = self.parse(source).root_node
        declared = self._declared(root)
        found = []
        for node, namespace, previous in self._statements(root):
            if node.type == "function_definition":
                found.append(self._function(node, qualify(namespace, self.text(node.child_by_field_name("name"))),
                                            previous))
            elif node.type in CLASS_LIKE:
                found += self._methods(node, namespace, declared)
            for inner in self._guarded(node):
                found.append(self._function(inner, qualify(namespace, self.text(inner.child_by_field_name("name"))),
                                            inner.prev_named_sibling))
        for fn in found:
            if fn.skip is None and re.split(r"\\|::", fn.name)[-1].lower() == "main":
                fn.skip = "main is the program's entry point: it isn't run"
        return found

    def _function(self, node, name, previous):
        params, skip = [], None
        doc = self._docblock(previous, node)
        holder = node.child_by_field_name("parameters")
        for child in holder.named_children if holder is not None else []:
            if child.type == "comment":
                continue
            if child.type == "variadic_parameter":
                skip = skip or "variadic parameters are not supported yet"
            elif child.type != "simple_parameter" or child.child_by_field_name("reference_modifier") is not None:
                skip = skip or f"parameter {self.text(child)!r} is not supported yet"  # by reference, promoted
            else:
                kind, default = self.text(child.child_by_field_name("type")), child.child_by_field_name("default_value")
                if kind is None and default is not None:
                    kind = f"default:{self.text(default)}"
                elif kind and default is not None and self.text(default).lower() == "null" and not self.nullable(kind):
                    kind = f"{kind}|null"  # `int $x = null` is implicitly nullable
                params.append(Param(self.text(child.child_by_field_name("name")).lstrip("$"), kind))
        docs = {name: kind.strip() for kind, name in re.findall(r"@param\s+(.+?)\s+&?(?:\.\.\.)?\$(\w+)", doc)}
        throws = [t.rsplit("\\", 1)[-1].lower() for group in re.findall(r"@throws\s+([\\\w|]+)", doc)
                  for t in group.split("|") if t]
        return PhpFunction(name=name, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, params=params,
                           returns=self.text(node.child_by_field_name("return_type")), text=self.text(node),
                           skip=skip, docs=docs, throws=throws)

    def _docblock(self, previous, node):
        """The /** docblock */ right above the function, or ""."""
        if previous is None or previous.type != "comment" or not self.text(previous).startswith("/**") \
                or previous.end_point[0] < node.start_point[0] - 1:
            return ""
        return self.text(previous)

    def _methods(self, node, namespace, declared):
        cls = self.text(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        missing = self._missing(node, declared)
        found, previous = [], None
        for method in body.named_children if body is not None else []:
            if method.type == "method_declaration":
                name = f"{qualify(namespace, cls)}::{self.text(method.child_by_field_name('name'))}"
                fn = self._function(method, name, previous)
                modifiers = {c.type: self.text(c) for c in method.children if c.type.endswith("_modifier")}
                visibility = modifiers.get("visibility_modifier", "public")
                if node.type == "interface_declaration" or method.child_by_field_name("body") is None:
                    fn.skip = "abstract methods have no body to run"
                elif node.type == "trait_declaration":
                    fn.skip = "trait methods are not run on their own"
                elif "static_modifier" not in modifiers:
                    fn.skip = "instance methods are not run yet"
                elif visibility != "public":
                    fn.skip = f"{visibility} methods can't be called from outside the class"
                elif missing:
                    fn.skip = f"{cls} needs {', '.join(missing)}, which isn't in this file"
                found.append(fn)
            previous = method
        return found

    def _declared(self, root):
        return {self.text(node.child_by_field_name("name")).lower()
                for node, _, _ in self._statements(root) if node.type in CLASS_LIKE}

    def _missing(self, node, declared):
        """Parent classes, interfaces and traits of a class that neither this file nor PHP defines."""
        clauses = [c for c in node.named_children if c.type in ("base_clause", "class_interface_clause")]
        body = node.child_by_field_name("body")
        clauses += [c for c in body.named_children if c.type == "use_declaration"] if body is not None else []
        names = [self.text(n) for c in clauses for n in c.named_children if n.type in ("name", "qualified_name")]
        known = (declared - {self.text(node.child_by_field_name("name")).lower()}) | BUILTIN_TYPES  # not itself
        return [n for n in names if n.rsplit("\\", 1)[-1].lower() not in known]

    # ---------- inputs ----------

    def family(self, param_name, default=None):
        if default:
            lowered = default.strip().lower()
            if lowered.startswith(("[", "array(")):
                return "list"
            if lowered[:1] in "'\"":
                return "string"
            if lowered in ("true", "false"):
                return "bool"
            if re.fullmatch(r"-?\d[\d_]*", lowered):
                return "int"
            if re.fullmatch(r"-?\d*\.\d+(e-?\d+)?|-?\d+e-?\d+", lowered):
                return "float"
            if lowered != "null":
                return None  # a constant or an expression
        if param_name.lower().startswith(("is_", "has_", "should_", "can_", "allow_")):
            return "bool"
        kind = JavaScript.family(self, param_name, None)
        return {"array": "list", "number": "int", "boolean": "bool", "object": "map"}.get(kind, kind)

    def edge_values(self, type_text, param_name, doc=None, usage=None):
        """Boundary values, typical first. doc: the docblock's type for this parameter. usage: how the code
        reads it (see reads_keys); True means "reads string keys from it", like ("map", [])."""
        usage = ("map", []) if usage is True else usage or None
        default = type_text[len("default:"):].strip() if type_text and type_text.startswith("default:") else None
        if type_text and default is None:
            return self.typed_values(type_text, param_name, doc, usage)
        null = ("null", None if self.nullable(type_text) else f"{param_name} may be null")
        documented = self.typed_values(doc, param_name, doc, usage) if doc else []
        if documented:  # an untyped parameter with a documented type: the docblock is the contract
            return documented + ([null] if null[0] not in dict(documented) else [])
        kind = self.family(param_name, default) if default and default.lower() != "null" else None
        guessed = None  # where a kind that nothing declares comes from
        if kind is None:
            kind, guessed = ("list", "how the code reads it") if usage else (self.family(param_name), "its name")
        if kind is None:  # nothing to go on: try every kind, each one an assumption
            values = [(v, f"{param_name} may be {v}") for v in ("1", "0", "-1", "''", "'abc'", "[]")]
        elif kind in ("list", "map"):
            values = self._array_values(param_name, None, usage)
            if guessed:  # the whole array is a guess: say so, unless the value already names its own assumption
                values = [(v, a if a and " may be " in a else f"{param_name} is an array (guessed from {guessed})")
                          for v, a in values]
        else:
            note = f"{param_name} is {ARTICLE[kind]} (guessed from its name)" if guessed else None
            values = [(v, note) for v in SCALARS[kind]]
        return values + [null]

    def typed_values(self, type_text, param_name, doc=None, usage=None):
        """Values a declared type allows, typical first; extreme integers and guessed contents are assumptions."""
        values = []
        for part in split_union(type_text.strip().lstrip("?")):
            part = part.strip()
            values += self._typed_part(part[1:-1] if part[:1] + part[-1:] == "()" else part, param_name, doc, usage)
        if self.nullable(type_text) and type_text.strip().lower() != "mixed":
            values.append(("null", None))
        unique = {}
        for value, assumption in values:
            unique.setdefault(value, (value, assumption))
        return list(unique.values())

    def _typed_part(self, part, param_name, doc, usage):
        lowered = ALIASES.get(part.lower(), part.lower())
        if lowered in SCALARS:
            extremes = [(v, f"{param_name} {note}") for v, note in EXTREMES] if lowered == "int" else []
            return [(v, None) for v in SCALARS[lowered]] + extremes
        if lowered in ("true", "false", "null"):
            return [(lowered, None)]
        if lowered == "mixed":  # the type says anything goes
            return [(v, None) for v in ("1", "0", "''", "'abc'", "[]", "null")]
        if lowered in ("array", "iterable"):
            return self._array_values(param_name, doc, usage)
        shape = self._doc_array(part)  # a docblock type standing in for a missing declared type
        return self._shape_values(shape, param_name) if shape else []  # objects, callables: no literal makes one

    def _array_values(self, param_name, doc, usage=None):
        """Values for an array parameter: from the docblock's array types, else guessed (with assumptions)."""
        shapes = [s for s in (self._doc_array(p) for p in doc_parts(doc)) if s] if doc else []
        if shapes:
            values = {}
            for value, assumption in (v for s in shapes for v in self._shape_values(s, param_name)):
                values.setdefault(value, (value, assumption))
            return list(values.values())
        kind, keys = usage or (None, [])
        if kind == "map" or (kind is None and self.family(param_name) == "map"):
            typical = self._map_literal(keys)  # which keys it needs is unknown, so any array is an assumption
            return [(v, f"{param_name} may be {v}") for v in (typical, "[]")]
        if kind == "rows":  # a list of maps: only [] is sure to fit
            row = f"[{self._map_literal(keys)}]"
            return [(row, f"{param_name} may be {row}"), ("[]", None)]
        if kind == "objects":  # a list of objects: no literal makes one
            return [("[]", None)]
        item = self._element(param_name)
        note = f"{param_name} holds {item}s (guessed from its name)"
        return [(v, None if v == "[]" else note) for v in self._list_values(item)]

    def _map_literal(self, keys):
        """['price' => 1, 'name' => 'abc'] from the keys the code reads (values guessed from the key names)."""
        if not keys:
            return "['a' => 1]"
        sample = {"string": "'abc'", "bool": "true", "list": "[1]", "map": "[]"}
        return "[" + ", ".join(f"'{k}' => {sample.get(self.family(k), '1')}" for k in keys[:6]) + "]"

    def _doc_array(self, text):
        """A docblock array type as ("list" | "map", item, non_empty) or ("shape", fields, False), else None.
        item: "int", "float", "string", "bool", "mixed", "object" (no literal makes one), or a nested type."""
        t = (text or "").strip().lstrip("?").lstrip("\\").strip()
        if t.endswith("[]"):
            inner = t[:-2].strip()
            if inner.startswith("(") and inner.endswith(")"):
                inner = inner[1:-1]
            return ("list", self._doc_item(inner), False)
        found = GENERIC_ARRAY.fullmatch(t)
        if found:
            args = split_top(found.group(3))
            non_empty = bool(found.group(1))
            if len(args) == 2 and found.group(2).lower() != "list":
                key = args[0].strip().lower()
                return ("map" if key == "string" else "list", self._doc_item(args[1]), non_empty)
            return ("list", self._doc_item(args[0]), non_empty) if len(args) == 1 else None
        found = SHAPE.fullmatch(t)
        if found:
            fields = []
            for part in split_top(found.group(1)):
                part = part.strip()
                named = SHAPE_FIELD.fullmatch(part)
                if named:
                    fields.append((named.group(2), self._doc_item(named.group(4)), bool(named.group(3))))
                elif part and part != "...":
                    fields.append((None, self._doc_item(part), False))  # array{int, string}: a tuple
            return ("shape", fields, False)
        return None

    def _doc_item(self, text):
        for part in doc_parts(text):
            nested = self._doc_array(part)
            if nested:
                return nested
            lowered = ALIASES.get(part.lower().lstrip("\\"), part.lower().lstrip("\\"))
            if lowered in SCALARS:
                return lowered
            if lowered in ("mixed", "scalar", "array-key"):
                return "mixed"
            if lowered in ("array", "iterable"):
                return ("list", "mixed", False)
        return "object"  # a class, or a refined type (positive-int...) whose limits aren't modelled here

    def _item_samples(self, item, param_name):
        """(one element, typical list contents, [boundary elements]) for list items, or None when only []
        can be written."""
        if item == "mixed":  # the docblock allows anything: the name picks a sensible element
            item = self._element(param_name)
        if isinstance(item, str):
            return (ONE[item], ELEMENTS[item], SCALARS[item][1:3]) if item in SCALARS else None
        values = [v for v, a in self._shape_values(item, param_name) if a is None]
        return (values[0], values[0], values[1:3]) if values else None

    def _shape_values(self, shape, param_name):
        kind, item, non_empty = shape
        if kind == "shape":
            return self._record_values(item, param_name)
        empty = f"{param_name} may be empty" if non_empty else None
        samples = self._item_samples(item, param_name)
        if samples is None:  # objects: only the empty array can be written
            return [("[]", empty)]
        one, contents, boundaries = samples
        if kind == "map":
            return [(f"['a' => {one}]", None), ("[]", empty)]
        return [(f"[{contents}]", None), ("[]", empty)] + [(f"[{b}]", None) for b in boundaries]

    def _record_values(self, fields, param_name):
        """array{id: int, name: string}: the typical record, then each field at one boundary value."""
        required = [(key, self._item_samples(item, param_name), item) for key, item, optional in fields
                    if not optional]
        if any(samples is None for _, samples, _ in required):
            return []  # a required field no literal can make
        def record(values):
            return "[" + ", ".join(v if key is None else f"'{key}' => {v}" for (key, _, _), v in
                                   zip(required, values)) + "]"
        typical = [samples[0] for _, samples, _ in required]
        values = [record(typical)]
        for i, (_, samples, _) in enumerate(required):
            for boundary in samples[2][:1]:
                values.append(record(typical[:i] + [boundary] + typical[i + 1:]))
        return [(v, None) for v in values[:6]]

    def _element(self, param_name):
        last = re.split(r"_|(?<=[a-z])(?=[A-Z])", param_name)[-1].lower()
        return "string" if last.endswith("s") and last[:-1] in TEXT_WORDS and last != "ids" else "int"

    @staticmethod
    def _list_values(item):
        return [f"[{ELEMENTS[item]}]", "[]"] + [f"[{v}]" for v in SCALARS[item][1:3]]

    def edge_inputs(self, fn):
        """A typical input, then one parameter at a time set to each boundary value (as in base, with the
        docblock and the code's key reads as hints). An index outside its array or string is an assumption."""
        docs = getattr(fn, "docs", {})
        options = [self.edge_values(p.type, p.name, docs.get(p.name), reads_keys(fn.text, p.name)) for p in fn.params]
        if any(not values for values in options):
            return []
        base = [values[0] for values in options]
        inputs = [Input([value for value, _ in base], assumptions=[a for _, a in base if a])]
        for index, values in enumerate(options):
            for value, assumption in values[1:]:
                args = [b for b, _ in base]
                args[index] = value
                assumptions = [a for i, (_, a) in enumerate(base) if a and i != index] + [a for a in [assumption] if a]
                inputs.append(Input(args, assumptions=assumptions))
        inputs = list({tuple(i.args): i for i in inputs}.values())[:MAX_INPUTS]
        indexes = [i for i, p in enumerate(fn.params) if p.name.lower() in INDEX_WORDS]
        for inp in inputs:
            for i in indexes:
                at = {"PHP_INT_MAX": 2 ** 63 - 1, "PHP_INT_MIN": -2 ** 63}.get(inp.args[i], inp.args[i])
                if not re.fullmatch(r"-?\d+", str(at)):
                    continue
                for j, value in enumerate(inp.args):
                    size, low = self._size(value)
                    if j != i and size is not None and not low * size <= int(at) < size:
                        inp.assumptions.append(f"{fn.params[i].name} may be out of range for {fn.params[j].name}")
        return inputs

    def _size(self, expr):
        """(length, 0 or 1) of an array or quoted string literal: strings also take negative offsets."""
        if expr[:1] in "'\"":
            return len(expr) - 2, 1
        if expr.startswith("["):
            _, node = self.expression_tree(expr)
            return (sum(c.type == "array_element_initializer" for c in node.children), 0) if node else (None, 0)
        return None, 0

    def nullable(self, type_text):
        if not type_text:
            return False
        if type_text.startswith("default:"):
            return type_text[len("default:"):].strip().lower() == "null"
        lowered = type_text.strip().lower()
        return lowered.startswith("?") or any(p.strip("() ") in ("null", "mixed") for p in split_union(lowered))

    def expected_function(self, fn):
        returns = (fn.returns or "").strip()
        if returns in ("void", "never"):
            return None
        types = (f"{returns} $value): {returns}" if returns else "$value)")
        return f"\n\nfunction {EXPECTED}({types} {{ return $value; }}\n", EXPECTED

    def model_input(self, fn, args, hypothesis):
        result = super().model_input(fn, args, hypothesis)
        if result is None:
            return None
        notes = [f"{p.name} may be null" for p, a in zip(fn.params, args)  # NULL, Null: any case is null
                 if a.strip().lower() == "null" and not self.nullable(p.type)]
        if any(p.type is None or p.type.startswith("default:") for p in fn.params):
            notes.append("the parameters have no types, so this input is assumed to be allowed")
        result.assumptions += [n for n in notes if n not in result.assumptions]
        return result

    # ---------- running ----------

    def context(self, source, fn):
        """The file with every top-level statement that isn't a declaration or setup blanked (line numbers
        stay), and classes that extend something from another file blanked too: loading them would fail."""
        root = self.parse(source).root_node
        declared = self._declared(root)
        data = bytearray(source.encode())
        for node, _, _ in self._statements(root):
            if self._keep(node, declared):
                continue
            start = node.start_byte
            if node.type == "text" and start == 0 and data.startswith(b"#!"):  # PHP skips a shebang line itself
                start = min(node.end_byte, data.find(b"\n") + 1 or len(data))
            for i in range(start, node.end_byte):
                if data[i] not in (10, 13):
                    data[i] = 32
        return data.decode(errors="replace")

    def _keep(self, node, declared):
        """Does this top-level statement stay when the file loads? Declarations and setup do."""
        kind = node.type
        if kind in CLASS_LIKE:
            return not self._missing(node, declared)
        if kind in DECLARATIONS or self._guarded(node):
            return True
        if kind == "declare_statement":
            return self.text(node).endswith(";") and all(c.type == "declare_directive" for c in node.named_children)
        if kind == "expression_statement":
            return self._setup(node) and not self._runs_program(node)
        if kind in BLOCKS:  # a loop that fills a table, `if (!defined('X'))`...: kept if all of it is setup
            if kind == "if_statement" and ENTRY_GUARD.search(self.text(node.child_by_field_name("condition")) or ""):
                return False
            return not self._runs_program(node) and all(self._setup(s) for s in self._inside(node)
                                                        if s.type == "expression_statement")
        return False  # echo, exit, return, inline HTML...

    def _setup(self, statement):
        """An expression statement that sets something up: an assignment, include, define(), ini_set()..."""
        expr = next((c for c in statement.named_children if c.type != "comment"), None)
        if expr is None or not self.text(statement).rstrip().endswith(";"):
            return False  # `<?= $x ?>` prints
        if expr.type in SETUP:
            return True
        return expr.type == "function_call_expression" and \
            (self.text(expr.child_by_field_name("function")) or "").lstrip("\\").lower() in SETUP_CALLS

    def _inside(self, node):
        """Every node under node that runs while the file loads (not the bodies of functions and classes)."""
        stack = list(node.named_children)
        while stack:
            current = stack.pop()
            if current.type in DEFERRED:
                continue
            yield current
            stack.extend(current.named_children)

    def _runs_program(self, node):
        for inner in self._inside(node):
            if inner.type in RUNS_PROGRAM or (inner.type == "throw_expression" and node.type != "expression_statement"):
                return True
            if inner.type == "function_call_expression" and \
                    (self.text(inner.child_by_field_name("function")) or "").lower() in ("die", "exit"):
                return True
        return False

    def deliberate(self, source, failure):
        """True when the failure is the code refusing the input on purpose (see the module docstring)."""
        raised = failure.get("php_error")
        if raised:  # an error the harness turned into ErrorException: trigger_error(E_USER_ERROR) is on purpose
            return raised == E_USER_ERROR
        error = str(failure.get("error") or "").rsplit("\\", 1)[-1].lower()
        line = failure.get("line")
        if not error or not isinstance(line, int):
            return False
        created, parents, functions = self._rejections(source)
        lineage = ancestors(error, parents)
        here = created.get(line, set())
        if error in here or here & set(lineage) & set(parents):  # created here (or `new static` of a subclass)
            return True
        lines = source.splitlines()
        if error == "jsonexception" and 0 < line <= len(lines) and "JSON_THROW_ON_ERROR" in lines[line - 1]:
            return True  # the code asked json_* to throw on bad input
        path = [line] + [int(n) for n in re.findall(r"main\.php\((\d+)\)", failure.get("traceback") or
                                                    failure.get("trace") or "")]
        documented = set()
        for at in path:  # @throws of the innermost function around each line on the failing path
            around = [f for f in functions if f[0] <= at <= f[1]]
            if around:
                documented |= min(around, key=lambda f: f[1] - f[0])[2]
        return error in documented or bool((set(lineage[1:]) - {"throwable", "error"}) & documented)

    @functools.lru_cache(maxsize=16)
    def _rejections(self, source):
        """({line: classes created there, lowercase}, {class: its parent}, [(first, last line, @throws)])."""
        root = self.parse(source).root_node
        parents, created = {}, {}
        for node, _, _ in self._statements(root):
            if node.type == "class_declaration":
                base = next((c for c in node.named_children if c.type == "base_clause"), None)
                names = [self.text(n) for n in base.named_children] if base is not None else []
                parents[self.text(node.child_by_field_name("name")).lower()] = \
                    names[0].rsplit("\\", 1)[-1].lower() if names else ""
        stack = [(root, None)]
        while stack:
            node, cls = stack.pop()
            if node.type in CLASS_LIKE:
                cls = (self.text(node.child_by_field_name("name")) or "").lower()
            names = []
            if node.type == "object_creation_expression":
                named = next((c for c in node.named_children if c.type in ("name", "qualified_name")), None)
                name = self.text(named).rsplit("\\", 1)[-1].lower() if named is not None else ""
                name = cls if name in ("self", "static") else parents.get(cls, "") if name == "parent" else name
                names = [name] if name else []
            elif node.type == "function_call_expression" and \
                    self.text(node.child_by_field_name("function")) in ("assert", "\\assert"):
                names = ["assertionerror"]
            for line in range(node.start_point[0] + 1, node.end_point[0] + 2) if names else ():
                created.setdefault(line, set()).update(names)
            stack.extend((child, cls) for child in node.named_children)
        functions = [(f.line, f.end_line, set(f.throws)) for f in self.functions(source)]
        return created, parents, functions

    def program(self, source, fn, inputs):
        thunks = ",\n    ".join(f"fn() => [{', '.join(i.args)}]" for i in inputs)
        harness = f"""<?php
declare(strict_types=1);
// Phantom's harness: runs input $argv[1] against {fn.name}() from main.php and prints one verdict line.
$__phantomInputs = [
    {thunks}
];
$__phantomDone = false;
$__phantomSay = function (array $verdict) use (&$__phantomDone) {{
    $__phantomDone = true;
    $flags = JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE | JSON_PARTIAL_OUTPUT_ON_ERROR;
    fwrite(STDOUT, "\\n{RESULT_MARK}" . json_encode($verdict, $flags) . "\\n");
}};
register_shutdown_function(function () use (&$__phantomDone, $__phantomSay) {{
    $last = error_get_last();  // a fatal error is read from stderr; anything else ending early is exit()
    $fatal = $last !== null && ($last['type'] & (E_ERROR | E_CORE_ERROR | E_COMPILE_ERROR | E_PARSE));
    if (!$__phantomDone && !$fatal) {{
        $__phantomSay(['ok' => false, 'kind' => 'exit', 'error' => 'Exit',
                       'message' => 'the code ended the program itself (exit or die)']);
    }}
}});
ob_start(fn($output) => '', 65536);  // the function's own output is dropped: only the verdict counts
// each input runs its own copy of main.php in its own directory: files one input writes can't change the next
$__phantomRun = __DIR__ . '/run-' . preg_replace('/\\W/', '', $argv[1]);
@mkdir($__phantomRun);
copy(__DIR__ . '/main.php', $__phantomRun . '/main.php');
chdir($__phantomRun);
putenv('TMPDIR=' . $__phantomRun);
require $__phantomRun . '/main.php';
if ($argv[1] === 'build') {{
    $__phantomDone = true;
    exit(0);
}}
try {{
    $__phantomArgs = $__phantomInputs[(int) $argv[1]]();
}} catch (Throwable $e) {{
    $__phantomSay(['ok' => false, 'kind' => 'invalid_input', 'error' => get_class($e), 'message' => $e->getMessage()]);
    exit(0);
}}
touch(__DIR__ . '/.entered');  // the arguments are built: from here on a failure belongs to the function
$__phantomRaised = null;
set_error_handler(function (int $severity, string $message, string $file, int $line) use (&$__phantomRaised) {{
    if (!(error_reporting() & $severity)) {{
        return false;  // silenced with @
    }}
    if ($severity & (E_USER_WARNING | E_USER_NOTICE | E_USER_DEPRECATED)) {{
        return true;  // the code's own trigger_error(): it reports something and carries on
    }}
    if ($severity === E_DEPRECATED
            && !preg_match('/Implicit conversion|Passing null to parameter|Automatic conversion of false|crement on/', $message)) {{
        return true;  // about the code (a deprecated function), not about the input
    }}
    $names = [E_WARNING => 'Warning', E_NOTICE => 'Notice', E_DEPRECATED => 'Deprecated', E_USER_ERROR => 'Error'];
    throw $__phantomRaised = new ErrorException(($names[$severity] ?? 'Error') . ': ' . $message, 0, $severity, $file, $line);
}});
try {{
    $__phantomResult = \\{fn.name}(...$__phantomArgs);
    restore_error_handler();
    try {{
        $flags = JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION
            | JSON_PARTIAL_OUTPUT_ON_ERROR;
        $shown = is_array($__phantomResult) || is_object($__phantomResult)
            ? json_encode($__phantomResult, $flags, 16) : var_export($__phantomResult, true);
    }} catch (Throwable $e) {{
        $shown = false;
    }}
    $shown = is_string($shown) ? $shown : get_debug_type($__phantomResult);
    $__phantomSay(['ok' => true, 'result' => strlen($shown) > 300 ? substr($shown, 0, 297) . '...' : $shown]);
}} catch (Throwable $e) {{
    restore_error_handler();
    $message = $e->getMessage();
    $inMain = basename($e->getFile()) === 'main.php';
    $line = null;
    if (preg_match('/called in \\S*main\\.php on line (\\d+)/', $message, $m)) {{
        $line = (int) $m[1];  // a bad argument between two of the user's functions: the call is the bug
    }} elseif ($inMain) {{
        $line = $e->getLine();
    }} else {{
        foreach ($e->getTrace() as $frame) {{
            if (basename($frame['file'] ?? '') === 'main.php') {{
                $line = $frame['line'];
                break;
            }}
        }}
    }}
    $elsewhere = '/^(Call to undefined function|(Class|Interface|Trait|Enum) "[^"]+" not found|Undefined constant ")/';
    if ($e instanceof TypeError && preg_match('/(called|passed) in \\S*harness\\.php/', $message)) {{
        $kind = 'invalid_input';  // the input doesn't fit the declared parameter types
    }} elseif ($e instanceof Error && preg_match($elsewhere, $message)) {{
        $kind = 'unavailable';  // defined in another file
    }} elseif ($e instanceof TypeError && str_contains($message, 'Return value must be of type')) {{
        $kind = 'return_type';
    }} else {{
        $kind = 'exception';
    }}
    $paths = [$__phantomRun . '/' => '', __DIR__ . '/' => ''];
    $trace = $e->getFile() . ':' . $e->getLine() . "\\n" . $e->getTraceAsString();
    $__phantomSay(['ok' => false, 'kind' => $kind, 'error' => get_class($e), 'line' => $line,
                   'php_error' => $e === $__phantomRaised ? $e->getSeverity() : null,
                   'message' => substr(strtr($message, $paths), 0, 2000),
                   'trace' => substr(strtr($trace, $paths), 0, 4000)]);
}}
"""
        return {"files": {"main.php": source, "harness.php": harness},
                "build": f"timeout -k 1 {BUILD_LIMIT} {PHP} -d max_execution_time={BUILD_LIMIT} harness.php build",
                "run": f"{PHP} -d max_execution_time={CPU_LIMIT} harness.php {{i}}"}

    def classify(self, code, out, err):
        fatal = FATAL.search(err or "")
        if fatal and RESULT_MARK not in out:  # a PHP fatal error: no exception, no verdict
            message, path, line = fatal.groups()
            line = int(line) if path.endswith("main.php") else None
            if message.startswith("Maximum execution time"):
                return {"ok": False, "kind": "timeout", "error": "Timeout", "line": line,
                        "message": f"no result after {CPU_LIMIT} s of CPU time (possible infinite loop)"}
            error = "FatalError"
            if message.startswith("Allowed memory size"):  # a 256 KiB request is a new page of the call stack
                error = "StackOverflow" if "(tried to allocate 262144 bytes)" in message else "MemoryExhausted"
                message = f"{message} (infinite recursion?)" if error == "StackOverflow" else message
            return {"ok": False, "kind": "crash", "error": error, "message": message, "line": line,
                    "traceback": err[-4000:]}
        result = super().classify(code, out, err)
        if result.get("error") == "Error" and str(result.get("message", "")).startswith("Maximum call stack size"):
            result["error"] = "StackOverflow"
        return result

    def trace_line(self, text):
        hits = re.findall(r"main\.php(?::| on line |\()(\d+)", text or "")
        return int(hits[0]) if hits else None


def qualify(namespace, name):
    return f"{namespace}\\{name}" if namespace else name


def ancestors(name, parents):
    """[name, its parent, ...]: classes declared in the file first, then PHP's own exception classes."""
    chain = []
    while name and name not in chain:
        chain.append(name)
        name = parents.get(name) or BUILTIN_PARENTS.get(name)
    return chain


def doc_parts(text):
    """The alternatives of a docblock type without null: 'string[]|null' -> ['string[]'], '?int' -> ['int']."""
    return [p.strip().lstrip("?").strip() for p in split_union((text or "").strip())
            if p.strip().lstrip("?").strip().lower() not in ("null", "")]


def split_top(text):
    """'string, array<int, int>' -> ['string', 'array<int, int>']: commas outside <...>, {...}, (...)."""
    parts, depth, current = [], 0, ""
    for ch in text:
        depth += ch in "<({[" and 1 or ch in ">)}]" and -1 or 0
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [p.strip() for p in parts + [current] if p.strip()]


def reads_keys(text, param_name):
    """How the code reads $param: ("map", keys) for $p['name'], ("rows", keys) for string keys read from its
    items (foreach ($p as $row) ... $row['id'], or $p[$i]['id']), ("objects", []) for $item->x, else None."""
    text, p = text or "", re.escape(param_name)
    if re.search(rf"\${p}\s*\[\s*['\"]", text):
        return ("map", unique(re.findall(rf"\${p}\s*\[\s*['\"](\w+)['\"]\s*\]", text)))
    keyed = re.search(rf"\${p}\s*\[[^\]'\"]*\]\s*\[\s*['\"]", text) is not None
    rows = re.findall(rf"\${p}\s*\[[^\]'\"]*\]\s*\[\s*['\"](\w+)['\"]\s*\]", text)
    objects = re.search(rf"\${p}\s*\[[^\]'\"]*\]\s*->", text) is not None
    for item in re.findall(rf"foreach\s*\(\s*\${p}\s+as\s+(?:&?\$\w+\s*=>\s*)?&?\$(\w+)", text):
        keyed = keyed or re.search(rf"\${item}\s*\[\s*['\"]", text) is not None
        rows += re.findall(rf"\${item}\s*\[\s*['\"](\w+)['\"]\s*\]", text)
        objects = objects or re.search(rf"\${item}\s*->", text) is not None
    if keyed:
        return ("rows", unique(rows))
    return ("objects", []) if objects else None


def unique(items):
    return list(dict.fromkeys(items))
