"""Phantom worker: runs untrusted Python and reports what happened as one line of JSON.

Phantom starts this file as a separate process (inside the Docker sandbox when available),
sends one job on stdin and reads one JSON result from the last line of stdout. The analysed
code only ever runs here, never in Phantom's own process.

Call job - run the function once with given arguments (used to replay a repro):
    {"mode": "call", "source": "...", "func_name": "div", "args": [1, 0]}
 -> {"ok": true, "result": 2.0}
  | {"ok": false, "kind": "exception" | "timeout" | "return_type", "error": "ZeroDivisionError",
     "message": "division by zero", "line": 1, "traceback": "..."}

Fuzz job - generate inputs from the type hints with Hypothesis until one fails:
    {"mode": "fuzz", "source": "...", "func_name": "div", "max_examples": 200}
 -> {"status": "bug", "examples": 12, "input": {"args": [0, 0], "call": "div(0, 0)"}, "failure": {...}}
  | {"status": "no_bug_found", "examples": 200}
  | {"status": "skipped", "reason": "parameter 'x' has no type hint"}
  | {"status": "error", "error": "SyntaxError", "message": "..."}

Arguments travel as JSON. Values JSON can't hold are tagged so they come back exactly:
{"$tuple": [...]}, {"$set": [...]}, {"$frozenset": [...]}, {"$bytes": "<base64>"},
{"$float": "nan"}, {"$complex": [re, im]}, {"$dict": [[key, value], ...]}.
"""
import base64
import contextlib
import inspect
import io
import json
import linecache
import math
import signal
import sys
import traceback
import types
import typing

SNIPPET = "<snippet>"  # file name the analysed code gets in tracebacks
MEMORY_LIMIT = 1024 * 1024 * 1024  # bytes of address space the worker may use
CPU_LIMIT = 120  # CPU seconds before the OS kills the worker


class Timeout(BaseException):
    """One call ran too long. A BaseException, so `except Exception` in the analysed code can't swallow it."""


class ReturnTypeError(Exception):
    """The function returned a value its return annotation rules out (e.g. None from `-> int`)."""


class StopFuzzing(BaseException):
    """Ends the Hypothesis run at once (a timeout or sys.exit is not worth shrinking)."""


# ---------- running the analysed code ----------

def load_function(source, func_name):
    """exec the source into a fresh namespace and return the function called func_name."""
    # Register the source so tracebacks can show the analysed code's lines.
    linecache.cache[SNIPPET] = (len(source), None, source.splitlines(True), SNIPPET)
    namespace = {"__name__": "__phantom__"}
    exec(compile(source, SNIPPET, "exec"), namespace)
    func = namespace.get(func_name)
    if not callable(func):
        raise LookupError(f"no function named {func_name!r} in the source")
    return func


@contextlib.contextmanager
def time_limit(seconds):
    """Raise Timeout inside the block after `seconds` (then every 0.1 s, in case the code catches it)."""
    def on_alarm(signum, frame):
        raise Timeout()

    previous = signal.signal(signal.SIGALRM, on_alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds, 0.1)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def call_once(func, args, timeout):
    """Call func(*args) with a time limit and check the result against the return annotation."""
    with time_limit(timeout):
        value = func(*args)
    hint = return_hint(func)
    if hint is not None and not matches_type(value, hint):
        raise ReturnTypeError(
            f"returned {short_repr(value)} ({type(value).__name__}), "
            f"but the annotation says -> {type_name(hint)}"
        )
    return value


def describe_failure(exc, func, timeout):
    """Turn an exception from call_once into the failure dict every later layer builds on."""
    if isinstance(exc, Timeout):
        kind, message = "timeout", f"no result after {timeout:g} s (possible infinite loop)"
    elif isinstance(exc, ReturnTypeError):
        kind, message = "return_type", safe_str(exc)
    else:
        kind, message = "exception", safe_str(exc)

    # Skip the worker's own frames: the traceback starts at the analysed code.
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename != SNIPPET:
        tb = tb.tb_next
    snippet_frames = [frame for frame in traceback.extract_tb(tb) if frame.filename == SNIPPET]
    if snippet_frames:
        line = snippet_frames[-1].lineno  # innermost line of the analysed code: where it failed
    else:
        line = getattr(getattr(func, "__code__", None), "co_firstlineno", None)  # e.g. wrong return type: the def
    if kind == "timeout":  # where the code was when time ran out
        text = "".join(traceback.format_list(snippet_frames)) + f"Timeout: {message}\n"
    elif kind == "return_type":
        text = f"ReturnTypeError: {message}\n"
    else:
        text = "".join(traceback.format_exception(type(exc), exc, tb))
    return {
        "kind": kind,
        "error": "Timeout" if kind == "timeout" else type(exc).__name__,
        "message": message,
        "line": line,
        "traceback": text,
    }


