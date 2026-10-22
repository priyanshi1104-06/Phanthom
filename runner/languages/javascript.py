"""JavaScript and TypeScript: top-level functions run by Node 24 (TypeScript through --experimental-transform-types,
so enums, namespaces and parameter properties run too).

Inputs are JavaScript literals. TypeScript types decide exactly which values a parameter accepts. In plain
JavaScript a JSDoc type (`@param {number[]} values`) or a default value (`lo = 0`) does the same (JavaScript
callers aren't type-checked, so null and undefined are still tried, as assumptions). Otherwise the kind of
value is only guessed from the parameter's name, and every value carries that guess as an assumption
("values is an array (guessed from its name)"): a finding on it is a warning, not an error.

The sandbox gets the function and what it needs (context()): the declarations, imports and classes it uses,
the statements that fill a table it uses, global setup, "use strict". Blanked: bare calls that run the
program or print (`main()`, `app.listen(...)`, `console.log(...)`), entry-point guards
(`if (require.main === module)`), and what only other functions use (an unused `import "./money"`).

The harness compiles each input's text on its own, creates `.entered`, then calls the function, so a
failure before the call (loading the file, a missing module, a literal Node can't compile) is not_entered:
never a finding. Not findings either: a failure on a `throw` line (the code rejects the input on purpose),
a thrown value that isn't an Error, process.exit() (kind exit), a module the function itself requires
that isn't in the sandbox. A function declared `never` (or `@returns {never}`) or named main isn't run.
"""
import json
import re

from runner.languages.base import EXPECTED, RESULT_MARK, Function, Language, Param

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
A_KIND = {"array": "an array", "string": "a string", "number": "a number", "boolean": "a boolean",
          "object": "an object"}

NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram",
    "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream",
    "string_decoder", "sys", "test", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi",
    "worker_threads", "zlib", "sqlite",
})
# a bare top-level call statement that runs the program or prints demo output: blanked
PROGRAM_ROOTS = frozenset({"console", "process"})
PROGRAM_METHODS = frozenset({"listen", "serve", "start", "run", "main", "launch", "exec", "execute"})
ENTRY_GUARD = re.compile(r"\brequire\.main\b|\bimport\.meta\.(main|url|filename)\b|\bprocess\.argv\b")
TYPE_ONLY = frozenset({"interface_declaration", "type_alias_declaration", "ambient_declaration"})
FLOW = frozenset({"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                  "try_statement", "switch_statement", "statement_block", "labeled_statement"})
FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
INERT = frozenset({  # expressions that can't fail or touch anything else as the file loads
    "number", "string", "string_fragment", "escape_sequence", "template_string", "regex", "regex_pattern",
    "regex_flags", "true", "false", "null", "undefined", "array", "object", "pair", "property_identifier",
    "unary_expression", "binary_expression", "parenthesized_expression", "ternary_expression", "comment",
})
NOT_LITERAL_TYPES = frozenset({  # no plain literal is one of these
    "Map", "Set", "WeakMap", "WeakSet", "Date", "RegExp", "Promise", "Function", "Error", "URL", "Buffer",
    "Uint8Array", "Int8Array", "Uint16Array", "Int16Array", "Uint32Array", "Int32Array", "Float32Array",
    "Float64Array", "ArrayBuffer", "Symbol", "symbol",
})
LEGACY_NUMBER = re.compile(r"0[0-9_]")  # 010, 07.5, 0_1: SyntaxErrors in strict code (TS, ESM)
BAD_ESCAPE = re.compile(r"\\([1-9]|0[0-9])")  # legacy octal escapes, \8, \9
REJECTS = r"(?<![\w$.])throw\b"


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
    node_flags = "--no-warnings"
    marks_entry = True  # the harness creates .entered once the arguments are built, right before the call
    rejection_pattern = REJECTS  # see deliberate()
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
        return tree, node

    def _literal_node(self, node):
        """Plain literals only, at any depth: a sign only in front of a number (no !, typeof, void, -[],
        +1n), no __proto__ keys, and nothing Node refuses to compile in strict code (010, "\\1", "\\u{110000}")."""
        text = self.text(node)
        if node.type == "unary_expression":
            operator, argument = node.child_by_field_name("operator"), node.child_by_field_name("argument")
            if operator is None or argument is None or self.text(operator) not in ("-", "+"):
                return False
            if argument.type == "number":
                return not (self.text(operator) == "+" and self.text(argument).endswith("n")) \
                    and self._literal_node(argument)
            return argument.type == "identifier" and self.text(argument) in ("NaN", "Infinity")
        if node.type == "pair":
            key = node.child_by_field_name("key")
            if key is not None and self.text(key).strip("'\"") == "__proto__":
                return False  # sets the prototype: +{__proto__: null} throws, two of them don't compile
        if node.type == "number" and LEGACY_NUMBER.match(text):
            return False
        if node.type == "escape_sequence":
            if BAD_ESCAPE.match(text):
                return False
            code = re.fullmatch(r"\\u\{([0-9a-fA-F]+)\}", text)
            if code and int(code.group(1), 16) > 0x10FFFF:
                return False
        return super()._literal_node(node)

    # ---------- what the sandbox gets ----------

    def context(self, source, fn):
        """The file with only what fn needs, every line where it was (see the module docstring)."""
        nodes = self.parse(source).root_node.named_children
        statements = self._statements(nodes)
        definitions = {}
        for index, st in enumerate(statements):
            for name in st["defines"]:
                definitions.setdefault(name, []).append(index)
        data = {name for st in statements for name in st["data"]}
        always = {name for st in statements if st["role"] == "keep" for name in st["defines"]}
        functions = {name for st in statements if st["function"] for name in st["defines"]}
        file_names = set(definitions)

        kept = {i for i, st in enumerate(statements) if st["role"] == "keep"}
        followed, needed = set(), set()
        if fn is None:
            todo = sorted(functions)
        else:
            todo = [fn.name]
            for index, st in enumerate(statements):
                if fn.name in st["defines"] and st["node"].start_point[0] + 1 <= fn.line <= st["node"].end_point[0] + 1:
                    kept.add(index)
                    followed.add(index)
                    todo += sorted(st["refs"])
        while True:
            while todo:  # what the function uses, and what that uses
                name = todo.pop()
                if name in needed or name not in definitions:
                    continue
                needed.add(name)
                for index in definitions[name]:
                    if index not in followed and statements[index]["role"] != "blank":
                        kept.add(index)
                        followed.add(index)
                        todo += sorted(statements[index]["refs"])
            for index, st in enumerate(statements):  # statements that fill or change a value it uses
                if index in followed or st["role"] not in ("effect", "flow"):
                    continue
                if st["refs"] & needed & data or (st["role"] == "effect" and st["target"] in needed):
                    kept.add(index)
                    followed.add(index)
                    todo += sorted(st["refs"])
            if not todo:
                break
        for index, st in enumerate(statements):  # global setup that touches nothing else in the file
            used = st["refs"] & file_names
            if st["role"] == "effect" and used <= always and not (used & functions and st["calls"]):
                kept.add(index)
            elif st["role"] == "flow" and not used:
                kept.add(index)

        data_bytes = bytearray(source.encode())
        for index, st in enumerate(statements):
            if index not in kept:
                for i in range(st["node"].start_byte, st["node"].end_byte):
                    if data_bytes[i] not in (10, 13):
                        data_bytes[i] = 32
        text = data_bytes.decode(errors="replace")
        if any(node.type in ("import_statement", "export_statement") for node in nodes):
            text += "\nexport {};"  # still an ES module (strict, top-level await) with its exports blanked
        return text

    def _statements(self, nodes):
        """One record per top-level statement: its role and the names it defines and uses."""
        statements, prologue = [], True
        for node in nodes:
            if node.type == "comment":
                continue
            st = {"node": node, "role": "blank", "defines": set(), "data": set(), "refs": self._refs(node),
                  "function": False, "target": None, "calls": self._has_call(node)}
            inner = node
            if node.type == "export_statement":
                inner = node.child_by_field_name("declaration")
                if inner is None:  # export { a }, export * from, export default <expression>
                    statements.append(st)
                    prologue = False
                    continue
            directive = node.type == "expression_statement" and len(node.named_children) == 1 \
                and node.named_children[0].type == "string"
            if directive and prologue:
                st["role"] = "keep"  # "use strict"
            else:
                prologue = False
                self._classify(inner, st)
            statements.append(st)
        return statements

    def _classify(self, node, st):
        kind = node.type
        if kind in TYPE_ONLY or (kind == "import_statement" and self.text(node).startswith("import type")):
            st["role"] = "keep"  # types only: nothing runs
        elif kind in ("function_declaration", "generator_function_declaration"):
            st.update(role="keep", function=True, defines={self.text(node.child_by_field_name("name"))})
        elif kind in ("class_declaration", "abstract_class_declaration", "enum_declaration", "module"):
            name = node.child_by_field_name("name")
            st.update(role="define", defines={self.text(name)} if name is not None else set())
        elif kind in ("lexical_declaration", "variable_declaration"):
            declarators = [d for d in node.named_children if d.type == "variable_declarator"]
            names = {n for d in declarators for n in self._pattern_names(d.child_by_field_name("name"))}
            values = [d.child_by_field_name("value") for d in declarators]
            modules = self._required(node)
            st["defines"] = names
            if modules:  # const x = require("..."): an import
                st["role"] = "keep" if all(self._builtin(m) for m in modules) else "define"
            elif all(v is None or v.type in FUNCTION_VALUES or self._inert(v) for v in values):
                st["role"] = "keep"
                st["function"] = all(v is not None and v.type in FUNCTION_VALUES for v in values)
                st["data"] = set() if st["function"] else names
            else:
                st.update(role="define", data=names)
        elif kind == "import_statement":
            source = node.child_by_field_name("source")
            module = self.text(source).strip("'\"`") if source is not None else None
            clause = next((c for c in node.named_children if c.type in ("import_clause", "import_require_clause")), None)
            if clause is None:  # import "./setup": runs another file, which the sandbox doesn't have
                st["role"] = "keep" if module and self._builtin(module) else "blank"
            else:
                if clause.type == "import_require_clause":
                    module = next((self.text(c).strip("'\"`") for c in clause.named_children if c.type == "string"), None)
                st["defines"] = {self.text(n) for n in _walk(clause) if n.type == "identifier"}
                st["role"] = "keep" if module and self._builtin(module) else "define"
        elif kind == "expression_statement":
            expr = node.named_children[0] if node.named_children else None
            if expr is not None and expr.type in ("internal_module", "module"):  # namespace X { ... }
                name = expr.child_by_field_name("name")
                st.update(role="define", defines={self.text(name)} if name is not None else set())
            elif expr is None or expr.type == "string" or self._program_call(expr) or \
                    any(not self._builtin(m) for m in self._required(node)):
                st["role"] = "blank"
            else:
                st["role"] = "effect"
                if expr.type in ("assignment_expression", "augmented_assignment_expression"):
                    left = expr.child_by_field_name("left")
                    root = self._root(left)
                    if left is not None and left.type == "identifier":  # X = ...: (re)defines X
                        st["defines"] = st["data"] = {self.text(left)}
                    st["target"] = root
        elif kind in FLOW:
            condition = node.child_by_field_name("condition")
            guard = kind == "if_statement" and condition is not None and ENTRY_GUARD.search(self.text(condition))
            st["role"] = "blank" if guard else "flow"

    def _refs(self, node):
        return {self.text(n) for n in _walk(node) if n.type in ("identifier", "shorthand_property_identifier")}

    def _has_call(self, node):
        return any(n.type in ("call_expression", "new_expression") for n in _walk(node))

    def _pattern_names(self, node):
        if node is None:
            return set()
        return {self.text(n) for n in _walk(node) if n.type in ("identifier", "shorthand_property_identifier_pattern")}

    def _root(self, node):
        while node is not None and node.type in ("member_expression", "subscript_expression", "parenthesized_expression"):
            node = node.child_by_field_name("object") or (node.named_children or [None])[0]
        return self.text(node) if node is not None and node.type == "identifier" else None

    def _required(self, node):
        """Modules a statement loads with require("...") or import("...")."""
        found = []
        for n in _walk(node):
            if n.type == "call_expression":
                callee = n.child_by_field_name("function")
                args = n.child_by_field_name("arguments")
                if callee is not None and self.text(callee) in ("require", "import") and args is not None:
                    strings = [c for c in args.named_children if c.type == "string"]
                    if strings:
                        found.append(self.text(strings[0]).strip("'\"`"))
        return found

    def _builtin(self, module):
        return module.startswith("node:") or module.split("/")[0] in NODE_BUILTINS

    def _inert(self, node):
        if node.type in FUNCTION_VALUES:
            return True
        if node.type == "identifier":
            return self.text(node) in ("undefined", "NaN", "Infinity")
        if node.type == "binary_expression" and self.text(node.child_by_field_name("operator")) in ("in", "instanceof"):
            return False
        return node.type in INERT and all(self._inert(c) for c in node.named_children)

    def _program_call(self, expr):
        """main(), main().catch(...), await run(), console.log(...), app.listen(3000), (async () => {...})()."""
        while expr is not None and expr.type in ("await_expression", "parenthesized_expression", "unary_expression"):
            expr = expr.child_by_field_name("argument") or (expr.named_children or [None])[-1]
        if expr is None or expr.type != "call_expression":
            return False
        node = expr
        while node is not None:
            if node.type == "call_expression":
                callee = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments")
                if args is not None and "process.argv" in self.text(args):
                    return True
                if callee is None:
                    return False
                if callee.type == "identifier":
                    return self.text(callee) != "require"
                if callee.type in FUNCTION_VALUES or (callee.type == "parenthesized_expression" and any(
                        c.type in FUNCTION_VALUES for c in callee.named_children)):
                    return True  # an IIFE running the program
                node = callee
            elif node.type == "member_expression":
                prop = node.child_by_field_name("property")
                if prop is not None and self.text(prop) in PROGRAM_METHODS:
                    return True
                node = node.child_by_field_name("object")
            elif node.type == "identifier":
                return self.text(node) in PROGRAM_ROOTS
            else:
                return False
        return False

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
                if fn is not None:
                    fn.skip = "generator functions are not run yet"
                    found.append(fn)
        return [f for f in found if f is not None]

    def _function(self, node, name_node, outer):
        if name_node is None or name_node.type != "identifier":
            return None
        doc_params, doc_returns = self._jsdoc(outer) if self.name == "javascript" else ({}, None)
        params, skip = [], None
        holder = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
        children = holder.named_children if holder is not None and holder.type == "formal_parameters" \
            else [holder] if holder is not None else []
        for child in children:
            if child.type == "comment":
                continue
            param = self._param(child)
            if param is None:
                skip = f"parameter {self.text(child)!r} is not supported yet"
                continue
            if param.name in doc_params:  # a JSDoc type is a declared type
                optional = param.type is not None and param.type.startswith("default:")
                param = Param(param.name, doc_params[param.name] + (" | undefined" if optional else ""))
            params.append(param)
        returns = self._type_text(node.child_by_field_name("return_type")) or doc_returns
        name = self.text(name_node)
        if name == "main":
            skip = "main is the program's entry point: it isn't run"
        elif returns == "never":
            skip = "declared never to return: it throws on purpose"
        return Function(
            name=name, line=outer.start_point[0] + 1, end_line=outer.end_point[0] + 1,
            params=params, returns=returns, text=self.text(outer), skip=skip,
        )

    def _jsdoc(self, outer):
        """({param: type}, return type) from the /** ... */ comment right above a JavaScript function."""
        node = outer
        while node.parent is not None and node.parent.type == "export_statement":
            node = node.parent
        comment = node.prev_named_sibling
        if comment is None or comment.type != "comment" or comment.end_point[0] < node.start_point[0] - 1:
            return {}, None
        text = self.text(comment)
        if not text.startswith("/**"):
            return {}, None
        braces = r"\{((?:[^{}]|\{[^{}]*\})*)\}"
        params = {}
        for match in re.finditer(rf"@(?:param|arg|argument)\s+{braces}\s*(\[)?\s*([\w$]+)", text):
            kind = jsdoc_type(match.group(1))
            if kind:
                params[match.group(3)] = kind + (" | undefined" if match.group(2) else "")
        returns = re.search(rf"@returns?\s+{braces}", text)
        return params, jsdoc_type(returns.group(1)) if returns else None

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
        nulls = [("null", f"{param_name} may be null"), ("undefined", f"{param_name} may be undefined")]
        if type_text and not type_text.startswith("default:"):
            values = self.typed_values(type_text)
            if self.name == "typescript":
                return values
            if values:  # a JSDoc type: nothing checks JavaScript callers, so null and undefined are still tried
                return values + [(v, a) for v, a in nulls if v not in [value for value, _ in values]]
            type_text = None  # a JSDoc type we have no values for (a typedef): as if untyped
        kind = self.family(param_name, type_text)
        if kind is None:  # nothing to go on: try every kind, each one an assumption
            values = [(v, f"{param_name} may be {v}") for v in ("1", "0", "-1", '""', '"abc"', "[]", "{}")]
        elif kind == "object":  # which fields the code reads is unknown, so any object is an assumption
            values = [(v, f"{param_name} may be {v}") for v in FAMILY_VALUES[kind]]
        elif type_text:  # the default value says what kind it is: as good as a declared type
            values = [(v, None) for v in FAMILY_VALUES[kind]]
        else:  # only the name says so: a guess
            guess = f"{param_name} is {A_KIND[kind]} (guessed from its name)"
            values = [(v, guess) for v in FAMILY_VALUES[kind]]
        return values + nulls

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
        option = option.strip()
        if option.startswith("(") and option.endswith(")") and _balanced(option[1:-1]):
            return [v for v, _ in self.typed_values(option[1:-1])]
        if option.startswith("readonly "):
            option = option[len("readonly "):].strip()
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
            inner_values = self._typed_option(inner.strip()) or []
            return ([f"[{inner_values[0]}, {inner_values[0]}]"] if inner_values else []) + ["[]"] + \
                ([f"[{inner_values[1]}]"] if len(inner_values) > 1 else [])
        if re.fullmatch(r"-?\d+(\.\d+)?", option) or option[:1] in "'\"":
            return [option]  # a literal type
        if option in ("true", "false"):
            return [option]
        if option in ("any", "unknown"):
            return ["1", "0", '""', "[]", "{}", "null"]
        record = _record(option)
        if record is not None:  # Record<string, V>, { [key: string]: V }: every value must be a V
            keys, value_type = record
            values = [v for v, _ in self.typed_values(value_type)]
            if keys is None:
                return ([f'{{"a": {values[0]}}}'] if values else []) + ["{}"]
            return [f"{{{', '.join(f'{k}: {values[0]}' for k in keys)}}}"] if values else []
        if option in ("object", "Object"):
            return ['{"a": 1}', "{}"]
        return []  # interfaces and classes: their shape isn't known here

    def nullable(self, type_text):
        return bool(type_text) and not type_text.startswith("default:") and \
            any(part.strip() in ("null", "undefined", "any", "unknown") for part in split_union(type_text))

    def expected_function(self, fn):
        if self.name == "typescript":
            types = f"value: {fn.returns}): {fn.returns}" if fn.returns else "value: any): any"
            return f"\n\nfunction {EXPECTED}({types} {{ return value; }}\n", EXPECTED
        doc = f"/** @param {{{fn.returns}}} value\n * @returns {{{fn.returns}}} */\n" if fn.returns else ""
        return f"\n\n{doc}function {EXPECTED}(value) {{ return value; }}\n", EXPECTED

    def model_input(self, fn, args, hypothesis):
        """The model's input, unless a declared type (TypeScript or JSDoc) rules it out: passing an array
        where the type says string is a wrong call, not a bug. A value of another kind than a parameter's
        default, or null inside an array or object, is kept with an assumption."""
        result = super().model_input(fn, args, hypothesis)
        if result is None:
            return None
        for param, expr in zip(fn.params, args):
            _, node = self.expression_tree(expr.strip())
            kind = literal_kind(self, node)
            if kind in ("null", "undefined") or not param.type:
                continue
            if param.type.startswith("default:"):
                family = self.family(param.name, param.type)
                if family and kind != family and not (family == "object" and kind == "array"):
                    result.assumptions.append(f"{param.name} may be {expr} (its default is {A_KIND[family]})")
                continue
            notes = set()
            if self._fits(param.type, node, notes, top=True) is False:
                return None
            if "null" in notes:
                result.assumptions.append(f"{param.name} may contain null")
        return result

    def _fits(self, type_text, node, notes, top=False):
        """Does a literal fit a TypeScript type? True, False or None (can't tell: an interface, a generic)."""
        kind = literal_kind(self, node)
        options = split_union(type_text)
        if kind in ("null", "undefined"):
            if not top and not any(o in (kind, "any", "unknown") or (kind == "undefined" and o == "void")
                                   for o in options):
                notes.add("null")
            return True  # at the top, null is left to nullable() (an assumption)
        verdicts = [self._fits_option(o, node, kind, notes) for o in options]
        return True if True in verdicts else None if None in verdicts else False

    def _fits_option(self, option, node, kind, notes):
        option = option.strip()
        if option.startswith("(") and option.endswith(")") and _balanced(option[1:-1]):
            return self._fits(option[1:-1], node, notes)
        if option.startswith("readonly "):
            option = option[len("readonly "):].strip()
        simple = {"number": "number", "Number": "number", "bigint": "bigint", "string": "string",
                  "String": "string", "boolean": "boolean", "Boolean": "boolean"}
        if option in ("any", "unknown"):
            return True
        if option in simple:
            return kind == simple[option]
        if option in ("true", "false"):
            return kind == "boolean" and self.text(_unwrap(node)) == option
        if option in ("null", "undefined", "void", "never"):
            return False
        if re.fullmatch(r"-?\d+(\.\d+)?", option):
            return kind == "number"
        if option[:1] in "'\"`":
            return kind == "string"
        if option.endswith("[]") or option.startswith(("Array<", "ReadonlyArray<")):
            if kind != "array":
                return False
            inner = option[:-2] if option.endswith("[]") else option[option.index("<") + 1:-1]
            return _all(self._fits(inner, item, notes) for item in _unwrap(node).named_children
                        if item.type != "comment")
        if option.startswith("[") and option.endswith("]"):
            return kind == "array"  # a tuple type
        record = _record(option)
        if record is not None:
            if kind != "object":
                return False
            pairs = [p.child_by_field_name("value") for p in _unwrap(node).named_children if p.type == "pair"]
            return _all(self._fits(record[1], value, notes) for value in pairs if value is not None)
        if option in ("object", "Object", "{}"):
            return kind in ("object", "array")
        if option.startswith("{"):
            return True if kind == "object" else None if kind == "array" else False
        if "=>" in option or option.split("<")[0].strip() in NOT_LITERAL_TYPES:
            return False
        return None

    # ---------- running ----------

    def deliberate(self, source, failure):
        """A failure on a line that throws on purpose (`throw new RangeError(...)`), or with an error the
        failing line builds itself (`const err = new ValidationError(...)`, thrown later): the code rejects
        that input as designed. Comments and string contents don't count."""
        line, lines = failure.get("line"), source.splitlines()
        if not line or not 0 < line <= len(lines):
            return False
        code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`', '""', lines[line - 1])
        code = re.sub(r"//.*|/\*.*?\*/", "", code)
        if re.search(self.rejection_pattern, code):
            return True
        error = failure.get("error") or ""
        return bool(re.fullmatch(r"[A-Za-z_$][\w$]*", error)) and \
            re.search(rf"\bnew\s+{re.escape(error)}\s*\(", code) is not None

    def program(self, source, fn, inputs):
        texts = json.dumps([", ".join(i.args) for i in inputs])
        declared = (fn.returns or "").strip()
        promised = re.fullmatch(r"Promise<\s*(\w+)\s*>", declared)
        declared = promised.group(1) if promised else declared
        check = declared if declared in ("number", "string", "boolean") else ""
        # Everything the harness uses comes from globalThis: a user function named process, JSON or
        # String shadows the global in the module, never globalThis's property.
        harness = f"""
;(async () => {{
  const __g = globalThis, __process = __g.process, __JSON = __g.JSON, __String = __g.String;
  const __Number = __g.Number, __Error = __g.Error, __Object = __g.Object, __Function = __g.Function;
  const __fs = __process.getBuiltinModule("node:fs"), __exit = __process.exit;
  const __say = (verdict) => {{ __fs.writeSync(1, "\\n{RESULT_MARK}" + __JSON.stringify(verdict) + "\\n"); }};
  const __show = (v) => {{
    try {{
      if (v === undefined) return "undefined";
      if (typeof v === "number" && !__Number.isFinite(v)) return __String(v);
      if (typeof v === "bigint") return v + "n";
      const s = __JSON.stringify(v);
      if (s !== undefined) return s.length > 300 ? s.slice(0, 297) + "..." : s;
    }} catch (e) {{}}
    try {{ return __String(v); }} catch (e) {{}}
    try {{ return __Object.prototype.toString.call(v); }} catch (e) {{ return "<unprintable>"; }}
  }};
  const __describe = (e) => {{
    const isError = e instanceof __Error || (e !== null && typeof e === "object" && typeof e.stack === "string");
    if (!isError) {{  // only a throw statement throws a non-Error: the code's own choice
      return {{ ok: false, kind: "rejected", error: typeof e, message: "threw " + __show(e) }};
    }}
    let error = "Error", message = "", trace = "", code;
    try {{ error = (e.constructor && e.constructor.name) || e.name || "Error"; }} catch (x) {{}}
    try {{ message = __String(e.message); }} catch (x) {{}}
    try {{ trace = e.stack ? __String(e.stack) : ""; }} catch (x) {{}}
    try {{ code = e.code; }} catch (x) {{}}
    if (code === "MODULE_NOT_FOUND" || code === "ERR_MODULE_NOT_FOUND") {{  // lives in another file
      return {{ ok: false, kind: "not_entered", error, message: "needs a module the sandbox doesn't have: " + message }};
    }}
    return {{ ok: false, kind: "exception", error, message, trace }};
  }};
  let __entered = false, __done = false;
  const __reallyExit = __process.reallyExit;
  if (typeof __reallyExit === "function") {{  // process.exit(), however the code reached it: kind exit
    __process.reallyExit = function (code) {{
      if (__entered && !__done) {{
        __done = true;
        __say({{ ok: false, kind: "exit", error: "Exit",
                message: "the code called process.exit(" + (code === undefined ? "" : __String(code)) + ")" }});
      }}
      return __reallyExit.apply(this, arguments);
    }};
  }}
  const __args = new __Function('"use strict"; return [' + {texts}[__Number(__process.argv[2])] + '];')();
  __fs.writeFileSync(".entered", "");
  __entered = true;
  let __result, __error, __threw = false;
  try {{
    __result = {fn.name}(...__args);
    if (__result && typeof __result.then === "function") __result = await __result;
  }} catch (e) {{
    __threw = true;
    __error = e;
  }}
  let __verdict;
  try {{
    const declared = {check!r};
    if (__threw) {{
      __verdict = __describe(__error);
    }} else if (declared && (__result === undefined || __result === null)) {{
      __verdict = {{ ok: false, kind: "return_type", error: "ReturnTypeError",
                    message: "returned " + __show(__result) + ", but the declared return type is " + declared }};
    }} else {{
      __verdict = {{ ok: true, result: __show(__result) }};
    }}
  }} catch (e) {{
    __verdict = {{ ok: false, kind: "no_verdict", error: "NoVerdict", message: "the result couldn't be reported" }};
  }}
  __done = true;
  __say(__verdict);
  __exit.call(__process, 0);
}})();
"""
        return {"files": {self.file_name: source + "\n" + harness}, "build": None,
                "run": f"node {self.node_flags} {self.file_name} {{i}}"}


class TypeScript(JavaScript):
    name = "typescript"
    title = "TypeScript"
    vscode_ids = ("typescript",)
    extensions = (".ts", ".mts", ".cts")
    grammar = "typescript"
    file_name = "main.ts"
    node_flags = "--no-warnings --experimental-transform-types"  # enums, namespaces, parameter properties


def jsdoc_type(text):
    """A JSDoc type in TypeScript's words: '?number' -> 'number | null', 'Array.<string>' -> 'string[]'."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return None
    suffix = ""
    if text.endswith("="):
        text, suffix = text[:-1].strip(), " | undefined"
    if text in ("*", "?"):
        return "any"
    if text.startswith("?"):
        text, suffix = text[1:].strip(), suffix + " | null"
    elif text.endswith("?"):
        text, suffix = text[:-1].strip(), suffix + " | null"
    text = text.lstrip("!").strip()
    if text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        text = text[1:-1]
    text = re.sub(r"\.<", "<", text)
    text = re.sub(r"\bObject<", "Record<", text)
    text = re.sub(r"\bArray\b(?!<)", "any[]", text)
    text = re.sub(r"\b(String|Number|Boolean)\b", lambda m: m.group(1).lower(), text)
    text = re.sub(r"\bfunction\s*\([^)]*\)(\s*:\s*[\w$.<>\[\]]+)?", "Function", text)
    return " | ".join(split_union(text)) + suffix


def literal_kind(lang, node):
    """number, bigint, string, boolean, null, undefined, array or object (None if it isn't a literal)."""
    node = _unwrap(node)
    if node is None:
        return None
    if node.type == "unary_expression":
        argument = node.child_by_field_name("argument")
        return literal_kind(lang, argument) if argument is not None else None
    if node.type == "number":
        return "bigint" if lang.text(node).endswith("n") else "number"
    if node.type == "identifier":
        text = lang.text(node)
        return "number" if text in ("NaN", "Infinity") else "undefined" if text == "undefined" else None
    return {"string": "string", "template_string": "string", "true": "boolean", "false": "boolean",
            "null": "null", "undefined": "undefined", "array": "array", "object": "object"}.get(node.type)


def _unwrap(node):
    while node is not None and node.type == "parenthesized_expression" and node.named_children:
        node = node.named_children[0]
    return node


def _walk(node):
    """Every node under node, as a list (not a generator: a debugger attached to the backend stops on
    the GeneratorExit that any() raises when it closes a generator early)."""
    nodes, stack = [], [node]
    while stack:
        current = stack.pop()
        nodes.append(current)
        stack.extend(current.children)
    return nodes


def _all(verdicts):
    verdicts = list(verdicts)
    return False if False in verdicts else None if None in verdicts else True


def _depths(text):
    """(character, bracket depth after it) for each character; the > of => doesn't close anything."""
    depth, previous = 0, ""
    for ch in text:
        if ch in "<({[":
            depth += 1
        elif ch in ")}]" or (ch == ">" and previous != "="):
            depth -= 1
        previous = ch
        yield ch, depth


def _balanced(text):
    depths = [d for _, d in _depths(text)]
    return all(d >= 0 for d in depths) and (not depths or depths[-1] == 0)


def _record(option):
    """(keys or None for any string key, value type) for Record<K, V> and { [key: string]: V }, else None."""
    match = re.fullmatch(r"Record<(.+)>", option, re.S)
    if match:
        parts = _split_top(match.group(1), ",")
        if len(parts) != 2:
            return None
        key, value = parts
        if key in ("string", "number", "PropertyKey", "string | number"):
            return None, value
        keys = split_union(key)
        if keys and all(k[:1] in "'\"" for k in keys):
            return [json.dumps(k[1:-1]) for k in keys], value
        return None
    match = re.fullmatch(r"\{\s*\[\s*[\w$]+\s*:\s*(?:string|number)\s*\]\s*:\s*(.+?)\s*;?\s*\}", option, re.S)
    return (None, match.group(1)) if match else None


def _split_top(text, sep):
    parts, current = [], ""
    for ch, depth in _depths(text):
        if ch == sep and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    return [p for p in parts if p]


def split_union(type_text):
    """'number | null' -> ['number', 'null'], ignoring | inside <...>, (...), {...}."""
    parts, current = [], ""
    for ch, depth in _depths(type_text):
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]
