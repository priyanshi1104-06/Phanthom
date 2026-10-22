"""What every language plugin shares: finding functions, building inputs, running them in a sandbox.

A plugin (runner/languages/<name>.py) describes one language:
  - how to find the functions in a file and their parameter types   (tree-sitter, nothing is run)
  - which input expressions count as plain literals                 (so inputs can't fake a failure)
  - boundary inputs for each parameter type                          (0, -1, "", empty arrays, ...)
  - a small harness program that calls one function with one input and reports what happened

Specter runs every candidate input of a function in ONE sandbox: the code is built once and each
input runs in a fresh process. Each failure is then replayed alone in a brand-new sandbox.

The harness prints its verdict as a line starting with @@PHANTOM followed by JSON:
    {"ok": true, "result": "<value as text>"}
    {"ok": false, "kind": "exception", "error": "TypeError", "message": "...", "trace": "..."}
A process that dies without that line (segfault, sanitizer abort, stack overflow, timeout) is
classified from its exit code and stderr by the plugin.

Result kinds that are failures (specter.FAILURES): exception, timeout, crash, return_type. Kinds that
are never findings: build_error (the code doesn't compile), not_entered (it failed before the function
was called: while loading the file, running its init code or building the input), input_error,
no_verdict, exit (the code ended the program on purpose), rejected (it threw on purpose).
A harness with marks_entry = True creates the file `.entered` in its working directory right before it
calls the function, after the arguments are built; base.script reports whether it exists.
"""
import base64
import json
import re
from dataclasses import dataclass, field

RESULT_MARK = "@@PHANTOM "
EXPECTED = "phantom_expected"  # Analyst's identity function (see Language.expected_function)
MAX_INPUTS = 24  # candidate inputs per function and sandbox run
MAX_EXPR = 400  # characters in one argument expression


@dataclass
class Param:
    name: str
    type: str | None  # the annotation as written in the code; None when untyped


@dataclass
class Function:
    name: str  # what the harness calls: "average", or "Stats.average" for a Java static method
    line: int  # 1-based line of the definition
    end_line: int
    params: list[Param]
    returns: str | None
    text: str  # the function's own source
    skip: str | None = None  # why it can't be run (instance method, generics, ...), or None


@dataclass
class Input:
    args: list[str]  # one source expression per parameter, in the language's own syntax
    origin: str = "edge"  # "edge" (from the types) or "model" (suggested by Specter's model)
    assumptions: list[str] = field(default_factory=list)  # e.g. "values may be null"
    hypothesis: dict | None = None  # the model's claim, for model inputs