# ---------- return-type check ----------

def return_hint(func):
    try:
        return typing.get_type_hints(func).get("return")
    except Exception:
        return None  # unresolvable annotations: don't guess


def matches_type(value, hint):
    """Shallow isinstance check against an annotation. Unknown kinds of hint count as a match."""
    if hint is typing.Any or hint is object:
        return True
    if hint is None or hint is type(None):
        return value is None
    origin = typing.get_origin(hint)
    if origin in (typing.Union, getattr(types, "UnionType", typing.Union)):  # Optional[int], int | None
        return any(matches_type(value, arg) for arg in typing.get_args(hint))
    if origin is typing.Literal:
        return value in typing.get_args(hint)
    if origin is typing.Annotated:
        return matches_type(value, typing.get_args(hint)[0])
    if isinstance(origin, type):
        return isinstance(value, origin)  # list[int] -> is it a list (elements not checked)
    if isinstance(hint, type):
        if hint is float:
            return isinstance(value, (int, float))  # an int is fine where a float is expected
        if hint is complex:
            return isinstance(value, (int, float, complex))
        return isinstance(value, hint)
    return True


def type_name(hint):
    return hint.__name__ if isinstance(hint, type) else repr(hint).replace("typing.", "")


# ---------- the two job modes ----------

def run_call(job):
    func = load_function(job["source"], job["func_name"])
    timeout = job.get("call_timeout", 1.0)
    try:
        value = call_once(func, decode(job.get("args", [])), timeout)
    except (Exception, Timeout, SystemExit) as exc:
        return {"ok": False, **describe_failure(exc, func, timeout)}
    try:
        return {"ok": True, "result": encode(value)}
    except TypeError:
        return {"ok": True, "result": short_repr(value)}


def run_fuzz(job):
    try:
        from hypothesis import HealthCheck, Verbosity, given, settings
        from hypothesis import strategies as st
    except ImportError:
        return {"status": "error", "error": "ImportError",
                "message": "Hypothesis is not installed: pip install -r requirements.txt"}

    func = load_function(job["source"], job["func_name"])
    timeout = job.get("call_timeout", 1.0)
    strategies = input_strategies(func, st)
    if isinstance(strategies, str):
        return {"status": "skipped", "reason": strategies}

    examples = 0
    failures = []  # (input, exception) for every failing call; the last one is the shrunk minimum

    def attempt(args):
        nonlocal examples
        examples += 1
        # Snapshot the input before the call: the function may mutate its arguments.
        snapshot = {"args": encode_or_none(list(args)), "call": call_text(job["func_name"], args)}
        try:
            call_once(func, args, timeout)
        except (Timeout, SystemExit) as exc:
            failures.append((snapshot, exc))
            raise StopFuzzing() from None  # stop now: shrinking a timeout costs a timeout per step
        except Exception as exc:
            failures.append((snapshot, exc))
            raise

    @settings(
        max_examples=job.get("max_examples", 200),
        deadline=None,  # our own time limit handles slow calls
        database=None,  # write nothing to disk (the sandbox filesystem is read-only)
        derandomize=True,  # same code -> same inputs -> same result every run
        report_multiple_bugs=False,  # one bug, and Hypothesis replays its minimal input last
        suppress_health_check=list(HealthCheck),
        verbosity=Verbosity.quiet,
    )
    @given(st.tuples(*strategies))
    def search(args):
        attempt(args)

    try:
        search()
    except StopFuzzing:
        pass
    except Exception as exc:
        if not failures:  # Hypothesis itself failed, e.g. it can't build a type
            return {"status": "error", "error": type(exc).__name__, "message": safe_str(exc)}

    if not failures:
        return {"status": "no_bug_found", "examples": examples}
    snapshot, exc = failures[-1]
    return {"status": "bug", "examples": examples, "input": snapshot,
            "failure": describe_failure(exc, func, timeout)}


