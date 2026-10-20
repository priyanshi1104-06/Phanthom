"""Language plugins: one module per language in this package, found automatically.

    languages.get("go"), languages.for_vscode("typescript"), languages.for_path("app/main.rs")
"""
import importlib
import logging
import pkgutil
from pathlib import PurePath

from runner.languages.base import Language

BROKEN = {}  # plugin module -> why it didn't load (one broken plugin must not take the others down)


def _load():
    plugins = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name == "base":
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:
            BROKEN[info.name] = f"{type(exc).__name__}: {exc}"
            logging.getLogger("phantom").warning("language plugin %s not loaded: %s", info.name, BROKEN[info.name])
            continue
        for value in vars(module).values():
            if isinstance(value, type) and issubclass(value, Language) and value.name:
                plugins.setdefault(value.name, value())
    return plugins


LANGUAGES = _load()


def get(name):
    return LANGUAGES.get(name)


def for_vscode(language_id):
    return next((lang for lang in LANGUAGES.values() if language_id in lang.vscode_ids), None)


def for_path(path):
    suffix = PurePath(path).suffix.lower()
    return next((lang for lang in LANGUAGES.values() if suffix in lang.extensions), None)


def resolve(language=None, path=None):
    """A plugin from a registry name, a VS Code languageId, or a file path."""
    return (get(language) or for_vscode(language) if language else None) or (for_path(path) if path else None)
