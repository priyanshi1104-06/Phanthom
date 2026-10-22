"""Ruby: top-level methods, and methods callable on a class or module (def self.f,
module_function, extend self, class << self) as M.f, run by Ruby 3.4.

Inputs are Ruby literals. Ruby has no declared types, so what a parameter takes comes from, in order:
  - a YARD doc comment above the method (# @param values [Array<Integer>]): declared, no assumption;
  - a literal default (sep = ",", limit = 3, opts = {}): declared, and the default itself is the
    typical value; for a string or symbol default (an option) any other value is an assumption;
  - the parameter's name (values, text, count...): a guess, so every value carries it as an assumption
    ("values is an array of numbers (guessed from its name)").
nil is an assumption unless the default is nil or the doc type lists nil. An index parameter is kept
inside the array or string it is used with; an index past the end carries an assumption.

The harness is a second file next to the user's main.rb: it takes the input number off ARGV, loads
main.rb (with DATA when the file has __END__), evaluates input i, creates .entered and sends the
method, catching every Exception (SystemStackError, NoMethodError on nil...). exit, abort and exit!
are the code ending the program on purpose: kind "exit", never a finding. A raise/fail on the failing
line that raised that very exception is the method rejecting its input (deliberate()).
The build step runs the harness in "check" mode: Ruby compiles it (a literal tree-sitter accepts but
Ruby rejects, like 08, fails there) and it loads main.rb once (a missing gem fails there, not per input).
"""
import ast
import re
from dataclasses import dataclass

from runner.languages.base import EXPECTED, RESULT_MARK, Function, Language, Param, json_string

# what a parameter's name suggests (the same idea as the JavaScript plugin, with Ruby's words)
LIST_WORDS = ("values", "items", "list", "arr", "array", "nums", "numbers", "elements", "entries", "rows", "xs")
NUMERIC_LISTS = ("values", "nums", "numbers", "xs")
TEXT_WORDS = ("name", "str", "string", "text", "word", "address", "path", "url", "key", "email", "label", "title",
              "message", "msg", "input", "line", "query", "s", "code", "id", "tag", "slug", "token", "char",
              "letter", "sentence", "prefix", "suffix", "sep", "separator", "delimiter", "pattern", "filename")
NUMBER_WORDS = ("n", "i", "j", "k", "count", "index", "idx", "num", "x", "y", "size", "len", "length", "age",
                "total", "amount", "price", "limit", "max", "min", "start", "end", "cents", "people", "year",
                "rate", "step", "depth", "level", "port", "score", "qty", "quantity", "width", "height")
HASH_WORDS = ("obj", "user", "config", "options", "opts", "data", "item", "record", "person", "params",
              "settings", "payload", "event", "req", "request", "res", "response", "map", "dict", "hash", "attrs",
              "attributes", "headers", "env")
BOOLEAN_WORDS = ("flag", "enabled", "verbose", "strict", "debug", "force", "recursive", "dry")
INDEX_WORDS = ("index", "idx", "i", "j", "pos", "position")

FAMILY_VALUES = {  # typical value first, then boundary values of the same kind
    "array": ["[1, 2, 3]", "[]", "[0]", "[-1]"],  # an array of numbers
    "list": ["[1, 2, 3]", "[]", "[0]", "[-1]"],  # an array whose elements are unknown: numbers are a guess
    "strings": ['["a", "b", "c"]', "[]", '[""]'],
    "floats": ["[1.5, 2.5]", "[]", "[0.0]", "[-1.5]"],
    "symbols": ["[:a, :b]", "[]"],
    "string": ['"abc"', '""', '" "', '"a:b"'],
    "symbol": [":abc"],
    "number": ["3", "0", "-1"],  # Integers are unbounded: no overflow to test
    "index": ["1", "0", "-1"],  # -1 is a valid index in Ruby
    "float": ["1.5", "0.0", "-1.5"],
    "numeric": ["3", "0", "-1", "1.5", "0.0"],
    "boolean": ["true", "false"],
    "hash": ["{}", "{a: 1}"],
}
ARRAYS = ("array", "list", "strings", "floats", "symbols")
KIND_TEXT = {"array": "an array of numbers", "list": "an array of numbers", "strings": "an array of strings",
             "floats": "an array of floats", "symbols": "an array of symbols", "string": "a string",
             "symbol": "a symbol", "number": "an integer", "index": "an integer index", "float": "a float",
             "numeric": "a number", "boolean": "true or false", "hash": "a hash"}
