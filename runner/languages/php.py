"""PHP: top-level functions (also inside namespaces) and public static methods, run by PHP 8.4 CLI.

Inputs are PHP literals: numbers, strings without interpolation, true/false/null and arrays.
Declared types decide the values: PHP enforces them, so a typed parameter never sees null unless
its type allows it. `array` says nothing about its contents, so a docblock (@param int[] $values,
array<string, int> $counts) or the parameter's name decides; an array whose keys the code reads
($user['name']) without a docblock is a map with unknown keys, so every value of it is an
assumption. Untyped parameters work like plain JavaScript: kind from the docblock, name or default.

The harness is a second file, harness.php, with strict_types: an input of the wrong type fails at
the call ("called in harness.php") and is dropped as an invalid input, never reported. It loads
main.php, turns every warning, notice and deprecation into an ErrorException ("Undefined array
key"...) and reports the innermost line in main.php. Not reported either: an exception the code
creates on purpose (`throw new X` or a failed assert() on that line: the code rejects the input)
and calls to functions or classes that live in other files. Fatal errors (memory, a recursion
that exhausts it, the 1 s CPU limit) come from stderr. The build step compiles the harness and
loads main.php once, so a broken file or a literal PHP rejects (like "\\u{110000}") fails there.
"""
import re
from dataclasses import dataclass, field

from runner.languages.base import MAX_INPUTS, RESULT_MARK, Function, Input, Language, Param
from runner.languages.javascript import TEXT_WORDS, JavaScript, split_union

PHP = "php -n -d memory_limit=128M -d display_errors=stderr -d log_errors=0"
CPU_LIMIT = 1  # max_execution_time: CPU seconds, so a loop is stopped (with its line) before the wall timeout
CLASS_LIKE = ("class_declaration", "interface_declaration", "trait_declaration", "enum_declaration")
KEEP = frozenset({"php_tag", "function_definition", "namespace_definition", "namespace_use_declaration",
                  "const_declaration", "comment", *CLASS_LIKE})
