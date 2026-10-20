"""Specter's static checks: milliseconds, no code is run.

These are hints, not confirmed bugs: only a failing input from the shadow run is "confirmed".
Pyflakes provides the checks; style-only messages (unused imports, ...) are dropped, since
Phantom looks for bugs, not lint.
"""
import ast

from pyflakes import checker

STYLE_ONLY = {
    "UnusedImport", "UnusedVariable", "UnusedAnnotation", "UnusedIndirectAssignment",
    "RedefinedWhileUnused", "ImportShadowedByLoopVar", "ImportStarUsed", "ImportStarUsage",
    "FStringMissingPlaceholders",
}
MUTABLE = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)


def static_findings(source, tree=None):
    """[{"line", "col", "rule", "message"}] sorted by position. Raises SyntaxError if unparsable."""
    tree = tree or ast.parse(source)
    findings = [
        {"line": m.lineno, "col": m.col, "rule": type(m).__name__, "message": m.message % m.message_args}
        for m in checker.Checker(tree, filename="<editor>").messages
        if type(m).__name__ not in STYLE_ONLY
    ]
    findings += mutable_defaults(tree)
    return sorted(findings, key=lambda f: (f["line"], f["col"]))


def mutable_defaults(tree):
    """def f(items=[]): the one default list is shared by every call."""
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                if isinstance(default, MUTABLE):
                    findings.append({
                        "line": default.lineno, "col": default.col_offset, "rule": "MutableDefault",
                        "message": "mutable default argument: one object is shared by every call",
                    })
    return findings
