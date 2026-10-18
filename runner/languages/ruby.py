"""Ruby: top-level methods, and methods callable on a class or module (def self.f,
module_function, extend self, class << self) as M.f, run by Ruby 3.4.

Inputs are Ruby literals. Ruby has no declared types, so it works like plain JavaScript: the
typical value comes from the parameter's name or default, and a finding that needs another kind
of value (or nil, or a hash whose keys are a guess) carries that as an assumption. An index
parameter is kept inside the array or string it is paired with; an index past the end carries
an assumption.

The harness is a second file next to the user's main.rb: it loads main.rb, evaluates input i
and sends the method, catching every Exception (SystemStackError, NoMethodError on nil...).
The build step compiles the harness (a literal tree-sitter accepts but Ruby rejects, like 08,
fails there) and loads main.rb once (a missing gem fails there, not as a finding per input).
"""
import ast
import re

from runner.languages.base import RESULT_MARK, Function, Language, Param, json_string
from runner.languages.javascript import TEXT_WORDS, JavaScript

BOOLEAN_WORDS = ("flag", "enabled", "verbose", "strict", "debug", "force", "recursive", "dry")
INDEX_WORDS = ("index", "idx", "i", "j", "pos", "position")

FAMILY_VALUES = {  # typical value first, then boundary values of the same kind
    "array": ["[1, 2, 3]", "[]", "[0]", "[-1]"],
    "strings": ['["a", "b", "c"]', "[]", '[""]'],
    "string": ['"abc"', '""', '" "', '"a:b"'],
    "number": ["3", "0", "-1"],  # Integers are unbounded: no overflow to test
    "index": ["1", "0", "-1"],  # -1 is a valid index in Ruby
    "float": ["1.5", "0.0", "-1.5"],
    "boolean": ["true", "false"],
    "hash": ["{a: 1}", "{}"],
}
NUMBERS = frozenset({"integer", "float", "rational", "complex"})
# libraries that load without RubyGems; anything else needs it (and 60 ms more per run)
NO_GEMS_LIBS = frozenset({"json", "set", "date", "time", "digest", "securerandom", "stringio", "strscan",
                          "English", "pp", "ostruct"})