ELEMENTS = {"array": "numbers", "list": "numbers", "strings": "strings", "floats": "floats", "symbols": "symbols"}
YARD_TYPES = {"Integer": "number", "Fixnum": "number", "Bignum": "number", "Float": "float", "Numeric": "numeric",
              "Rational": "numeric", "String": "string", "Symbol": "symbol", "Boolean": "boolean", "Bool": "boolean",
              "TrueClass": "boolean", "FalseClass": "boolean", "true": "boolean", "false": "boolean",
              "Array": "list", "Hash": "hash"}
YARD_ELEMENTS = {"number": "array", "numeric": "array", "float": "floats", "string": "strings", "symbol": "symbols"}
SAMPLE = {"number": "1", "numeric": "1", "float": "1.5", "string": '"a"', "symbol": ":a", "boolean": "true"}
YARD_PARAM = re.compile(r"@param\s+(?:\[([^\]]*)\]\s+(\w+)|(\w+)\s+\[([^\]]*)\])")
NUMBERS = frozenset({"integer", "float", "rational", "complex"})
# libraries that load without RubyGems; anything else needs it (and 60 ms more per run)
NO_GEMS_LIBS = frozenset({"json", "set", "date", "time", "digest", "securerandom", "stringio", "strscan",
                          "English", "pp", "ostruct"})
VISIBILITY = ("private", "public", "protected", "module_function")
# top-level code that runs the program or prints demo output: blanked when main.rb is loaded
RUN_CALLS = frozenset({"main", "run", "puts", "print", "p", "pp", "printf", "putc", "display", "exit", "exit!",
                       "abort", "gets", "readline", "readlines", "sleep", "system", "exec", "spawn", "fork", "loop",
                       "at_exit", "warn", "trap"})
RUN_METHODS = frozenset({"main", "run", "run!", "start", "listen", "serve", "execute", "invoke", "irb", "pry",
                         "puts", "print", "printf", "write", "p", "pp", "display", "exit", "exit!", "abort"})
RUN_NAMES = re.compile(r"(main|run|start|serve|listen|launch|cli)(_|!|$)")  # run_cli, start_server, main!
RUN_WORDS = frozenset({"main", "run", "exit", "exit!", "abort", "gets"})  # a bare word that runs something
STREAMS = frozenset({"$stdout", "STDOUT", "$stderr", "STDERR", "$stdin", "STDIN", "Kernel", "ARGF"})
ENTRY_GUARD = re.compile(r"__FILE__|\$0\b|\$PROGRAM_NAME")  # if __FILE__ == $0 / if $PROGRAM_NAME == __FILE__
CLOSURES = frozenset({"define_method", "define_singleton_method", "lambda", "proc", "new"})  # blocks run later
CONTROL = ("if", "unless", "if_modifier", "unless_modifier", "while", "until", "while_modifier", "until_modifier")
NOTE = "the parameters have no types, so this input is assumed to be allowed"


@dataclass
class Kind:
    """What one parameter takes, and how Phantom knows it."""
    family: str | None  # a FAMILY_VALUES key, or None when nothing says
    how: str | None  # "doc" (YARD @param), "default" (a literal default), "name" (a guess), None
    nullable: bool = False  # the default is nil, or the doc type lists nil
    default: str | None = None  # the default's text, when it is a plain literal
    sample: str | None = None  # a typed hash literal from Hash{K => V}


