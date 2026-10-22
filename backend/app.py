"""Phantom analysis backend: the VS Code extension sends the file it's editing, this answers.

    python -m backend        (uvicorn on 127.0.0.1:8765)

GET  /health                                         -> sandbox per language, model status
POST /static  {"source", "language"?}                -> Python static checks (milliseconds; other languages: [])
POST /triage  {"source", "language"?, "path"?}       -> Specter's risk rating of the file and the edit (milliseconds)
POST /specter {"source", "language"?, "path"?, "model"?} -> Specter: reproduced failing inputs per function (seconds)
POST /analyst {"source", "language"?, "path"?, "model"?} -> Analyst: reproduced wrong results per function (seconds)
POST /replay  {"source", "language"?, "path"?, "function", "input", "expected"?} -> run one finding's input again,
              fresh sandbox (with "expected": a wrong-result finding, compared with that value)

`language` is a VS Code languageId or a Phantom language name; without it the file extension of
`path` decides, and without both it is Python (what the Phase 2 extension sent).

Specter caches each function's result by a hash of the code it needs, so typing in one function
re-runs only that function. The backend remembers each file's function texts from the last
/specter call, so triage can tell which functions the edit changed.
Analyst keeps its own memory of each file, so the functions edited since its last look (and their callers)
are the first ones its model reads.
The extension sends the whole file because this backend runs on the same machine (localhost).
"""
import threading
from collections import OrderedDict

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from runner import analyst, languages, llm, specter
from runner.static import static_findings

app = FastAPI(title="Phantom")


@app.middleware("http")
async def no_browsers(request, call_next):
    """This server runs code it's sent. Browsers add an Origin header; the extension doesn't,
    so a web page can't make it run anything."""
    if "origin" in request.headers:
        return JSONResponse({"detail": "browser requests are not accepted"}, status_code=403)
    return await call_next(request)


class Source(BaseModel):
    source: str
    language: str | None = None
    path: str | None = None
    model: bool = True
    models: dict[str, str] | None = None  # the user's tier per layer: {"specter": "balanced"}


class Replay(BaseModel):
    source: str
    function: str
    language: str | None = None
    path: str | None = None
    input: dict | None = None  # a finding's "input": {"exprs": [...]} or {"args": [...]}
    expected: str | None = None  # a wrong-result finding's expected value, as the harness shows it
    args: list | None = None  # Phase 2 extension: Python arguments as tagged JSON


class LastLook:
    """Each file's function texts from the last Specter run (the 500 most recent files)."""

    def __init__(self, size=500):
        self.size, self.texts, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            return self.texts.get(key)

    def put(self, key, texts):
        if texts is None:
            return
        with self.lock:
            self.texts[key] = texts
            self.texts.move_to_end(key)
            while len(self.texts) > self.size:
                self.texts.popitem(last=False)


last_look = LastLook()
analyst_look = LastLook()


def language_of(request):
    if request.language or request.path:
        return request.language, request.path
    return "python", None


def file_key(request):
    language, path = language_of(request)
    return f"{language}\0{path or ''}"


BUILT = ("specter", "analyst")  # Oracle arrives in 0.3


@app.get("/health")
def health():
    ready = {}
    for lang in languages.LANGUAGES.values():
        ready[lang.name] = {"title": lang.title, "vscode_ids": list(lang.vscode_ids), "sandbox": lang.sandbox()}
    python = ready.get("python", {}).get("sandbox")
    layers = {name: {"tier": llm.LAYER_TIERS[name], "built": name in BUILT} for name in llm.LAYERS}
    return {"ok": True, "sandbox": python, "languages": ready, "model": llm.status(),
            "layers": layers, "tiers": llm.tier_choices()}


@app.post("/static")
def static(request: Source):
    language, path = language_of(request)
    lang = languages.resolve(language, path)
    if lang is None:
        return {"parsed": False, "supported": False, "findings": []}
    if lang.has_errors(request.source):
        return {"parsed": False, "findings": []}  # mid-typing: the extension keeps the last results
    findings = static_findings(request.source) if lang.name == "python" else []
    return {"parsed": True, "findings": findings}


@app.post("/triage")
def triage(request: Source):
    language, path = language_of(request)
    return specter.assess(request.source, language, path, previous=last_look.get(file_key(request)))


@app.post("/specter")
def run_specter(request: Source):
    language, path = language_of(request)
    key = file_key(request)
    answer = specter.run(request.source, language, path, use_model=request.model, previous=last_look.get(key),
                         models=request.models)
    if answer.get("parsed"):
        last_look.put(key, specter.function_texts(request.source, language, path))
    return answer


@app.post("/analyst")
def run_analyst(request: Source):
    language, path = language_of(request)
    key = file_key(request)
    answer = analyst.run(request.source, language, path, use_model=request.model, previous=analyst_look.get(key),
                         models=request.models)
    if answer.get("parsed"):
        analyst_look.put(key, specter.function_texts(request.source, language, path))
    return answer


@app.post("/replay")
def replay(request: Replay):
    language, path = language_of(request)
    given = request.input or {"args": request.args}
    if request.expected is not None:
        return analyst.replay(request.source, language, request.function, given, request.expected, path)
    return specter.replay(request.source, language, request.function, given, path)
