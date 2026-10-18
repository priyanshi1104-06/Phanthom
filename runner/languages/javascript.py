"""JavaScript and TypeScript: top-level functions run by Node 24 (TypeScript types are stripped by Node).

Inputs are JavaScript literals. In plain JavaScript nothing says what a parameter accepts, so the
typical value comes from the parameter's name or default, and a finding that needs a different kind
of value (or null/undefined) carries that as an assumption. TypeScript types decide this exactly.
"""
import re

from runner.languages.base import RESULT_MARK, Function, Language, Param

LIST_WORDS = ("values", "items", "list", "arr", "array", "nums", "numbers", "elements", "entries", "rows", "xs")
TEXT_WORDS = ("name", "str", "text", "word", "address", "path", "url", "key", "email", "label", "title",
              "message", "msg", "input", "line", "query", "s", "code", "id")
NUMBER_WORDS = ("n", "i", "j", "k", "count", "index", "idx", "num", "x", "y", "size", "len", "length", "age",
                "total", "amount", "price", "limit", "max", "min", "start", "end", "cents", "people", "year",
                "rate", "step", "depth", "level", "port", "score", "qty", "quantity", "width", "height")
OBJECT_WORDS = ("obj", "user", "config", "options", "opts", "data", "item", "record", "person", "params",
                "settings", "payload", "event", "req", "request", "res", "response", "map", "dict")

FAMILY_VALUES = {  # typical value first, then boundary values of the same kind
    "array": ["[1, 2, 3]", "[]", "[0]", "[-1]"],
    "string": ['"abc"', '""', '" "', '"a:b"'],
    "number": ["3", "0", "-1", "0.5"],
    "boolean": ["true", "false"],
    "object": ['{"a": 1}', "{}"],
}