class Ruby(Language):
    name = "ruby"
    title = "Ruby"
    vscode_ids = ("ruby",)
    extensions = (".rb",)
    grammar = "ruby"
    image = "ruby:3.4-slim"
    local_tool = "ruby"
    memory = "256m"
    tmpfs = "64m"
    build_timeout = 30.0
    call_timeout = 2
    marks_entry = True
    null_words = ("nil",)
    # a raise/fail call on the failing line (deliberate() then checks that it raised that very exception)
    rejection_pattern = r"(?<![\w.:@$])(raise|fail)\b(?![?!:]|\s*=[^=~])"
    literal_hint = ("numbers (also -1, 3r, 2i), strings without #{} interpolation, symbols, arrays, hashes, "
                    "%w[] / %i[], ranges of literals, true/false, nil, Float::INFINITY, Float::NAN")
    literal_nodes = frozenset({
        "integer", "float", "rational", "complex", "string", "string_content", "escape_sequence", "chained_string",
        "character", "simple_symbol", "delimited_symbol", "hash_key_symbol", "string_array", "symbol_array",
        "bare_string", "bare_symbol", "array", "hash", "pair", "nil", "true", "false", "unary", "range",
    })
    literal_names = frozenset({"Float::INFINITY", "Float::NAN", "Float::EPSILON", "Float::MAX", "Float::MIN"})

    # ---------- functions ----------

    def functions(self, source):
        found = []
        self._collect(self.parse(source).root_node, None, False, found, source.splitlines())
        return found

    def _collect(self, holder, scope, singleton, found, lines):
        """Methods among holder's statements: at the top level by name; in a class or module the ones
        callable on it (def self.f, module_function, extend self) as M.f, instance methods as C#m (skipped)."""
        statements = holder.named_children
        shared = singleton or any(n.type == "call" and self._call_name(n) == "extend"
                                  and self.text(n.child_by_field_name("arguments")) == "self" for n in statements)
        for node in statements:
            outer = node
            wrapper = self._call_name(node) if node.type == "call" else None
            if wrapper in VISIBILITY:  # private def f ... end
                args = node.child_by_field_name("arguments")
                inner = args.named_children if args is not None else []
                if len(inner) != 1 or inner[0].type != "method":
                    continue
                node = inner[0]
            if node.type == "identifier" and self.text(node) == "module_function" and scope is not None:
                shared = True  # every method below it is also M.f
            elif node.type == "method":
                if scope is None:
                    self._add(found, node, outer, None, lines)
                elif shared or wrapper == "module_function":
                    self._add(found, node, outer, f"{scope}.", lines)
                else:
                    self._add(found, node, outer, f"{scope}#", lines, "instance methods need an object: not run yet")
            elif node.type == "singleton_method" and scope is not None and \
                    self.text(node.child_by_field_name("object")) == "self":
                self._add(found, node, outer, f"{scope}.", lines)
            elif node.type in ("class", "module"):
                name = self.text(node.child_by_field_name("name"))
                body = node.child_by_field_name("body")
                if body is not None:
                    self._collect(body, f"{scope}::{name}" if scope else name, False, found, lines)
            elif node.type == "singleton_class" and scope is not None and \
                    self.text(node.child_by_field_name("value")) == "self":
                body = node.child_by_field_name("body")
                if body is not None:  # class << self
                    self._collect(body, scope, True, found, lines)

    def _call_name(self, node):
        if node.child_by_field_name("receiver") is not None:
            return None
        return self.text(node.child_by_field_name("method"))

    def _add(self, found, node, outer, prefix, lines, skip=None):
        name = node.child_by_field_name("name")
        if name is None or name.type not in ("identifier", "constant"):
            return  # operators (def +), setters (def x=): not run
        if prefix is None and self.text(name) == "main":
            skip = skip or "the program's entry point is not run"
        docs = _doc_types(lines, outer.start_point[0])
        params = []
        holder = node.child_by_field_name("parameters")
        for child in holder.named_children if holder is not None else []:
            if child.type == "identifier":
                doc = docs.get(self.text(child))
                params.append(Param(self.text(child), f"[{doc}]" if doc else None))
            elif child.type == "optional_parameter":
                param = self.text(child.child_by_field_name("name"))
                value = f"default:{self.text(child.child_by_field_name('value'))}"
                params.append(Param(param, f"[{docs[param]}] {value}" if param in docs else value))
            elif child.type == "keyword_parameter" and child.child_by_field_name("value") is not None:
                continue  # left out of the call: its default is used
            elif child.type not in ("hash_splat_nil", "comment"):
                skip = skip or f"parameter {self.text(child)!r} is not supported yet"
        body = node.child_by_field_name("body")
        if skip is None and body is not None and self._has(body, "yield") and "block_given?" not in self.text(body):
            skip = "it yields to a block: not run yet"
        found.append(Function(
            name=(prefix or "") + self.text(name), line=outer.start_point[0] + 1, end_line=outer.end_point[0] + 1,
            params=params, returns=None, text=self.text(outer), skip=skip,
        ))

    def _has(self, node, node_type):
        return node.type == node_type or any(self._has(child, node_type) for child in node.named_children)

    # ---------- inputs ----------

    def expression_tree(self, expr):
        # the harness builds `-> { [arg, arg] }`, so each argument is checked as an array element
        tree = self.parse(f"[{expr}]")
        top = tree.root_node.named_children
        items = top[0].named_children if len(top) == 1 and top[0].type == "array" else []
        node = items[0] if len(items) == 1 else None
        if node is not None and node.type == "pair":
            return tree, None  # `a: 1` alone would be passed as keyword arguments
        return tree, node

    def literal_ok(self, expr):
        if any(ch < " " and ch != "\t" or ch == "\x7f" for ch in expr or ""):
            return False  # NUL, ^D and ^Z end a Ruby source file early
        return super().literal_ok(expr)

    def _literal_node(self, node):
        if node.type == "unary":  # only a sign in front of a number: no !, not, defined?
            operator, operand = node.child_by_field_name("operator"), node.child_by_field_name("operand")
            return self.text(operator) in ("-", "+") and operand is not None and \
                (operand.type in NUMBERS or self.text(operand) in self.literal_names)
        if node.type == "string_content" and re.search(r"#[{@$]", self.text(node)):
            return False  # interpolation syntax, even inside '...' where Ruby wouldn't run it
        return super()._literal_node(node)

    def kind(self, param_name, type_text):
        """What the parameter takes: from its YARD type, else its literal default, else its name."""
        doc, default = _split_type(type_text)
        nullable = default == "nil"
        literal = default if default and default != "nil" and self.literal_ok(default) else None
        if doc is not None:
            family, sample, doc_nil = self._doc_family(doc)
            nullable = nullable or doc_nil
            if family == "number" and _last(param_name) in INDEX_WORDS:
                family = "index"
            if family is not None:
                return Kind(family, "doc", nullable, literal, sample)
        family = self._literal_family(literal, param_name) if literal else None
        if family is not None:
            return Kind(family, "default", nullable, literal)
        family = self._name_family(param_name)
        return Kind(family, "name" if family else None, nullable)

    def _doc_family(self, doc):
        """(family, typed hash sample, lists nil) for a YARD type list such as `Array<Integer>, nil`."""
        parts = _split_top(doc)
        nullable = any(p in ("nil", "NilClass") for p in parts)
        for part in parts:
            family, sample = self._yard_type(part)
            if family is not None:
                return family, sample, nullable
        return None, None, nullable

    def _yard_type(self, text):
        """(family, typed hash sample) for one YARD type: Integer, Array<String>, Hash{Symbol => Integer}..."""
        head = re.match(r"[\w:]+", text.strip())
        if head is None:
            return None, None  # a duck type (#to_s) or a literal
        family = YARD_TYPES.get(head.group(0).lstrip(":"))
        inner = text.strip()[head.end():].strip()[1:-1]  # what <...>, (...) or {...} holds
        parts = _split_top(inner)
        if family == "list" and parts:  # Array<Integer>, Array(String)
            return YARD_ELEMENTS.get(self._yard_type(parts[0])[0], "list"), None
        if family == "hash" and parts:  # Hash{String => Integer}, Hash<Symbol, Integer>
            pair = re.split(r"\s*=>\s*", inner, maxsplit=1)
            pair = pair if len(pair) == 2 else parts
            if len(pair) == 2:
                key, value = (SAMPLE.get(self._yard_type(t)[0]) for t in pair)
                if key and value:
                    return "hash", f"{{{key} => {value}}}"
        return family, None

    def _literal_family(self, default, param_name):
        """The family a literal default belongs to (for an array, by its elements), or None."""
        _, node = self.expression_tree(default)
        kind = node.type if node is not None else None
        if kind == "unary":  # -1, -1.5, -Float::INFINITY
            kind = node.child_by_field_name("operand").type
        if kind == "integer":
            return "index" if _last(param_name) in INDEX_WORDS else "number"
        if kind == "float" or default.lstrip("+-") in self.literal_names:
            return "float"
        if kind in ("string", "chained_string"):
            return "string"
        if kind in ("simple_symbol", "delimited_symbol"):
            return "symbol"
        if kind in ("true", "false"):
            return "boolean"
        if kind == "hash":
            return "hash"
        if kind == "string_array":
            return "strings"
        if kind == "symbol_array":
            return "symbols"
        if kind == "array":
            types = {self._element(c) for c in node.named_children}
            if len(types) == 1:
                return {"integer": "array", "float": "floats", "string": "strings", "symbol": "symbols"}.get(
                    types.pop(), "list")
            return "list"
        return None

    def _element(self, node):
        kind = node.child_by_field_name("operand").type if node.type == "unary" else node.type
        return {"chained_string": "string", "simple_symbol": "symbol", "delimited_symbol": "symbol"}.get(kind, kind)

    def _name_family(self, param_name):
        last = _last(param_name)
        if param_name.startswith(("is_", "has_", "should_", "can_", "allow_")) or last in BOOLEAN_WORDS:
            return "boolean"
        if last in INDEX_WORDS:
            return "index"
        if last in LIST_WORDS:
            return "array" if last in NUMERIC_LISTS else "list"
        if last.endswith("s") and len(last) > 3 and last[:-1] in TEXT_WORDS:
            return "strings"
        if last.endswith("s") and len(last) > 3 and last[:-1] in NUMBER_WORDS:
            return "array"
        if last in NUMBER_WORDS:
            return "number"
        if last in TEXT_WORDS:
            return "string"
        if last in HASH_WORDS:
            return "hash"
        return None

    def edge_values(self, type_text, param_name):
        k = self.kind(param_name, type_text)
        name = param_name
        if k.family is None:  # nothing to go on: try every kind, each one an assumption
            values = [(v, f"{name} may be {v}") for v in ("1", "0", "-1", '""', '"abc"', "[]", "{}")]
        elif k.how == "name":  # the kind itself is a guess
            guess = f"{name} is {{}} (guessed from its name)"
            values = []
            for v in FAMILY_VALUES[k.family]:
                if k.family == "hash":
                    values.append((v, guess.format("a hash") if v == "{}" else f"{name} may be {v}"))
                elif k.family in ARRAYS and v == "[]":
                    values.append((v, guess.format("an array")))
                else:
                    values.append((v, guess.format(KIND_TEXT[k.family])))
        else:  # declared by a doc comment or a literal default
            option = k.how == "default" and k.family in ("string", "symbol")  # sep = ",", order = :asc
            values = [(k.default, None)] if k.default else []
            if k.family == "symbol" and k.default:
                pass  # a symbol option: other symbols are probably not allowed
            elif k.family == "list":  # an array whose elements nothing declares
                values += [("[]", None)] + self._element_values(name)
            elif k.family == "hash":
                values += [("{}", None)] + ([(k.sample, None)] if k.sample else [("{a: 1}", f"{name} may be {{a: 1}}")])
            else:
                values += [(v, f"{name} may be {v}" if option else None) for v in FAMILY_VALUES[k.family]]
        unique = {}
        for value, assumption in values:
            unique.setdefault(value, assumption)
        return list(unique.items()) + [("nil", None if k.nullable else f"{name} may be nil")]

    def _element_values(self, name):
        """Non-empty arrays for an array parameter whose element type nothing declares."""
        family = self._name_family(name)
        if family in ARRAYS and family != "list":  # tags, scores: the elements are guessed from the name
            return [(v, f"{name} contains {ELEMENTS[family]} (guessed from its name)")
                    for v in FAMILY_VALUES[family] if v != "[]"]
        return [(v, f"{name} may contain numbers") for v in FAMILY_VALUES["list"] if v != "[]"]

    def edge_inputs(self, fn):
        """As usual, but an index outside the array or string it is used with needs an assumption."""
        inputs = super().edge_inputs(fn)
        kinds = [self.kind(p.name, p.type) for p in fn.params]
        for i, k in enumerate(kinds):
            if k.family != "index":
                continue
            partners = self._indexed(fn, i, kinds)
            for inp in inputs:
                if not re.fullmatch(r"-?\d+", inp.args[i]):
                    continue
                for j in partners:
                    size = _size(inp.args[j])
                    if size is not None and not -size <= int(inp.args[i]) < size:
                        inp.assumptions.append(f"{fn.params[i].name} may be out of range for {fn.params[j].name}")
        return inputs

    def _indexed(self, fn, i, kinds):
        """The parameters index i goes with: the ones the body indexes with it, else the first array
        (or, without an array, the first string)."""
        index = re.escape(fn.params[i].name)
        collections = [j for j, k in enumerate(kinds) if j != i and k.family in ARRAYS + ("string",)]
        used = [j for j in collections if re.search(
            rf"\b{re.escape(fn.params[j].name)}\s*(\[\s*{index}\b|\.(fetch|at|slice|delete_at|insert|values_at|dig)"
            rf"\s*\(?\s*{index}\b)", fn.text)]
        if used:
            return used
        arrays = [j for j in collections if kinds[j].family in ARRAYS]
        return arrays[:1] or collections[:1]

    def nullable(self, type_text):
        doc, default = _split_type(type_text)
        return default == "nil" or (doc is not None and self._doc_family(doc)[2])

    def expected_function(self, fn):
        return f"\n\ndef {EXPECTED}(value)\n  value\nend\n", EXPECTED

    def model_input(self, fn, args, hypothesis):
        """The model's input: its nil arguments are assumptions as usual, and so is any value that
        nothing declares (a guessed or unknown kind, a value of another kind, another option)."""
        result = super().model_input(fn, args, hypothesis)
        if result is None:
            return None
        result.assumptions = [a for a in result.assumptions if a != NOTE]
        loose = False
        for param, arg in zip(fn.params, args):
            arg = arg.strip()
            if arg in self.null_words:
                continue  # already an assumption unless the parameter allows nil
            k = self.kind(param.name, param.type)
            if k.how == "default" and k.family in ("string", "symbol"):
                if arg != k.default:
                    result.assumptions.append(f"{param.name} may be {arg}")
            elif not self._fits(k, arg):
                loose = True
        if loose:
            result.assumptions.append(NOTE)
        return result

    def _fits(self, k, expr):
        """True when the literal is a value of the parameter's declared kind."""
        if k.how not in ("doc", "default") or k.family is None:
            return False
        _, node = self.expression_tree(expr)
        if node is None:
            return False
        kind = self._element(node)
        if self.text(node).lstrip("+-") in self.literal_names:
            kind = "float"
        if k.family in ("number", "index"):
            return kind == "integer"
        if k.family in ("float", "numeric"):
            return kind == "float" or (k.family == "numeric" and kind in ("integer", "rational"))
        if k.family in ("string", "symbol", "boolean"):
            return kind == k.family or (k.family == "boolean" and kind in ("true", "false"))
        if k.family == "hash":
            return kind == "hash" and (not node.named_children or k.sample is not None)
        if kind not in ("array", "string_array", "symbol_array"):
            return False
        elements = {self._element(c) for c in node.named_children}
        if kind == "string_array":
            elements = {"string"}
        elif kind == "symbol_array":
            elements = {"symbol"}
        wanted = {"array": "integer", "floats": "float", "strings": "string", "symbols": "symbol"}.get(k.family)
        return not elements or elements == {wanted}

    # ---------- running ----------

    def context(self, source, fn):
        """main.rb as it loads in the sandbox: definitions, assignments, requires, include/using/alias,
        loops that fill tables and other setup stay; top-level code that runs the program or prints demo
        output (main, puts ..., an `if __FILE__ == $0` block) becomes spaces, so line numbers stay the
        user's. A heredoc's body goes with its statement; the text after __END__ stays (DATA)."""
        root = self.parse(source).root_node
        names = {f.name for f in self.functions(source)}
        data = bytearray(source.encode())
        kept = True
        for node in root.named_children:
            if node.type == "comment":
                continue  # kept, and it doesn't separate a statement from its heredoc body
            if node.type != "heredoc_body":
                kept = not self._runs(node, names, top=True)
            if kept:
                continue
            for i in range(node.start_byte, node.end_byte):
                if data[i] not in (10, 13):
                    data[i] = 32
        return data.decode(errors="replace")

    def _runs(self, node, names, top=False):
        """True when this statement runs the program or prints something (see context)."""
        kind = node.type
        if kind in ("method", "singleton_method", "class", "module", "lambda", "comment", "uninterpreted",
                    "alias", "begin_block", "rescue"):
            return False  # a rescue clause only runs when the setup above it fails: begin; require "x"; rescue ...
        if top and kind in ("assignment", "operator_assignment"):
            return False  # LIMIT = 10, $memo = {}, @rates = {...}, TABLE = build_table
        if kind == "identifier":
            return self.text(node) in RUN_WORDS or self.text(node) in names and bool(RUN_NAMES.match(self.text(node)))
        if kind in CONTROL:
            condition = node.child_by_field_name("condition")
            if condition is not None and ENTRY_GUARD.search(self.text(condition)):
                return True  # if __FILE__ == $0 ... end, main if $PROGRAM_NAME == __FILE__
        if kind == "call":
            receiver, method = node.child_by_field_name("receiver"), self.text(node.child_by_field_name("method"))
            if receiver is None and (method in RUN_CALLS or method in names and RUN_NAMES.match(method or "")):
                return True
            if receiver is not None and (method in RUN_METHODS or self.text(receiver) in STREAMS
                                         or f"{self.text(receiver)}.{method}" in names):
                return True
            if method in CLOSURES:
                return False  # define_method(:f) { ... }, Struct.new(...) { ... }: the block runs later
        return any(self._runs(child, names) for child in node.named_children)

    def program(self, source, fn, inputs):
        thunks = ",\n  ".join(f"-> {{ [{', '.join(i.args)}] }}" for i in inputs)
        receiver, _, method = fn.name.rpartition(".")
        libraries = set(re.findall(r"""^\s*require[ (]\s*["']([^"']+)["']""", source, re.MULTILINE))
        gems = not libraries <= NO_GEMS_LIBS or re.search(r"\bGem\b|^\s*gem[ (]", source, re.MULTILINE)
        ruby = "ruby" if gems else "ruby --disable-gems"  # 10 ms instead of 70 per run
        offset = self._data_offset(source)
        data = "" if offset is None else (
            f'DATA = File.open(File.join(__dir__, "main.rb"), "r:UTF-8")  # the text after __END__\n'
            f"DATA.seek({offset})\n")
        harness = f"""# frozen_string_literal: false
trap("TERM", "SYSTEM_DEFAULT")  # a timeout ends the run: it must not become a rescued exception
require "json"
mode = ARGV.shift  # the input number is the harness's argument, not main.rb's
{data}begin
  require_relative "main"
rescue SystemExit => e
  abort("main.rb ended the program while loading (exit #{{e.status}})")
end
real_exit = Process.method(:exit!)
real_exit.(0) if mode == "check"
inputs = [
  {thunks}
]
out = STDOUT.dup
STDOUT.reopen(File::NULL, "w")  # whatever the method prints can't crowd out the verdict
text = ->(s) {{ s.to_s.dup.force_encoding("UTF-8").scrub("?") }}
report = ->(verdict) {{ out.write("\\n{RESULT_MARK}" + JSON.generate(verdict) + "\\n"); out.flush }}
begin
  args = inputs[Integer(mode)].call
rescue Exception => e  # not the method's fault (a range of mismatched literals): never a finding
  verdict = {{ ok: false, kind: "input_error", error: e.class.to_s, message: text.(e.message)[0, 500] }}
end
unless verdict
  ended = ->(status) {{
    report.({{ ok: false, kind: "exit", error: "SystemExit", message: "exit!(#{{status.inspect}}) was called" }})
    real_exit.(status)
  }}
  Kernel.send(:define_method, :exit!) {{ |status = false| ended.(status) }}
  Kernel.send(:private, :exit!)
  [Kernel, Process].each {{ |m| m.define_singleton_method(:exit!) {{ |status = false| ended.(status) }} }}
  File.write(File.join(__dir__, ".entered"), "")
  begin
    result = {receiver or "self"}.__send__(:{json_string(method)}, *args)
  rescue SystemExit => e  # exit or abort: the code ended the program on purpose
    verdict = {{ ok: false, kind: "exit", error: "SystemExit",
                message: "exit(#{{e.status}}) was called" + (e.message == "exit" ? "" : ": " + text.(e.message)[0, 300]) }}
  rescue Exception => e
    verdict = {{ ok: false, kind: "exception", error: e.class.name || e.class.to_s,
                message: text.(begin; e.message; rescue Exception; ""; end)[0, 500],
                trace: text.(Array(e.backtrace).first(40).join("\\n")) }}
  else
    shown = begin
      text.(result.inspect)
    rescue Exception  # a BasicObject, or an inspect that fails: showing the result is not the method's job
      begin; "#<#{{::Kernel.instance_method(:class).bind_call(result)}}>"; rescue Exception; "#<object>"; end
    end
    verdict = {{ ok: true, result: shown.size > 300 ? shown[0, 297] + "..." : shown }}
  end
end
report.(verdict)
real_exit.(0)
"""
        return {"files": {"main.rb": source, "harness.rb": harness},
                "build": f"timeout -k 1 10 {ruby} harness.rb check",
                "run": f"{ruby} harness.rb {{i}}"}

    def _data_offset(self, source):
        """Byte offset of the text after a real `__END__` line (what DATA reads), or None."""
        node = next((n for n in self.parse(source).root_node.named_children if n.type == "uninterpreted"), None)
        if node is None:
            return None
        data = source.encode()
        end = data.find(b"\n", node.start_byte)
        return len(data) if end < 0 else end + 1

    def crash_error(self, code, err):
        bug = next((line for line in err.splitlines() if "[BUG]" in line), None)
        if bug:
            return ("SIGSEGV" if "Segmentation fault" in bug else "RubyBug"), bug.strip()[:300]
        uncaught = re.search(r":in .*?: (.*) \(([A-Z][\w:]*)\)$", err, re.MULTILINE)
        if uncaught:
            return uncaught.group(2), uncaught.group(1)[:300]
        return super().crash_error(code, err)

    def deliberate(self, source, failure):
        """The failing line has a raise/fail, the exception was raised by user code there (not by a core
        method such as Integer() or Array#fetch called on that line), and it is of the class that raise
        names. raise NotImplementedError is a method not written yet: still reported."""
        if failure.get("error") == "NotImplementedError" or not super().deliberate(source, failure):
            return False
        line = failure["line"]
        top = next(iter((failure.get("traceback") or "").splitlines()), "")
        frame = re.match(r"(.*?):(\d+):in [`'](.*)'$", top)
        if frame is None or not re.search(rf"{self.user_file}\.rb$", frame.group(1)) or int(frame.group(2)) != line:
            return False
        label = frame.group(3)
        if not label.startswith(("block ", "rescue in ", "ensure in ", "<")):
            defined = {self.text(n) for n in _nodes(self.parse(source).root_node, ("method", "singleton_method"),
                                                    field="name")}
            if re.split(r"[#.]", label)[-1] not in defined:
                return False  # raised inside a core method called on that line
        error = (failure.get("error") or "").split("::")[-1]
        for node in self._raises(self.parse(source).root_node, line - 1):
            args = node.child_by_field_name("arguments") if node.type == "call" else None
            first = args.named_children[0] if args is not None and args.named_children else None
            if first is None:
                return True  # bare raise: re-raises the exception being handled
            if first.type == "call" and self.text(first.child_by_field_name("method")) in ("new", "exception"):
                first = first.child_by_field_name("receiver") or first
            if first.type in ("string", "chained_string"):
                if error == "RuntimeError":
                    return True
            elif first.type in ("constant", "scope_resolution"):
                if self.text(first).split("::")[-1] == error:
                    return True
            else:
                return True  # raise error_object: its class is not visible here
        return False

    def _raises(self, node, row):
        """raise/fail calls (without a receiver) whose span covers 0-based row."""
        if node.start_point[0] > row or node.end_point[0] < row:
            return
        if node.type == "call" and self._call_name(node) in ("raise", "fail"):
            yield node
        elif node.type == "identifier" and self.text(node) in ("raise", "fail") and (
                node.parent is None or node.parent.type != "call" or node.parent.child_by_field_name("method") != node):
            yield node
        for child in node.named_children:
            yield from self._raises(child, row)


