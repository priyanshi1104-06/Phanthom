"""What a function needs to run on its own, cut out of its file.

The sandbox doesn't get the whole file, only the function plus its context: the imports, and the
top-level functions, classes and constants it uses (and what those use), the top-level statements
that fill a table it uses (`for i in range(10): SQUARES.append(i * i)`, `UNITS.update(km=1000)`), and
global setup that only touches imports and builtins (`sys.setrecursionlimit(5000)`). Everything else is
blanked out: script code at the bottom of the file (`main()`, `print(...)`), the entry-point guard
`if __name__ == "__main__":`, and statements that only use other parts of the file. Blank lines rather
than removed ones, so every line number in a traceback is still the line number in the user's file.
A failure while the kept code loads is never a finding (see worker.py).
"""
import ast
import builtins
import re

BUILTINS = frozenset(dir(builtins))
# calls that run the program or print demo output when written as a bare top-level statement
PROGRAM_CALLS = frozenset({"main", "print", "pprint", "input", "exit", "quit", "breakpoint", "help"})
PROGRAM_METHODS = frozenset({"main", "run", "mainloop", "serve_forever", "run_forever", "run_until_complete",
                             "exit", "listen"})
COMPOUND = (ast.For, ast.AsyncFor, ast.While, ast.If, ast.With, ast.AsyncWith, ast.Try, ast.Match) + \
    ((ast.TryStar,) if hasattr(ast, "TryStar") else ())


def public_functions(tree):
    """{name: def line} of the public top-level functions, in file order."""
    return {node.name: node.lineno for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}


def top_level_names(tree):
    """Every name the file defines at top level (functions, classes, assignments, imports)."""
    return {name for node in tree.body for name in defined_names(node)}


def function_context(source, func_name, tree=None):
    """The source with everything blanked except func_name and what it needs."""
    tree = tree or ast.parse(source)
    body = [node for node in tree.body if not entry_guard(node)]
    definitions = {}  # name -> the top-level statements that define it
    imported = set()
    for node in body:
        names = defined_names(node)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported.update(names)
            continue
        for name in names:
            definitions.setdefault(name, []).append(node)
    data = {name for name, nodes in definitions.items()  # names that hold values, not defs or classes
            if any(not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in nodes)}

    keep = [node for node in body if isinstance(node, (ast.Import, ast.ImportFrom))]
    others = [node for node in body if isinstance(node, (ast.Expr,) + COMPOUND) and not program_call(node)]
    needed, todo = set(), [func_name]
    while True:
        while todo:  # follow the names each kept definition uses, transitively
            name = todo.pop()
            if name in needed or name not in definitions:
                continue
            needed.add(name)
            for node in definitions[name]:
                if node not in keep:
                    keep.append(node)
                    todo.extend(loaded_names(node))
        for node in others:  # statements that change a value the function uses: a loop filling a table
            if node not in keep and loaded_names(node) & needed & data:
                keep.append(node)
                todo.extend(loaded_names(node))
        if not todo:
            break
    for node in others:  # global setup that only touches imports and builtins
        used = loaded_names(node) - stored_names(node)
        if node not in keep and used and used <= imported | BUILTINS and not (used & set(definitions)):
            keep.append(node)

    lines = source.splitlines(keepends=True)
    kept_lines = set()
    for node in keep:
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        kept_lines.update(range(first, node.end_lineno + 1))
    return "".join(line if number in kept_lines else "\n"
                   for number, line in enumerate(lines, start=1))


def defined_names(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(alias.asname or alias.name).split(".")[0] for alias in node.names]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)]
    if isinstance(node, COMPOUND):  # `try: import json` / `except ImportError: json = None`
        return sorted({name for child in ast.iter_child_nodes(node) for stmt in _statements(child)
                       for name in defined_names(stmt)} | stored_names(node, shallow=True))
    return []


def _statements(node):
    if isinstance(node, ast.stmt):
        return [node]
    if isinstance(node, (ast.excepthandler, ast.match_case)):
        return node.body
    return []


def loaded_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def stored_names(node, shallow=False):
    """Names a statement assigns (for a loop: its target), not looking into nested defs when shallow."""
    if shallow:
        targets = [node.target] if isinstance(node, (ast.For, ast.AsyncFor)) else \
            [item.optional_vars for item in node.items if item.optional_vars is not None] \
            if isinstance(node, (ast.With, ast.AsyncWith)) else []
        return {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def entry_guard(node):
    """`if __name__ == "__main__":`: the program's entry point, never part of a function's context."""
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    sides = [node.test.left] + node.test.comparators
    return any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides) and \
        any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides)


def program_call(node):
    """A bare top-level call that runs the program or prints: main(), print(...), asyncio.run(main())."""
    if not isinstance(node, ast.Expr):
        return False
    call = node.value.value if isinstance(node.value, ast.Await) else node.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in PROGRAM_CALLS
    if isinstance(func, ast.Attribute):
        return func.attr in PROGRAM_METHODS
    return False


def missing_context(finding, file_names):
    """If a confirmed NameError is for a name the file does define, the bug is in our context
    extraction (e.g. a name set inside `if`/`try` at top level), not in the user's code."""
    failure = finding.get("failure") or {}
    if finding.get("status") != "bug" or failure.get("error") != "NameError":
        return None
    match = re.search(r"name '(\w+)' is not defined", failure.get("message", ""))
    return match.group(1) if match and match.group(1) in file_names else None