class JavaScript(Language):
    name = "javascript"
    title = "JavaScript"
    vscode_ids = ("javascript",)
    extensions = (".js", ".mjs", ".cjs")
    grammar = "javascript"
    image = "node:24-slim"
    local_tool = "node"
    memory = "512m"
    file_name = "main.js"
    null_words = ("null", "undefined")
    literal_hint = "numbers, strings, arrays, objects, true/false, null, undefined, NaN, Infinity"
    literal_nodes = frozenset({
        "number", "string", "string_fragment", "escape_sequence", "template_string", "true", "false", "null",
        "undefined", "array", "object", "pair", "property_identifier", "unary_expression",
        "parenthesized_expression",
    })
    literal_names = frozenset({"NaN", "Infinity"})

    def expression_tree(self, expr):
        tree = self.parse(f"({expr});")
        statement = tree.root_node.named_children[0] if tree.root_node.named_children else None
        inner = statement.named_children[0] if statement is not None and statement.named_children else None
        node = inner.named_children[0] if inner is not None and inner.named_children else None
        if node is not None and node.type == "unary_expression" and self.text(node)[:1] not in "-+":
            return tree, None  # only a sign in front of a number: no !, typeof, void, delete
        return tree, node

    # statements kept when the file runs in the sandbox; calls like main() at the top level are blanked
    keep_top_level = frozenset({
        "function_declaration", "generator_function_declaration", "class_declaration", "lexical_declaration",
        "variable_declaration", "import_statement", "export_statement", "interface_declaration",
        "type_alias_declaration", "enum_declaration", "abstract_class_declaration", "ambient_declaration",
        "internal_module", "module",
    })

    def context(self, source, fn):
        return self.blank_top_level(source, self.keep_top_level)

    # ---------- functions ----------

    def functions(self, source):
        tree = self.parse(source)
        found = []
        for node in tree.root_node.named_children:
            if node.type == "export_statement":
                node = node.child_by_field_name("declaration") or (node.named_children or [node])[0]
            if node.type == "function_declaration":
                found.append(self._function(node, node.child_by_field_name("name"), node))
            elif node.type in ("lexical_declaration", "variable_declaration"):
                for declarator in node.named_children:
                    value = declarator.child_by_field_name("value")
                    if declarator.type == "variable_declarator" and value is not None \
                            and value.type in ("arrow_function", "function_expression", "function"):
                        found.append(self._function(value, declarator.child_by_field_name("name"), node))
            elif node.type in ("generator_function_declaration",):
                fn = self._function(node, node.child_by_field_name("name"), node)
                fn.skip = "generator functions are not run yet"
                found.append(fn)
        return [f for f in found if f is not None]

    def _function(self, node, name_node, outer):
        if name_node is None or name_node.type != "identifier":
            return None
        params, skip = [], None
        holder = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
        children = holder.named_children if holder is not None and holder.type == "formal_parameters" \
            else [holder] if holder is not None else []
        for child in children:
            param = self._param(child)
            if param is None:
                skip = f"parameter {self.text(child)!r} is not supported yet"
            else:
                params.append(param)
        returns = node.child_by_field_name("return_type")
        return Function(
            name=self.text(name_node), line=outer.start_point[0] + 1, end_line=outer.end_point[0] + 1,
            params=params, returns=self._type_text(returns), text=self.text(outer), skip=skip,
        )

    def _param(self, node):
        if node.type == "identifier":
            return Param(self.text(node), None)
        if node.type == "assignment_pattern":  # x = default: its default tells the kind of value
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if left is None or left.type != "identifier":
                return None
            return Param(self.text(left), f"default:{self.text(right)}")
        if node.type in ("required_parameter", "optional_parameter"):  # TypeScript
            pattern = node.child_by_field_name("pattern")
            if pattern is None or pattern.type != "identifier":
                return None
            kind = self._type_text(node.child_by_field_name("type"))
            default = node.child_by_field_name("value")
            if kind is None:  # untyped: like JavaScript, the default (if any) tells the kind
                return Param(self.text(pattern), f"default:{self.text(default)}" if default is not None else None)
            if node.type == "optional_parameter" or default is not None:
                kind = f"{kind} | undefined"
            return Param(self.text(pattern), kind)
        return None  # destructuring, rest parameters: later

    def _type_text(self, node):
        text = self.text(node)
        return text.lstrip(":").strip() if text else None

    # ---------- inputs ----------

    def family(self, param_name, type_text):
        if type_text and type_text.startswith("default:"):
            default = type_text[len("default:"):].strip()
            if default.startswith("["):
                return "array"
            if default[:1] in "'\"`":
                return "string"
            if default in ("true", "false"):
                return "boolean"
            if default.startswith("{"):
                return "object"
            if re.fullmatch(r"-?[\d.]+(e-?\d+)?", default):
                return "number"
            return None
        lowered = param_name.lower()
        parts = re.split(r"_|(?<=[a-z])(?=[A-Z])", param_name)
        last = parts[-1].lower() if parts else lowered
        if lowered.startswith(("is", "has", "should", "can")) and len(param_name) > 2 and param_name[2:3].isupper():
            return "boolean"
        if last in LIST_WORDS or (last.endswith("s") and len(last) > 3 and last[:-1] in TEXT_WORDS + NUMBER_WORDS):
            return "array"
        if last in NUMBER_WORDS:
            return "number"
        if last in TEXT_WORDS:
            return "string"
        if last in OBJECT_WORDS:
            return "object"
        return None

    def edge_values(self, type_text, param_name):
        if type_text and not type_text.startswith("default:"):
            return self.typed_values(type_text)
        kind = self.family(param_name, type_text)
        if kind is None:  # nothing to go on: try every kind, each one an assumption
            values = [(v, f"{param_name} may be {v}") for v in ("1", "0", "-1", '""', '"abc"', "[]", "{}")]
        elif kind == "object":  # which fields the code reads is unknown, so any object is an assumption
            values = [(v, f"{param_name} may be {v}") for v in FAMILY_VALUES[kind]]
        else:
            values = [(v, None) for v in FAMILY_VALUES[kind]]
        values += [("null", f"{param_name} may be null"), ("undefined", f"{param_name} may be undefined")]
        return values

    def typed_values(self, type_text):
        """TypeScript: values the declared type allows, typical first. No assumptions needed."""
        options = [part.strip() for part in split_union(type_text)]
        values = []
        for option in options:
            values += self._typed_option(option)
        if not values:
            return []
        unique = list(dict.fromkeys(values))
        return [(v, None) for v in unique]

    def _typed_option(self, option):
        if option == "number":
            return FAMILY_VALUES["number"]
        if option == "string":
            return FAMILY_VALUES["string"]
        if option == "boolean":
            return FAMILY_VALUES["boolean"]
        if option in ("null", "undefined"):
            return [option]
        if option.endswith("[]") or option.startswith(("Array<", "ReadonlyArray<")):
            inner = option[:-2] if option.endswith("[]") else option[option.index("<") + 1:-1]
            inner_values = self._typed_option(inner.strip().strip("()")) or []
            return ([f"[{inner_values[0]}, {inner_values[0]}]"] if inner_values else []) + ["[]"] + \
                ([f"[{inner_values[1]}]"] if len(inner_values) > 1 else [])
        if re.fullmatch(r"-?\d+(\.\d+)?", option) or option[:1] in "'\"":
            return [option]  # a literal type
        if option in ("any", "unknown"):
            return ["1", "0", '""', "[]", "{}", "null"]
        if option.startswith("Record<string") or option in ("object", "Object"):
            return ['{"a": 1}', "{}"]
        return []  # interfaces and classes: their shape isn't known here

    def nullable(self, type_text):
        return bool(type_text) and not type_text.startswith("default:") and \
            any(part.strip() in ("null", "undefined", "any", "unknown") for part in split_union(type_text))

    def model_input(self, fn, args, hypothesis):
        result = super().model_input(fn, args, hypothesis)
        if result is not None and any(p.type is None or p.type.startswith("default:") for p in fn.params):
            note = "the parameters have no types, so this input is assumed to be allowed"
            if note not in result.assumptions:
                result.assumptions.append(note)
        return result

    # ---------- running ----------

    def program(self, source, fn, inputs):
        thunks = ",\n    ".join(f"() => [{', '.join(i.args)}]" for i in inputs)
        declared = (fn.returns or "").strip()
        check = declared if declared in ("number", "string", "boolean") else ""
        harness = f"""
;(async () => {{
  const __phantomInputs = [
    {thunks}
  ];
  const __phantomShow = (v) => {{
    try {{
      if (v === undefined) return "undefined";
      if (typeof v === "number" && !Number.isFinite(v)) return String(v);
      if (typeof v === "bigint") return v + "n";
      const s = JSON.stringify(v);
      return s === undefined ? String(v) : s.length > 300 ? s.slice(0, 297) + "..." : s;
    }} catch (e) {{ return String(v); }}
  }};
  let __phantomVerdict;
  try {{
    let __phantomResult = {fn.name}(...__phantomInputs[Number(process.argv[2])]());
    if (__phantomResult && typeof __phantomResult.then === "function") __phantomResult = await __phantomResult;
    const __phantomDeclared = {check!r};
    if (__phantomDeclared && (__phantomResult === undefined || __phantomResult === null)) {{
      __phantomVerdict = {{ ok: false, kind: "return_type", error: "ReturnTypeError",
                  message: "returned " + __phantomShow(__phantomResult) + ", but the declared return type is " + __phantomDeclared }};
    }} else {{
      __phantomVerdict = {{ ok: true, result: __phantomShow(__phantomResult) }};
    }}
  }} catch (__phantomError) {{
    const e = __phantomError, isError = e instanceof Error;
    __phantomVerdict = {{ ok: false, kind: "exception", error: isError ? e.constructor.name : typeof e,
                message: isError ? e.message : String(e), trace: isError && e.stack ? String(e.stack) : "" }};
  }}
  process.stdout.write("\\n{RESULT_MARK}" + JSON.stringify(__phantomVerdict) + "\\n");
  process.exit(0);
}})();
"""
        return {"files": {self.file_name: source + "\n" + harness}, "build": None,
                "run": f"node --no-warnings {self.file_name} {{i}}"}


class TypeScript(JavaScript):
    name = "typescript"
    title = "TypeScript"
    vscode_ids = ("typescript",)
    extensions = (".ts", ".mts", ".cts")
    grammar = "typescript"
    file_name = "main.ts"


def split_union(type_text):
    """'number | null' -> ['number', 'null'], ignoring | inside <...>, (...), {...}."""
    parts, depth, current = [], 0, ""
    for ch in type_text:
        if ch in "<({[":
            depth += 1
        elif ch in ">)}]":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]
