"""Phantom analysis backend: the VS Code extension sends the file it's editing, this answers.

    uvicorn backend.app:app --port 8765

POST /static  {"source"}                       -> Specter's static checks (milliseconds)
POST /shadow  {"source"}                       -> shadow run of every public function (seconds)
POST /replay  {"source", "function", "args"}   -> run one failing input again, in a fresh sandbox
GET  /health                                   -> {"ok": true, "sandbox": "docker" | "local"}

Shadow results are cached by a hash of what each function needs to run (its code plus its
context), so an unchanged function is never analysed twice: typing in one function only re-runs
that function and the functions that use it.

The extension sends the whole file because this backend runs on the same machine (localhost).
Only each function and its context goes into the sandbox.
"""
import ast
import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from runner.analyzer import analyze_function, analyze_in_file, replay_input
from runner.context import function_context, public_functions
from runner.executor import choose_sandbox
from runner.static import static_findings

SANDBOX = choose_sandbox(os.environ.get("PHANTOM_SANDBOX", "auto"))
MAX_EXAMPLES = int(os.environ.get("PHANTOM_EXAMPLES", "200"))
CACHE_SIZE = 2000

app = FastAPI(title="Phantom")
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("PHANTOM_JOBS", "4")))


@app.middleware("http")
async def no_browsers(request, call_next):
    """This server runs code it's sent. Browsers add an Origin header; the extension doesn't,
    so a web page can't make it run anything."""
    if "origin" in request.headers:
        return JSONResponse({"detail": "browser requests are not accepted"}, status_code=403)
    return await call_next(request)


class Source(BaseModel):
    source: str


class Replay(BaseModel):
    source: str
    function: str
    args: list


class ShadowCache:
    """Finished and in-progress analyses by key. Two requests for the same function share one run."""

    def __init__(self, size):
        self.size = size
        self.futures = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key, compute):
        with self.lock:
            future = self.futures.get(key)
            if future is None:
                future = self.futures[key] = pool.submit(compute)
                while len(self.futures) > self.size:
                    self.futures.popitem(last=False)
            self.futures.move_to_end(key)
        try:
            return future.result()
        except Exception:
            with self.lock:  # don't cache a failure of Phantom itself
                if self.futures.get(key) is future:
                    del self.futures[key]
            raise


cache = ShadowCache(CACHE_SIZE)


def cached_analyze(source, func_name, **options):
    key = hashlib.sha256(f"{SANDBOX}\0{MAX_EXAMPLES}\0{func_name}\0{source}".encode()).hexdigest()
    return cache.get(key, lambda: analyze_function(source, func_name, **options))


def parse(source):
    try:
        return ast.parse(source)
    except SyntaxError:
        return None  # mid-typing: the extension keeps showing the last results


@app.get("/health")
def health():
    return {"ok": True, "sandbox": SANDBOX}


@app.post("/static")
def static(request: Source):
    tree = parse(request.source)
    if tree is None:
        return {"parsed": False, "findings": []}
    return {"parsed": True, "findings": static_findings(request.source, tree)}


@app.post("/shadow")
def shadow(request: Source):
    tree = parse(request.source)
    if tree is None:
        return {"parsed": False, "findings": []}
    # One thread per function; each waits on its (cached or new) sandbox run in the shared pool.
    with ThreadPoolExecutor(max_workers=8) as waiters:
        findings = list(waiters.map(
            lambda name: analyze_in_file(request.source, tree, name, cached_analyze,
                                         sandbox=SANDBOX, max_examples=MAX_EXAMPLES),
            public_functions(tree)))
    return {"parsed": True, "sandbox": SANDBOX, "findings": findings}


@app.post("/replay")
def replay(request: Replay):
    tree = parse(request.source)
    if tree is None:
        return {"ok": None, "message": "the file doesn't parse right now"}
    snippet = function_context(request.source, request.function, tree)
    return replay_input(snippet, request.function, request.args, SANDBOX)