def _nodes(node, types, field):
    """The `field` child of every node of the given types under node."""
    if node.type in types and node.child_by_field_name(field) is not None:
        yield node.child_by_field_name(field)
    for child in node.named_children:
        yield from _nodes(child, types, field)


def _last(param_name):
    return param_name.split("_")[-1].lower()


def _split_type(type_text):
    """("Array<Integer>" or None, "default text" or None) from a Param.type: "[T]", "[T] default:v", "default:v"."""
    if not type_text:
        return None, None
    if type_text.startswith("default:"):
        return None, type_text[len("default:"):].strip()
    doc, found, default = type_text.partition("] default:")
    if found:
        return doc[1:].strip(), default.strip()
    return (type_text[1:-1].strip(), None) if type_text.startswith("[") and type_text.endswith("]") else (None, None)


def _split_top(text):
    """Split a YARD type list at the commas outside <>, {}, () and []."""
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch in "<{([":
            depth += 1
        elif ch in ">})]" and not (ch == ">" and current.endswith("=")):  # the > of => closes nothing
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    return [p for p in parts + [current.strip()] if p]


def _doc_types(lines, row):
    """{parameter: YARD type} from the comment lines right above 0-based row."""
    types = {}
    above = row - 1
    while 0 <= above < len(lines) and lines[above].lstrip().startswith("#"):
        match = YARD_PARAM.search(lines[above])
        if match:
            name, typ = (match.group(2), match.group(1)) if match.group(2) else (match.group(3), match.group(4))
            types.setdefault(name, typ.strip())
        above -= 1
    return types


def _size(expr):
    """Length of an array or string edge value ("[1, 2, 3]" -> 3), None for anything else."""
    try:
        value = ast.literal_eval(expr)
    except (ValueError, SyntaxError):
        return None
    return len(value) if isinstance(value, (list, str)) else None