INDEX_WORDS = ("index", "idx", "i", "j", "pos", "position", "offset")
SCALARS = {  # typical value first, then boundary values of the same type
    "int": ["1", "0", "-1"],
    "float": ["1.5", "0.0", "-1.0"],
    "string": ["'abc'", "''", "' '", "'a:b'"],
    "bool": ["true", "false"],
}
ELEMENTS = {"int": "1, 2, 3", "float": "1.5, 2.5", "string": "'a', 'b', 'c'", "bool": "true, false"}
ALIASES = {"integer": "int", "double": "float", "boolean": "bool", "list": "array"}
EXTREMES = [("PHP_INT_MAX", "may be as large as PHP_INT_MAX"), ("PHP_INT_MIN", "may be as small as PHP_INT_MIN")]
GUARD = re.compile(r"\(\s*!\s*function_exists\s*\(\s*(['\"])[\\\w]+\1\s*\)\s*\)")
FATAL = re.compile(r"(?:PHP )?Fatal error:\s+(.+?) in (\S+) on line (\d+)")
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
        return found

    def _function(self, node, name, previous):
        params, skip = [], None
        docs = self._docs(previous, node)
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
        return PhpFunction(name=name, line=node.start_point[0] + 1, end_line=node.end_point[0] + 1, params=params,
                           returns=self.text(node.child_by_field_name("return_type")), text=self.text(node),
                           skip=skip, docs=docs)

    def _docs(self, previous, node):
        """@param types from the /** docblock */ right above the function."""
        if previous is None or previous.type != "comment" or not self.text(previous).startswith("/**") \
                or previous.end_point[0] < node.start_point[0] - 1:
            return {}
        return {name: kind.strip() for kind, name in
                re.findall(r"@param\s+(.+?)\s+&?(?:\.\.\.)?\$(\w+)", self.text(previous))}

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

    def edge_values(self, type_text, param_name, doc=None, keys=False):
        """keys: the function reads string keys from this parameter, so it is a map."""
        default = type_text[len("default:"):].strip() if type_text and type_text.startswith("default:") else None
        if type_text and default is None:
            return self.typed_values(type_text, param_name, doc, keys)
        null = ("null", None if self.nullable(type_text) else f"{param_name} may be null")
        documented = self.typed_values(doc, param_name, doc, keys) if doc else []
        if documented:  # an untyped parameter with a documented type: the docblock is the contract
            return documented + ([null] if null[0] not in dict(documented) else [])
        kind = self.family(param_name, default)
        if kind == "list" and (keys or self.family(param_name) == "map"):
            kind = "map"
        if kind is None:  # nothing to go on: try every kind, each one an assumption
            values = [(v, f"{param_name} may be {v}") for v in ("1", "0", "-1", "''", "'abc'", "[]")]
        elif kind == "map":  # which keys the code reads is unknown, so any array is an assumption
            values = [(v, f"{param_name} may be {v}") for v in ("['a' => 1]", "[]")]
        elif kind == "list":
            values = [(v, None) for v in self._list_values(self._element(param_name))]
        else:
            values = [(v, None) for v in SCALARS[kind]]
        return values + [null]

    def typed_values(self, type_text, param_name, doc=None, keys=False):
        """Values a declared type allows, typical first; only extreme integers carry an assumption."""
        values = []
        for part in split_union(type_text.strip().lstrip("?")):
            values += self._typed_part(part.strip().strip("()"), param_name, doc, keys)
        if self.nullable(type_text) and type_text.strip().lower() != "mixed":
            values.append(("null", None))
        unique = {}
        for value, assumption in values:
            unique.setdefault(value, (value, assumption))
        return list(unique.values())

    def _typed_part(self, part, param_name, doc, keys):
        lowered = ALIASES.get(part.lower(), part.lower())
        if lowered in SCALARS:
            extremes = [(v, f"{param_name} {note}") for v, note in EXTREMES] if lowered == "int" else []
            return [(v, None) for v in SCALARS[lowered]] + extremes
        if lowered in ("true", "false", "null"):
            return [(lowered, None)]
        if lowered == "mixed":  # the type says anything goes
            return [(v, None) for v in ("1", "0", "''", "'abc'", "[]", "null")]
        if lowered in ("array", "iterable") or self._array_doc(part):
            return self._array_values(param_name, doc if self._array_doc(doc or "") else part, keys)
        return []  # objects, callables, classes: no literal makes one

    @staticmethod
    def _array_doc(text):
        return bool(re.fullmatch(r"\w+\[\]|(non-empty-)?(array|list|iterable)<.+>", text.strip()))

    def _array_values(self, param_name, doc, keys):
        found = re.fullmatch(r"(\w+)\[\]|(non-empty-)?(array|list|iterable)<(?:(\w+),\s*)?(.+)>", doc.strip())
        if found is None:  # a plain `array`: the name or the code's key reads decide
            if keys or self.family(param_name) == "map":
                return [(v, f"{param_name} may be {v}") for v in ("['a' => 1]", "[]")]
            return [(v, None) for v in self._list_values(self._element(param_name))]
        item = (found.group(1) or found.group(5)).lower()
        item = self._element(param_name) if item in ("mixed", "scalar") else ALIASES.get(item, item)
        sample = {"string": "'abc'"}.get(item, SCALARS.get(item, [None])[0])
        if sample is None:  # objects or nested types: only the empty array can be written
            values = ["[]"]
        elif (found.group(4) or "").lower() == "string":
            values = [f"['a' => {sample}]", "[]"]
        else:
            values = self._list_values(item)
        empty = f"{param_name} may be empty" if found.group(2) else None
        return [(v, empty if v == "[]" else None) for v in values]

    def _element(self, param_name):
        last = re.split(r"_|(?<=[a-z])(?=[A-Z])", param_name)[-1].lower()
        return "string" if last.endswith("s") and last[:-1] in TEXT_WORDS else "int"

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
        """The file with every top-level statement blanked except declarations (line numbers stay), and
        classes that extend something from another file blanked too: loading them would fail."""
        root = self.parse(source).root_node
        declared = self._declared(root)
        data = bytearray(source.encode())
        for node, _, _ in self._statements(root):
            simple_declare = node.type == "declare_statement" and self.text(node).endswith(";") and \
                all(c.type == "declare_directive" for c in node.named_children)
            if simple_declare or self._guarded(node) or \
                    (node.type in KEEP and not (node.type in CLASS_LIKE and self._missing(node, declared))):
                continue
            for i in range(node.start_byte, node.end_byte):
                if data[i] not in (10, 13):
                    data[i] = 32
        return data.decode(errors="replace")

    def _deliberate(self, source):
        """{line: exception class names} where main.php throws on purpose: `throw new X` and assert()."""
        lines, stack = {}, [self.parse(source).root_node]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            names = []
            if node.type == "throw_expression":
                for created in (c for c in node.named_children if c.type == "object_creation_expression"):
                    names += [self.text(n).rsplit("\\", 1)[-1] for n in created.named_children
                              if n.type in ("name", "qualified_name")][:1]
            elif node.type == "function_call_expression" and \
                    self.text(node.child_by_field_name("function")) in ("assert", "\\assert"):
                names = ["AssertionError"]
            for line in range(node.start_point[0] + 1, node.end_point[0] + 2) if names else ():
                lines.setdefault(line, set()).update(n for n in names if re.fullmatch(r"\w+", n))
        return {line: sorted(names) for line, names in lines.items() if names}

    def program(self, source, fn, inputs):
        thunks = ",\n    ".join(f"fn() => [{', '.join(i.args)}]" for i in inputs)
        throws = ", ".join(f"{line} => [{', '.join(repr(n) for n in names)}]"
                           for line, names in self._deliberate(source).items())
        harness = f"""<?php
declare(strict_types=1);
// Phantom's harness: runs input $argv[1] against {fn.name}() from main.php and prints one verdict line.
$__phantomInputs = [
    {thunks}
];
$__phantomThrows = [{throws}];
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
        $__phantomSay(['ok' => true, 'result' => 'exit() was called']);
    }}
}});
ob_start(fn($output) => '', 65536);  // the function's own output is dropped: only the verdict counts
require __DIR__ . '/main.php';
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
set_error_handler(function (int $severity, string $message, string $file, int $line) {{
    if (!(error_reporting() & $severity)) {{
        return false;  // silenced with @
    }}
    $names = [E_WARNING => 'Warning', E_NOTICE => 'Notice', E_DEPRECATED => 'Deprecated',
              E_USER_WARNING => 'Warning', E_USER_NOTICE => 'Notice', E_USER_DEPRECATED => 'Deprecated'];
    throw new ErrorException(($names[$severity] ?? 'Error') . ': ' . $message, 0, $severity, $file, $line);
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
    $short = substr(strrchr('\\\\' . get_class($e), '\\\\'), 1);
    $elsewhere = '/^(Call to undefined function|(Class|Interface|Trait|Enum) "[^"]+" not found|Undefined constant ")/';
    if ($e instanceof TypeError && preg_match('/(called|passed) in \\S*harness\\.php/', $message)) {{
        $kind = 'invalid_input';  // the input doesn't fit the declared parameter types
    }} elseif ($e instanceof Error && preg_match($elsewhere, $message)) {{
        $kind = 'unavailable';  // defined in another file
    }} elseif ($inMain && in_array($short, $__phantomThrows[$e->getLine()] ?? [], true)) {{
        $kind = 'rejected';  // thrown on purpose by the code: it refuses this input
    }} elseif ($e instanceof TypeError && str_contains($message, 'Return value must be of type')) {{
        $kind = 'return_type';
    }} else {{
        $kind = 'exception';
    }}
    $trace = $e->getFile() . ':' . $e->getLine() . "\\n" . $e->getTraceAsString();
    $__phantomSay(['ok' => false, 'kind' => $kind, 'error' => get_class($e), 'line' => $line,
                   'message' => substr(str_replace(__DIR__ . '/', '', $message), 0, 2000),
                   'trace' => substr(str_replace(__DIR__ . '/', '', $trace), 0, 4000)]);
}}
"""
        return {"files": {"main.php": source, "harness.php": harness}, "build": f"{PHP} harness.php build",
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


def reads_keys(text, param_name):
    """Does the code read string keys from $param ($user['name'])? Then it is a map, not a list."""
    return bool(re.search(rf"\${re.escape(param_name)}\s*\[\s*['\"]", text or ""))
