"""What a function needs to run on its own, cut out of its file.

The sandbox doesn't get the whole file, only the function plus its context: the imports, and the
top-level functions, classes and constants it uses (and what those use). Everything else, such as
script code at the bottom of the file, is blanked out. Blank lines rather than removed ones, so
every line number in a traceback is still the line number in the user's file.
"""
import ast
import re


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
    definitions = {}  # name -> the top-level statements that define it
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in defined_names(node):
                definitions.setdefault(name, []).append(node)

    keep = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    needed, todo = set(), [func_name]
    while todo:  # follow the names each kept definition uses, transitively
        name = todo.pop()
        if name in needed or name not in definitions:
            continue
        needed.add(name)
        for node in definitions[name]:
            if node not in keep:
                keep.append(node)
                todo.extend(n.id for n in ast.walk(node) if isinstance(n, ast.Name))

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
    return []


def missing_context(finding, file_names):
    """If a confirmed NameError is for a name the file does define, the bug is in our context
    extraction (e.g. a name set inside `if`/`try` at top level), not in the user's code."""
    failure = finding.get("failure") or {}
    if finding.get("status") != "bug" or failure.get("error") != "NameError":
        return None
    match = re.search(r"name '(\w+)' is not defined", failure.get("message", ""))
    return match.group(1) if match and match.group(1) in file_names else None