VISIBILITY = ("private", "public", "protected", "module_function")


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
    null_words = ("nil",)
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
        self._collect(self.parse(source).root_node, None, False, found)
        return found

    def _collect(self, holder, scope, singleton, found):
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
                    self._add(found, node, outer, None)
                elif shared or wrapper == "module_function":
                    self._add(found, node, outer, f"{scope}.")
                else:
                    self._add(found, node, outer, f"{scope}#", "instance methods need an object: not run yet")
            elif node.type == "singleton_method" and scope is not None and \
                    self.text(node.child_by_field_name("object")) == "self":
                self._add(found, node, outer, f"{scope}.")
            elif node.type in ("class", "module"):
                name = self.text(node.child_by_field_name("name"))
                body = node.child_by_field_name("body")
                if body is not None:
                    self._collect(body, f"{scope}::{name}" if scope else name, False, found)
            elif node.type == "singleton_class" and scope is not None and \
                    self.text(node.child_by_field_name("value")) == "self":
                body = node.child_by_field_name("body")
                if body is not None:  # class << self
                    self._collect(body, scope, True, found)

    def _call_name(self, node):
        if node.child_by_field_name("receiver") is not None:
            return None
        return self.text(node.child_by_field_name("method"))

    def _add(self, found, node, outer, prefix, skip=None):
        name = node.child_by_field_name("name")
        if name is None or name.type not in ("identifier", "constant"):
            return  # operators (def +), setters (def x=): not run
        params = []
        holder = node.child_by_field_name("parameters")
        for child in holder.named_children if holder is not None else []:
            if child.type == "identifier":
                params.append(Param(self.text(child), None))
            elif child.type == "optional_parameter":
                value = self.text(child.child_by_field_name("value"))
                params.append(Param(self.text(child.child_by_field_name("name")), f"default:{value}"))
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

    def family(self, param_name, type_text):
        default = type_text[len("default:"):].strip() if type_text and type_text.startswith("default:") else ""
        last = param_name.split("_")[-1].lower()
        if default.startswith(("[", "%w", "%i")):
            return "array"
        if default.startswith(('"', "'", "%q", "%Q", "%(")):
            return "string"
        if default in ("true", "false"):
            return "boolean"
        if default.startswith("{"):
            return "hash"
        if re.fullmatch(r"-?\d[\d_]*", default):
            return "index" if last in INDEX_WORDS else "number"
        if re.fullmatch(r"-?\d[\d_]*\.\d+(e-?\d+)?", default):
            return "float"
        # no default, nil, or an expression: go by the name, with JavaScript's word lists
        if param_name.startswith(("is_", "has_", "should_", "can_", "allow_")) or last in BOOLEAN_WORDS:
            return "boolean"
        if last in INDEX_WORDS:
            return "index"
        kind = JavaScript.family(self, param_name, None)
        if kind == "array" and last.endswith("s") and last[:-1] in TEXT_WORDS:
            return "strings"
        return "hash" if kind == "object" else kind

    def edge_values(self, type_text, param_name):
        default = type_text[len("default:"):].strip() if type_text else ""
        kind = self.family(param_name, type_text)
        if default.startswith(":") and self.literal_ok(default):
            values = [(default, None)]  # a symbol option: other symbols are probably not allowed
        elif kind is None:  # nothing to go on: try every kind, each one an assumption
            values = [(v, f"{param_name} may be {v}") for v in ("1", "0", "-1", '""', '"abc"', "[]", "{}")]
        elif kind == "hash":  # which keys the code reads is unknown, so any hash is an assumption
            values = [(v, f"{param_name} may be {v}") for v in FAMILY_VALUES[kind]]
        else:
            values = [(v, None) for v in FAMILY_VALUES[kind]]
        return values + [("nil", None if self.nullable(type_text) else f"{param_name} may be nil")]

    def edge_inputs(self, fn):
        """As usual, but an index outside the array or string it goes with needs an assumption."""
        inputs = super().edge_inputs(fn)
        indexes = [i for i, p in enumerate(fn.params) if self.family(p.name, p.type) == "index"]
        for inp in inputs:
            for i in indexes:
                if not re.fullmatch(r"-?\d+", inp.args[i]):
                    continue
                for j, value in enumerate(inp.args):
                    size = _size(value)
                    if j != i and size is not None and not -size <= int(inp.args[i]) < size:
                        inp.assumptions.append(f"{fn.params[i].name} may be out of range for {fn.params[j].name}")
        return inputs

    def nullable(self, type_text):
        return type_text == "default:nil"

    def model_input(self, fn, args, hypothesis):
        result = super().model_input(fn, args, hypothesis)
        note = "the parameters have no types, so this input is assumed to be allowed"
        if result is not None and fn.params and note not in result.assumptions:
            result.assumptions.append(note)
        return result

    # ---------- running ----------

    def context(self, source, fn):
        """Only definitions run when main.rb loads: methods, classes, modules, constants, requires.
        Everything else at the top level becomes spaces, so line numbers stay the user's."""
        data = bytearray(source.encode())
        for node in self.parse(source).root_node.named_children:
            if self._kept(node):
                continue
            for i in range(node.start_byte, node.end_byte):
                if data[i] not in (10, 13):
                    data[i] = 32
        return data.decode(errors="replace")

    def _kept(self, node):
        if node.type in ("method", "singleton_method", "class", "module", "comment"):
            return True
        if node.type in ("assignment", "operator_assignment"):  # LIMIT = 10, Config::MAX ||= 3
            return node.child_by_field_name("left").type in ("constant", "scope_resolution")
        if node.type == "call":
            name = self._call_name(node)
            args = node.child_by_field_name("arguments")
            return name in ("require", "require_relative") or (
                name in VISIBILITY and args is not None and [c.type for c in args.named_children] == ["method"])
        return False

    def program(self, source, fn, inputs):
        thunks = ",\n  ".join(f"-> {{ [{', '.join(i.args)}] }}" for i in inputs)
        receiver, _, method = fn.name.rpartition(".")
        libraries = set(re.findall(r"""^\s*require[ (]\s*["']([^"']+)["']""", source, re.MULTILINE))
        ruby = "ruby --disable-gems" if libraries <= NO_GEMS_LIBS else "ruby"  # 10 ms instead of 70 per run
        harness = f"""# frozen_string_literal: false
trap("TERM", "SYSTEM_DEFAULT")  # a timeout ends the run: it must not become a rescued exception
require "json"
require_relative "main"
inputs = [
  {thunks}
]
out = STDOUT.dup
STDOUT.reopen(File::NULL, "w")  # whatever the method prints can't crowd out the verdict
text = ->(s) {{ s.to_s.dup.force_encoding("UTF-8").scrub("?") }}
begin
  args = inputs[ARGV[0].to_i].call
rescue Exception => e  # not the method's fault (a range of mismatched literals): never a finding
  verdict = {{ ok: false, kind: "input_error", error: e.class.to_s, message: text.(e.message)[0, 500] }}
end
unless verdict
  begin
    result = {receiver or "self"}.__send__(:{json_string(method)}, *args)
    shown = text.(begin; result.inspect; rescue Exception; "#<#{{result.class}}>"; end)
    verdict = {{ ok: true, result: shown.size > 300 ? shown[0, 297] + "..." : shown }}
  rescue Exception => e
    verdict = {{ ok: false, kind: "exception", error: e.class.name || e.class.to_s,
                message: text.(begin; e.message; rescue Exception; ""; end)[0, 500],
                trace: text.(Array(e.backtrace).first(40).join("\\n")) }}
  end
end
out.write("\\n{RESULT_MARK}" + JSON.generate(verdict) + "\\n")
out.flush
exit!(0)
"""
        check = "RubyVM::InstructionSequence.compile_file(%q(harness.rb)); require %q(./main)"
        return {"files": {"main.rb": source, "harness.rb": harness},
                "build": f"timeout -k 1 10 {ruby} -e '{check}'",
                "run": f"{ruby} harness.rb {{i}}"}

    def crash_error(self, code, err):
        bug = next((line for line in err.splitlines() if "[BUG]" in line), None)
        if bug:
            return ("SIGSEGV" if "Segmentation fault" in bug else "RubyBug"), bug.strip()[:300]
        uncaught = re.search(r":in .*?: (.*) \(([A-Z][\w:]*)\)$", err, re.MULTILINE)
        if uncaught:
            return uncaught.group(2), uncaught.group(1)[:300]
        return super().crash_error(code, err)


def _size(expr):
    """Length of an array or string edge value ("[1, 2, 3]" -> 3), None for anything else."""
    try:
        value = ast.literal_eval(expr)
    except (ValueError, SyntaxError):
        return None
    return len(value) if isinstance(value, (list, str)) else None
