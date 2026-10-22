"""Python: the Phase 1 engine. Functions come from Python's own ast, inputs are Python literals turned
into the worker's tagged JSON, and every typed function is also fuzzed with Hypothesis (in specter.py).

The worker loads the file once and runs each input in a forked child (see worker.py), so a failure while
loading (a missing import, top-level code) is kind not_entered, never a finding. A raise/assert line is
the function rejecting its input on purpose, and sys.exit is kind exit: neither is a finding. An untyped
parameter with a literal default (sep=" ") takes values of the default's type with no assumption.
"""
import ast

from runner.context import function_context
from runner.languages.base import EXPECTED, Function, Language, Param

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
NEVER_RETURNS = ("NoReturn", "Never", "typing.NoReturn", "typing.Never", "typing_extensions.NoReturn",
                 "typing_extensions.Never")
DEFAULT = "default:"  # Param.type of an untyped parameter with a literal default: "default:' '"
# What a literal can be, for checking a model's input against the annotation (see fits()).
KINDS = {
    "int": (int,), "float": (int, float), "complex": (int, float, complex), "str": (str,), "bytes": (bytes,),
    "bool": (bool,), "list": (list,), "List": (list,), "tuple": (tuple,), "Tuple": (tuple,),
    "set": (set, frozenset), "Set": (set, frozenset), "frozenset": (set, frozenset), "FrozenSet": (set, frozenset),
    "AbstractSet": (set, frozenset), "MutableSet": (set,), "dict": (dict,), "Dict": (dict,), "Mapping": (dict,),
    "MutableMapping": (dict,), "Sequence": (list, tuple, str, bytes), "MutableSequence": (list,),
    "Iterable": (list, tuple, set, frozenset, dict, str, bytes), "Collection": (list, tuple, set, frozenset, dict, str, bytes),
    "Container": (list, tuple, set, frozenset, dict, str, bytes), "Reversible": (list, tuple, str, bytes),
    "Callable": (), "bytearray": (), "Iterator": (), "Generator": (),
}
ITEMS = {"list", "List", "set", "Set", "frozenset", "FrozenSet", "AbstractSet", "MutableSet", "Sequence",
         "MutableSequence", "Iterable", "Collection", "Container", "Reversible"}


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
            positional = args.posonlyargs + args.args
            defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            params = [Param(a.arg, ast.unparse(a.annotation) if a.annotation else literal_default(d))
                      for a, d in zip(positional, defaults)]
            returns = ast.unparse(node.returns) if node.returns else None
            skip = None
            if node.name == "main":
                skip = "main is the program's entry point: it isn't run"
            elif returns in NEVER_RETURNS:
                skip = f"declared -> {returns}: it never returns normally, so failing is what it is for"
            elif isinstance(node, ast.AsyncFunctionDef):
                skip = "async functions are not run yet"
            elif args.kwonlyargs and any(d is None for d in args.kw_defaults):
                skip = "keyword-only parameters without defaults are not supported yet"
            found.append(Function(
                name=node.name, line=node.lineno, end_line=node.end_lineno, params=params,
                returns=returns, text=ast.get_source_segment(source, node) or "", skip=skip,
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
        if not expr or len(expr) > 400 or "\n" in expr or "\r" in expr:
            return False
        try:
            tree = ast.parse(expr, mode="eval")
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            return False
        if isinstance(tree.body, ast.Tuple) and not expr.strip().startswith("("):
            return False  # "1, 2" would be two arguments, not one tuple
        if has_comment(expr):
            return False  # "1 #": a comment is not part of a literal
        for node in ast.walk(tree):
            if isinstance(node, ast.UnaryOp):
                if not isinstance(node.op, (ast.USub, ast.UAdd)) or not isinstance(node.operand, ast.Constant) \
                        or type(node.operand.value) not in (int, float, complex):
                    return False  # only a sign in front of a number
            elif not isinstance(node, ALLOWED) or isinstance(node, ast.Constant) and node.value is Ellipsis:
                return False
        return argument(expr) is not UNUSABLE  # it must also be a value the worker can receive

    def edge_values(self, type_text, param_name):
        if type_text and type_text.startswith(DEFAULT):  # untyped, but its default says what it takes
            default = ast.literal_eval(type_text[len(DEFAULT):])
            values = TYPED.get(type(default).__name__, [])
            if values:
                return [(v, None) for v in dict.fromkeys([type_text[len(DEFAULT):]] + values)] + \
                    [("None", f"{param_name} may be None")]
            type_text = None
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
        return bool(type_text) and not type_text.startswith(DEFAULT) and \
            ("None" in split_top(type_text, "|") or type_text.startswith("Optional["))

    def expected_function(self, fn):
        if fn.returns is None:
            return f"\n\ndef {EXPECTED}(value):\n    return value\n", EXPECTED
        return f"\n\ndef {EXPECTED}(value: {fn.returns}) -> {fn.returns}:\n    return value\n", EXPECTED

    def model_input(self, fn, args, hypothesis):
        """The model's input, unless a declared type rules it out (passing an int where the annotation
        says str is a wrong call, not a bug). A value of another kind than an untyped parameter's
        default, or None inside a container, is kept with an assumption."""
        result = super().model_input(fn, args, hypothesis)
        if result is None:
            return None
        for param, expr in zip(fn.params, args):
            value = ast.literal_eval(expr)
            if value is None or not param.type:
                continue
            if param.type.startswith(DEFAULT):
                default = ast.literal_eval(param.type[len(DEFAULT):])
                if not isinstance(value, KINDS.get(type(default).__name__, (object,))):
                    result.assumptions.append(f"{param.name} may be {expr} (its default is a {type(default).__name__})")
                continue
            verdict = fits(value, param.type)
            if verdict is False:
                return None
            if verdict == "contains None":
                result.assumptions.append(f"{param.name} may contain None")
        return result

    def call_text(self, fn, args):
        return f"{fn.name}({', '.join(args)})"

    def run_inputs(self, source, fn, inputs, sandbox):
        from runner.executor import run_job

        results, calls = [None] * len(inputs), []
        for n, inp in enumerate(inputs):  # each expression on its own: "1 #" can't swallow its neighbours
            args = [argument(a) for a in inp.args]
            if any(a is UNUSABLE for a in args):
                results[n] = {"ok": False, "kind": "input_error", "error": "InputError",
                              "message": f"not a value the worker can receive: {', '.join(inp.args)}"[:200]}
            else:
                calls.append((n, args))
        if calls:
            job = run_job({"mode": "calls", "source": self.context(source, fn), "func_name": fn.name,
                           "calls": [args for _, args in calls], "call_timeout": CALL_TIMEOUT},
                          timeout=15 + (CALL_TIMEOUT + 2.5) * len(calls), sandbox=sandbox)
            ran = job.get("results")
            if not isinstance(ran, list) or len(ran) != len(calls):
                # the file didn't load (syntax, a missing import, its top-level code) or the worker was lost:
                # nothing reached the function, so that's no finding (all inputs failing so: status error)
                detail = ": ".join(str(job[k]) for k in ("error", "message") if job.get(k)) or "no result"
                ran = [{"ok": False, "kind": "not_entered", "error": job.get("error") or "LoadError",
                        "message": f"the file didn't load: {detail}"[:2000]}] * len(calls)
            for (n, _), result in zip(calls, ran):
                results[n] = result
        return [{**r, "kind": "exit"} if r.get("error") == "SystemExit" else r for r in results]


CALL_TIMEOUT = 1.0  # seconds per input


def literal_default(node):
    """'default:<literal>' for an untyped parameter whose default is a plain str/int/float/bool/bytes."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        text = ast.unparse(node)
    elif isinstance(node, ast.Constant) and type(node.value) in (str, int, float, bool, bytes):
        text = ast.unparse(node)
    else:
        return None
    try:
        return DEFAULT + text if type(ast.literal_eval(text)) in (str, int, float, bool, bytes) else None
    except (ValueError, SyntaxError):
        return None


def has_comment(expr):
    """True when expr holds a # comment (a '#' inside a string literal doesn't count)."""
    import io
    import tokenize

    try:
        return any(token.type == tokenize.COMMENT for token in tokenize.generate_tokens(io.StringIO(expr).readline))
    except (tokenize.TokenError, SyntaxError):
        return True


UNUSABLE = object()  # what argument() returns for a literal the worker can't receive (None itself is a fine input)


def argument(expr):
    """A literal as the worker's tagged JSON, or UNUSABLE when it isn't a value the worker can receive."""
    import worker  # its JSON helpers only: no analysed code runs in this process

    try:
        return worker.encode(ast.literal_eval(expr))
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return UNUSABLE


def fits(value, type_text):
    """Does a literal value fit an annotation? True, False, None (can't tell: a class, a TypeVar...),
    or "contains None" (fits, except for None items). Shallow for unknown kinds, so it never guesses no."""
    try:
        node = ast.parse(type_text, mode="eval").body
    except SyntaxError:
        return None
    return _fits(value, node)


def _fits(value, node):
    if value is None:
        return True  # None at the top is left to nullable() (an assumption), and to "contains None" below
    if isinstance(node, ast.Constant):
        if node.value is None:
            return False
        return None  # a string annotation ("Foo"): a forward reference
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _any(_fits(value, side) for side in (node.left, node.right))
    if isinstance(node, (ast.Name, ast.Attribute)):
        name = node.id if isinstance(node, ast.Name) else node.attr
        if name in ("Any", "object"):
            return True
        if name in KINDS:
            return isinstance(value, KINDS[name]) if KINDS[name] else False
        return None
    if isinstance(node, ast.Subscript):
        base = node.value.id if isinstance(node.value, ast.Name) else \
            node.value.attr if isinstance(node.value, ast.Attribute) else None
        args = list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]
        if base == "Optional":
            return _fits(value, args[0])
        if base == "Union":
            return _any(_fits(value, a) for a in args)
        if base == "Annotated":
            return _fits(value, args[0])
        if base == "Literal":
            try:
                return value in [ast.literal_eval(a) for a in args]
            except ValueError:
                return None
        if base not in KINDS:
            return None
        whole = _fits(value, node.value)
        if whole is not True:
            return whole
        if base in ITEMS and isinstance(value, (list, tuple, set, frozenset)):
            return _items(value, args[0])
        if base in ("tuple", "Tuple") and isinstance(value, tuple):
            if len(args) == 2 and isinstance(args[1], ast.Constant) and args[1].value is Ellipsis:
                return _items(value, args[0])
            if len(args) != len(value):
                return False
            return _all(_fits(item, a) if item is not None else "contains None" for item, a in zip(value, args))
        if base in ("dict", "Dict", "Mapping", "MutableMapping") and isinstance(value, dict) and len(args) == 2:
            return _all([_items(list(value.keys()), args[0]), _items(list(value.values()), args[1])])
        return True
    return None


def _items(values, node):
    return _all(_fits(v, node) if v is not None else "contains None" for v in values)


def _any(verdicts):
    verdicts = list(verdicts)
    if True in verdicts or "contains None" in verdicts:
        return True if True in verdicts else "contains None"
    return None if None in verdicts else False


def _all(verdicts):
    verdicts = list(verdicts)
    if False in verdicts:
        return False
    if None in verdicts:
        return None
    return "contains None" if "contains None" in verdicts else True


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