def input_strategies(func, st):
    """One Hypothesis strategy per positional parameter, from its type hint (or a reason to skip)."""
    try:
        hints = typing.get_type_hints(func)
    except Exception as exc:
        return f"can't read the type hints: {safe_str(exc)}"
    strategies = []
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        has_default = param.default is not param.empty
        if param.kind == param.KEYWORD_ONLY:
            if not has_default:
                return f"keyword-only parameter {param.name!r} has no default"
            continue
        if param.name in hints:
            strategies.append(st.from_type(hints[param.name]))
        elif has_default:
            strategies.append(st.just(param.default))
        else:
            return f"parameter {param.name!r} has no type hint"
    return strategies


# ---------- JSON encoding of arguments and results ----------

def encode(value):
    """Python value -> JSON-safe data that decode() turns back into an equal value."""
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is float:
        return value if math.isfinite(value) else {"$float": repr(value)}
    if kind is list:
        return [encode(item) for item in value]
    if kind is tuple:
        return {"$tuple": [encode(item) for item in value]}
    if kind in (set, frozenset):
        return {"$" + kind.__name__: [encode(item) for item in value]}
    if kind is bytes:
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if kind is complex:
        return {"$complex": [encode(value.real), encode(value.imag)]}
    if kind is dict:
        if all(type(key) is str and not key.startswith("$") for key in value):
            return {key: encode(item) for key, item in value.items()}
        return {"$dict": [[encode(key), encode(item)] for key, item in value.items()]}
    raise TypeError(f"can't encode a {kind.__name__}")


def decode(data):
    if isinstance(data, list):
        return [decode(item) for item in data]
    if isinstance(data, dict):
        if len(data) == 1:
            (tag, inner), = data.items()
            if tag == "$float":
                return float(inner)
            if tag == "$tuple":
                return tuple(decode(item) for item in inner)
            if tag == "$set":
                return {decode(item) for item in inner}
            if tag == "$frozenset":
                return frozenset(decode(item) for item in inner)
            if tag == "$bytes":
                return base64.b64decode(inner)
            if tag == "$complex":
                return complex(decode(inner[0]), decode(inner[1]))
            if tag == "$dict":
                return {decode(key): decode(item) for key, item in inner}
        return {key: decode(item) for key, item in data.items()}
    return data


def encode_or_none(value):
    try:
        return encode(value)
    except TypeError:
        return None  # can't be replayed exactly; the "call" text still shows it


def call_text(func_name, args):
    return f"{func_name}({', '.join(short_repr(arg, 200) for arg in args)})"


def short_repr(value, limit=80):
    text = safe_str(value, repr)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def safe_str(value, convert=str):
    try:
        return convert(value)
    except Exception:
        return f"<{type(value).__name__}>"


# ---------- entry point ----------

def limit_resources():
    """Cap memory and CPU time. Soft = hard limit, so the analysed code can't raise them."""
    try:
        import resource
    except ImportError:  # not on Linux/macOS: rely on the sandbox alone
        return
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT))


def handle(job):
    mode = job.get("mode", "call")
    try:
        if mode == "fuzz":
            return run_fuzz(job)
        return run_call(job)
    except Exception as exc:  # the source didn't load: syntax error, missing function, ...
        error = {"error": type(exc).__name__, "message": safe_str(exc)}
        return {"status": "error", **error} if mode == "fuzz" else {"ok": False, "kind": "error", **error}


def main():
    limit_resources()
    job = json.load(sys.stdin)
    # stdout is reserved for our JSON: whatever the analysed code prints is captured and dropped.
    with contextlib.redirect_stdout(io.StringIO()):
        result = handle(job)
    # Leading newline: our JSON starts a fresh line even if the code wrote to the real stdout.
    sys.stdout.write("\n" + json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
