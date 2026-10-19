"""Python: the Phase 1 engine. Functions come from Python's own ast, inputs are Python literals turned
into the worker's tagged JSON, and every typed function is also fuzzed with Hypothesis (in specter.py).
"""
import ast

from runner.context import function_context
from runner.languages.base import Function, Language, Param

ALLOWED = (ast.Expression, ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Load, ast.USub, ast.UAdd)

TYPED = {
    "int": ["1", "0", "-1"],
    "float": ["1.5", "0.0", "-1.0"],
    "str": ["'abc'", "''", "' '", "'a:b'"],
    "bool": ["True", "False"],
    "bytes": ["b'abc'", "b''"],
    "None": ["None"],
}
CONTAINERS = ("list", "List", "Sequence", "Iterable", "Collection", "tuple", "Tuple", "set", "Set",
              "frozenset", "dict", "Dict", "Mapping")


class Python(Language):
    name = "python"
    title = "Python"
    vscode_ids = ("python",)
    extensions = (".py",)
    grammar = "python"
    image = "phantom-sandbox"
    local_tool = "python3"
    null_words = ("None",)
    rejection_pattern = r"^\s*(raise|assert)\b"  # the function refuses the input on purpose
    literal_hint = "numbers, strings, bytes, lists, tuples, dicts, sets, True/False, None"

    def functions(self, source):
        tree = ast.parse(source)
        found = []
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                continue
            args = node.args
            params = [Param(a.arg, ast.unparse(a.annotation) if a.annotation else None)
                      for a in args.posonlyargs + args.args]
            skip = None
            if isinstance(node, ast.AsyncFunctionDef):
                skip = "async functions are not run yet"
            elif args.kwonlyargs and any(d is None for d in args.kw_defaults):
                skip = "keyword-only parameters without defaults are not supported yet"
            found.append(Function(
                name=node.name, line=node.lineno, end_line=node.end_lineno, params=params,
                returns=ast.unparse(node.returns) if node.returns else None,
                text=ast.get_source_segment(source, node) or "", skip=skip,
            ))
        return found

    def context(self, source, fn):
        return function_context(source, fn.name)

    def has_errors(self, source):
        try:
            ast.parse(source)
        except SyntaxError:
            return True
        return False

    def literal_ok(self, expr):
        if not expr or len(expr) > 400:
            return False
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return False
        if isinstance(tree.body, ast.Tuple) and not expr.strip().startswith("("):
            return False  # "1, 2" would be two arguments, not one tuple
        for node in ast.walk(tree):
            if isinstance(node, ast.UnaryOp):
                if not isinstance(node.op, (ast.USub, ast.UAdd)) or not isinstance(node.operand, ast.Constant):
                    return False
            elif not isinstance(node, ALLOWED):
                return False
        return True

    def edge_values(self, type_text, param_name):
        if type_text is None:  # untyped: every kind of value is an assumption
            return [(v, f"{param_name} may be {v}") for v in ("1", "0", "-1", "''", "'abc'", "[]", "{}", "None")]
        values = self._typed(type_text.strip())
        return [(v, None) for v in dict.fromkeys(values)]

    def _typed(self, text):
        if text in TYPED:
            return TYPED[text]
        if text.startswith("Optional[") and text.endswith("]"):
            return self._typed(text[9:-1]) + ["None"]
        parts = split_top(text, "|")
        if len(parts) > 1:
            return [v for part in parts for v in self._typed(part)]
        head, _, rest = text.partition("[")
        inner = split_top(rest[:-1], ",") if rest.endswith("]") else []
        if head in ("Union",):
            return [v for part in inner for v in self._typed(part)]
        if head in ("list", "List", "Sequence", "Iterable", "Collection") and inner:
            item = self._typed(inner[0])
            return ([f"[{item[0]}, {item[0]}]", "[]"] + ([f"[{item[1]}]"] if len(item) > 1 else [])) if item else []
        if head in ("tuple", "Tuple") and inner:
            if len(inner) == 2 and inner[1] == "...":
                item = self._typed(inner[0])
                return [f"({item[0]},)", "()"] if item else []
            items = [self._typed(i) for i in inner]
            return [f"({', '.join(i[0] for i in items)},)"] if all(items) else []
        if head in ("set", "Set", "frozenset") and inner:
            item = self._typed(inner[0])
            return [f"{{{item[0]}}}"] if item else []
        if head in ("dict", "Dict", "Mapping") and len(inner) == 2:
            keys, vals = self._typed(inner[0]), self._typed(inner[1])
            return [f"{{{keys[0]}: {vals[0]}}}", "{}"] if keys and vals else []
        if head == "Literal":
            return inner
        return []  # classes and anything unknown: left to Hypothesis

    def nullable(self, type_text):
        return bool(type_text) and ("None" in split_top(type_text, "|") or type_text.startswith("Optional["))

    def call_text(self, fn, args):
        return f"{fn.name}({', '.join(args)})"

    def run_inputs(self, source, fn, inputs, sandbox):
        import worker  # its JSON helpers only: no analysed code runs in this process
        from runner.executor import run_job

        calls, bad = [], {}
        for n, inp in enumerate(inputs):
            try:
                calls.append(worker.encode(list(ast.literal_eval(f"({', '.join(inp.args)},)"))) if inp.args else [])
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
                bad[n] = {"ok": False, "kind": "input_error", "error": type(exc).__name__, "message": str(exc)[:200]}
                calls.append(None)
        good = [c for c in calls if c is not None]
        result = run_job({"mode": "calls", "source": self.context(source, fn), "func_name": fn.name,
                          "calls": good, "call_timeout": 1.0},
                         timeout=10 + 2 * len(good), sandbox=sandbox) if good else {"results": []}
        if "results" not in result:
            failure = {"ok": False, **{k: result.get(k) for k in ("kind", "error", "message")}}
            return [bad.get(n, failure) for n in range(len(inputs))]
        ran = iter(result["results"])
        results = [bad[n] if n in bad else next(ran) for n in range(len(inputs))]
        return [{**r, "kind": "exit"} if r.get("error") == "SystemExit" else r for r in results]


def split_top(text, sep):
    """Split on sep outside brackets: 'dict[str, int] | None' -> ['dict[str, int]', 'None']."""
    parts, depth, current = [], 0, ""
    for ch in text:
        depth += ch in "[(" and 1 or ch in "])" and -1 or 0
        if ch == sep and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    return [p for p in parts if p]