class Language:
    """Base class: subclasses fill in the class attributes and the harness."""

    name = ""  # registry key: "go"
    title = ""  # "Go"
    vscode_ids = ()  # VS Code languageIds handled by this plugin
    extensions = ()  # file extensions, with the dot
    grammar = ""  # tree-sitter grammar name
    image = ""  # Docker image the code runs in
    memory = "512m"
    tmpfs = "256m"
    env = {}  # extra environment variables inside the sandbox
    local_tool = ""  # program that must be on PATH to run without Docker (e.g. "node")
    build_timeout = 40.0  # seconds for the whole sandbox run (build + all inputs)
    call_timeout = 2  # seconds per input
    null_words = ()  # literals meaning "no value": null, nil, None, nullptr, NULL, undefined
    literal_nodes = frozenset()  # tree-sitter node types allowed inside an input expression
    literal_calls = frozenset()  # callee texts allowed in an input expression: "vec", "List.of"
    literal_names = frozenset()  # names allowed as values: "undefined", "NaN", "Integer.MAX_VALUE"
    marks_entry = False  # the harness creates .entered right before calling the function (see the docstring)
    rejection_pattern = None  # regex for a source line that fails on purpose: raise, throw, assert...

    # ---------- parsing ----------

    def parser(self):
        from tree_sitter_language_pack import get_parser

        return get_parser(self.grammar)

    def parse(self, source):
        return self.parser().parse(source.encode())

    def functions(self, source):
        """Every top-level function Specter could run, in file order."""
        raise NotImplementedError

    @staticmethod
    def text(node):
        return node.text.decode() if node is not None else None

    # ---------- inputs ----------

    def expression_tree(self, expr):
        """Parse expr on its own, as an expression of this language: (tree, expression node)."""
        raise NotImplementedError

    def literal_ok(self, expr):
        """True when expr is made only of literals (numbers, strings, collections, null...)."""
        if not expr or len(expr) > MAX_EXPR or "\n" in expr or "\r" in expr:
            return False
        try:
            tree, node = self.expression_tree(expr)
        except Exception:
            return False
        if node is None or tree.root_node.has_error:
            return False
        if self.text(node) != expr.strip():
            return False  # the literal must be the whole input: '1); system("x"); (1' parses as more than one
        return self._literal_node(node)

    def _literal_node(self, node):
        if node.is_named and self.text(node) in self.literal_names:
            return True  # e.g. undefined, NaN, Integer.MAX_VALUE, None (Rust)
        if node.type in self.call_node_types:
            if not self._allowed_call(node):
                return False
            callee = {c.id for c in self._callee_parts(node)}
            return all(self._literal_node(c) for c in node.children if c.id not in callee)
        if node.is_named and node.type not in self.literal_nodes:
            return False
        return all(self._literal_node(child) for child in node.children)

    call_node_types = frozenset({"call_expression", "method_invocation", "macro_invocation",
                                 "object_creation_expression", "call"})

    def _callee_parts(self, node):
        fields = ("function", "macro", "name", "type", "object", "receiver", "method")
        return [c for c in (node.child_by_field_name(f) for f in fields) if c is not None]

    def _allowed_call(self, node):
        parts = {self.text(c) for c in self._callee_parts(node)}
        obj = node.child_by_field_name("object") or node.child_by_field_name("receiver")
        name = node.child_by_field_name("name") or node.child_by_field_name("method")
        if obj is not None and name is not None:
            parts.add(f"{self.text(obj)}.{self.text(name)}")
        return bool(parts & self.literal_calls)

    def edge_values(self, type_text, param_name):
        """Boundary literals for one parameter: (expression, assumption or None) pairs, typical first."""
        raise NotImplementedError

    def edge_inputs(self, fn):
        """A typical input, then one parameter at a time set to each of its boundary values."""
        options = [self.edge_values(p.type, p.name) for p in fn.params]
        if any(not values for values in options):
            return []
        base = [values[0] for values in options]
        inputs = [Input([value for value, _ in base], assumptions=[a for _, a in base if a])]
        for index, values in enumerate(options):
            for value, assumption in values[1:]:
                args = [b for b, _ in base]
                args[index] = value
                assumptions = [a for i, (_, a) in enumerate(base) if a and i != index]
                if assumption:
                    assumptions.append(assumption)
                inputs.append(Input(args, assumptions=assumptions))
        unique = {tuple(i.args): i for i in inputs}
        return list(unique.values())[:MAX_INPUTS]

    def model_input(self, fn, args, hypothesis):
        """Turn the model's suggested arguments into an Input, or None if they aren't plain literals."""
        if len(args) != len(fn.params) or not all(self.literal_ok(a) for a in args):
            return None
        assumptions = [f"{p.name} may be {a}" for p, a in zip(fn.params, args)
                       if a.strip() in self.null_words and not self.nullable(p.type)]
        if not all(p.type for p in fn.params):
            assumptions.append("the parameters have no types, so this input is assumed to be allowed")
        return Input(list(args), origin="model", assumptions=assumptions, hypothesis=hypothesis)

    def nullable(self, type_text):
        """Does the declared type itself allow a null value? (Optional[...], T | null, *T, ...)"""
        return False

    def call_text(self, fn, args):
        return f"{fn.name}({', '.join(args)})"

    # ---------- Analyst: an expected value, shown the way a result is shown ----------

    def expected_function(self, fn):
        """(source, name): an identity function that takes and returns fn's return type, written to be
        appended to the user's file. Analyst passes an expected value through it, so the harness shows the
        expected value exactly as it shows fn's results and the two can be compared as text. None when
        that isn't possible here (no declared return type in a typed language, void, several results)."""
        return None

    # ---------- running ----------

    literal_hint = "plain literals"  # told to the model: what an input expression may contain

    def context(self, source, fn):
        """The code the sandbox needs for fn (the whole file, unless a plugin can cut it down).
        Scripting languages blank their top-level statements here (see blank_top_level), so a
        `main()` or `print(average([]))` at the bottom of the file can't run with every input."""
        return source

    def blank_top_level(self, source, keep):
        """source with every top-level statement whose node type isn't in `keep` replaced by spaces.
        Newlines stay, so line numbers in errors still match the user's file."""
        data = bytearray(source.encode())
        for node in self.parse(source).root_node.named_children:
            if node.type in keep or node.type == "comment":
                continue
            for i in range(node.start_byte, node.end_byte):
                if data[i] not in (10, 13):
                    data[i] = 32
        return data.decode(errors="replace")

    def sandbox(self):
        """Where this language's code can run right now: "docker", "local" or None (not set up)."""
        from runner.executor import language_sandbox

        return language_sandbox(self)

    def run_inputs(self, source, fn, inputs, sandbox):
        """Run every input against fn in one sandbox (built once, a fresh process per input)."""
        from runner.executor import run_script

        output = run_script(self, self.script(self.context(source, fn), fn, inputs), len(inputs), sandbox)
        return self.parse_output(output, len(inputs))

    def has_errors(self, source):
        """True while the file doesn't parse (half-typed): Specter then keeps the previous results."""
        return self.parse(source).root_node.has_error

    def program(self, source, fn, inputs):
        """The files, build command and per-input run command for the sandbox:
        {"files": {name: text}, "build": "shell command" or None, "run": "shell command with {i}"}
        """
        raise NotImplementedError

    def script(self, source, fn, inputs):
        """One shell script that writes the files, builds once and runs each input in a fresh process."""
        prog = self.program(source, fn, inputs)
        lines = ["set -u", 'W="${PHANTOM_WORK:-/tmp/w}"', 'mkdir -p "$W" && cd "$W" || exit 1']
        for name, content in prog["files"].items():
            data = base64.b64encode(content.encode()).decode()
            lines.append(f"printf '%s' '{data}' | base64 -d > '{name}'")
        if prog.get("build"):
            lines += [
                f"({prog['build']}) > .build_out 2>&1",
                "code=$?",
                "if [ $code -ne 0 ]; then echo \"@@BUILD $code\"; head -c 20000 .build_out | base64 -w0; echo; exit 0; fi",
            ]
        lines.append("echo '@@BUILD 0'")
        run = prog["run"]
        for i in range(len(inputs)):
            lines += [  # elapsed seconds tell a timeout's SIGKILL from the memory limit's; the verdict is printed last
                f"rm -f .entered; s=$(date +%s); timeout -k 1 {self.call_timeout} {run.format(i=i)} > .out 2> .err < /dev/null",
                "code=$?; [ -f .entered ] && en=1 || en=0",
                f"echo \"@@RUN {i} $code $(( $(date +%s) - s )) $en\"",
                "tail -c 65536 .out | base64 -w0; echo",
                "tail -c 65536 .err | base64 -w0; echo",
            ]
        return "\n".join(lines) + "\n"

    def parse_output(self, stdout, count):
        """Turn the script's output into one result dict per input (or a build failure for all)."""
        lines = stdout.splitlines()
        results, index = [None] * count, 0
        while index < len(lines):
            line = lines[index]
            if line.startswith("@@BUILD ") and line != "@@BUILD 0":
                detail = _b64(lines[index + 1] if index + 1 < len(lines) else "")
                failure = {"ok": False, "kind": "build_error", "error": "BuildError", "message": detail[-2000:]}
                return [failure] * count
            if line.startswith("@@RUN "):
                _, i, code, *extra = line.split()
                out = _b64(lines[index + 1] if index + 1 < len(lines) else "")
                err = _b64(lines[index + 2] if index + 2 < len(lines) else "")
                result = self.classify(int(code), out, err)
                if result.get("kind") == "timeout" and code == "137" and extra and int(extra[0]) < self.call_timeout:
                    # SIGKILL before the time limit: the memory limit, not `timeout -k`
                    result = {**result, "kind": "crash", "error": "OutOfMemory",
                              "message": f"killed after {extra[0]} s: the memory limit ({self.memory}) was reached"}
                if self.marks_entry and len(extra) > 1 and extra[1] == "0" and not result.get("ok") \
                        and result.get("kind") in ("exception", "timeout", "crash", "return_type"):
                    result = {**result, "kind": "not_entered",  # loading the file or building the input failed
                              "message": f"before the function was called: {result.get('error')}: {result.get('message')}"}
                results[int(i)] = result
                index += 3
                continue
            index += 1
        missing = {"ok": False, "kind": "crash", "error": "SandboxError", "message": "no result from the sandbox"}
        return [r if r is not None else missing for r in results]

    def classify(self, code, out, err):
        """A result dict from the harness line, or from the exit code and stderr when it died."""
        verdict = None
        for line in reversed(out.splitlines()):
            if line.startswith(RESULT_MARK):
                try:
                    verdict = json.loads(line[len(RESULT_MARK):])
                except ValueError:
                    pass
                break
        if code in (124, 137) and verdict is None:
            return {"ok": False, "kind": "timeout", "error": "Timeout",
                    "message": f"no result after {self.call_timeout} s (possible infinite loop)",
                    "line": self.trace_line(err)}
        if verdict is not None:
            if not verdict.get("ok"):
                verdict.setdefault("kind", "exception")
                trace = verdict.get("trace", "")
                verdict["line"] = verdict.get("line") or self.trace_line(trace)
                verdict["traceback"] = trace
            return verdict
        if code == 0:  # ended normally without a verdict (e.g. the code called exit itself): not a failure
            return {"ok": False, "kind": "no_verdict", "error": "NoVerdict",
                    "message": "the program ended without reporting a result (did the code call exit?)"}
        error, message = self.crash_error(code, err)
        return {"ok": False, "kind": "crash", "error": error, "message": message,
                "line": self.trace_line(err), "traceback": err[-4000:]}

    def crash_error(self, code, err):
        """(error, message) for a process that died without reporting: signal names by default."""
        signals = {134: "SIGABRT", 136: "SIGFPE", 139: "SIGSEGV", 135: "SIGBUS"}
        first = next((ln.strip() for ln in err.splitlines() if ln.strip()), "")
        return signals.get(code, f"exit {code}"), first[:300] or f"the process exited with code {code}"

    def deliberate(self, source, failure):
        """True when the failing line throws on purpose (raise, throw, assert...): the function rejects
        that input as designed, which is not a bug. Plugins with better information override this."""
        line = failure.get("line")
        lines = source.splitlines()
        if not self.rejection_pattern or not line or not 0 < line <= len(lines):
            return False
        return re.search(self.rejection_pattern, lines[line - 1]) is not None

    user_file = "main"  # file name (without extension) the user's code is written to; traces point at it

    def trace_line(self, text):
        """The innermost line of the user's file mentioned in a stack trace or crash report."""
        pattern = re.compile(rf"{re.escape(self.user_file)}\.[A-Za-z]+:(\d+)")
        hits = pattern.findall(text or "")
        return int(hits[0]) if hits else None


def _b64(text):
    try:
        return base64.b64decode(text).decode(errors="replace")
    except ValueError:
        return ""


def json_string(text):
    """A string literal for embedding text in harness source (JSON quoting works for most languages)."""
    return json.dumps(text)
