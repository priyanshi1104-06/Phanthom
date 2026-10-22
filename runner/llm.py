"""Phantom's model layer: one call shape for every layer, whatever the provider.

    ask(layer, system, prompt, schema) -> dict, or None when no model answer is available

Each layer has its own model and effort, set in .env (see .env.example):
    specter - fast model, every typing pause        (default claude-haiku-5-5, effort low)
    analyst  - large model, seconds                   (default claude-sonnet-5-5, effort medium)
    oracle   - frontier model running as an agent     (default claude-fable-5-1, effort high)

Providers (PHANTOM_MODEL_PROVIDER):
    anthropic - the Claude API through the Anthropic SDK (default)
    scripted  - canned answers from a JSON file (PHANTOM_MODEL_SCRIPT), for tests and demos
    none      - models off: Phantom still runs its static checks and the sandbox

A model answer is never shown to the user as a bug by itself: it is a hypothesis that the
sandbox has to reproduce first. That also limits what prompt injection hidden in the analysed
code can do - it can suppress hypotheses, never create a confirmed bug.
"""
import json
import os
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAYERS = {
    "specter": {"model": "claude-haiku-5-5", "effort": "low", "max_tokens": 4000, "timeout": 20.0},
    "analyst": {"model": "claude-sonnet-5-5", "effort": "medium", "max_tokens": 16000, "timeout": 120.0},
    "oracle": {"model": "claude-fable-5-1", "effort": "high", "max_tokens": 32000, "timeout": 600.0},
}

_provider = None
_status = {"provider": None, "available": False, "detail": "not started"}
_lock = threading.Lock()


def load_env(path=ROOT / ".env"):
    """KEY=VALUE lines from .env into os.environ (already-set variables win). No dependency needed."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# Phantom's tiers: what users pick in the editor. Which supplier model sits behind each tier is decided
# here, on the server (PHANTOM_TIER_SWIFT=... overrides), so the editor never names a model or a supplier.
TIERS = {
    "swift": {"label": "Swift", "detail": "fastest", "model": "claude-haiku-5-5"},
    "balanced": {"label": "Balanced", "detail": "deeper, a little slower", "model": "claude-sonnet-5-5"},
    "deep": {"label": "Deep", "detail": "for tricky code", "model": "claude-opus-5-5"},
    "deepest": {"label": "Deepest", "detail": "slowest, most thorough", "model": "claude-fable-5-1"},
}
LAYER_TIERS = {"specter": "swift", "analyst": "balanced", "oracle": "deepest"}  # defaults
MODEL_ID = re.compile(r"[a-z][a-z0-9.:/-]{1,80}")


def tier_model(tier):
    """The model id behind a tier, or None for an unknown tier."""
    if tier not in TIERS:
        return None
    model = os.environ.get(f"PHANTOM_TIER_{tier.upper()}", TIERS[tier]["model"])
    return model if MODEL_ID.fullmatch(model) else None


def tier_choices():
    """What the editor may show: tier ids, names and descriptions, never model ids."""
    return [{"id": tier, "label": t["label"], "detail": t["detail"]} for tier, t in TIERS.items()]


def layer_settings(layer):
    """Model, effort and limits for a layer; PHANTOM_<LAYER>_MODEL / _EFFORT override the defaults."""
    settings = dict(LAYERS[layer])
    for field in ("model", "effort"):
        settings[field] = os.environ.get(f"PHANTOM_{layer.upper()}_{field.upper()}", settings[field])
    return settings


def status():
    """What the status bar shows: which provider, and whether models can be used right now."""
    provider()
    return dict(_status)


def provider():
    global _provider
    with _lock:
        if _provider is None:
            _provider = _make_provider(os.environ.get("PHANTOM_MODEL_PROVIDER", "anthropic"))
        return _provider


def set_provider(new_provider):
    """Swap the provider (tests use this to inject a scripted one). None = re-read the settings."""
    global _provider
    with _lock:
        _provider = new_provider
        if new_provider is not None:
            _status.update(provider=type(new_provider).__name__, available=True, detail="ready")


def ask(layer, system, prompt, schema, tier=None):
    """One structured answer from the layer's model (or the model behind `tier`, the user's pick for
    this request), or None (models off, error, refusal, bad JSON)."""
    current = provider()
    if current is None:
        return None
    settings = {**layer_settings(layer), "layer": layer}
    if tier_model(tier):
        settings["model"] = tier_model(tier)
    try:
        answer = current.complete_json(settings, system, prompt, schema)
    except ModelUnavailable as exc:
        _status.update(available=False, detail=str(exc))
        return None
    if answer is not None:
        _status.update(available=True, detail="ready")
    return answer


class ModelUnavailable(Exception):
    """The provider can't be used at all (no credentials, no network): stop asking until restart."""


def _make_provider(name):
    if name == "none":
        _status.update(provider="none", available=False, detail="models are switched off (PHANTOM_MODEL_PROVIDER=none)")
        return None
    if name == "scripted":
        _status.update(provider="scripted", available=True, detail="scripted answers")
        return ScriptedProvider.from_file(os.environ.get("PHANTOM_MODEL_SCRIPT", ""))
    if name == "anthropic":
        try:
            created = AnthropicProvider()
        except Exception as exc:  # the SDK isn't installed, or no credentials at all
            _status.update(provider="anthropic", available=False, detail=f"Anthropic API not available: {exc}")
            return None
        client = created.client
        if not (client.api_key or client.auth_token or getattr(client, "credentials", None)):
            # without this the status says "ready" until the first request fails, so the editor shows AI tiers
            # while only Phantom's engine runs
            _status.update(provider="anthropic", available=False, detail="no AI key: Phantom's engine only "
                           "(\"Phantom: Set AI Key\", or ANTHROPIC_API_KEY in .env)")
            return None
        _status.update(provider="anthropic", available=True, detail="ready")
        return created
    raise ValueError(f"unknown PHANTOM_MODEL_PROVIDER {name!r} (anthropic, scripted or none)")


class AnthropicProvider:
    """Claude through the official SDK, with structured JSON output."""

    def __init__(self, client=None):
        import anthropic

        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY (or an `ant` profile)
        self.disabled = None

    def complete_json(self, settings, system, prompt, schema):
        if self.disabled:
            raise ModelUnavailable(self.disabled)
        errors = self.anthropic
        try:
            response = self.client.with_options(timeout=settings["timeout"], max_retries=1).messages.create(
                model=settings["model"],
                max_tokens=settings["max_tokens"],  # room for adaptive thinking plus the JSON
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": settings["effort"],
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except (errors.AuthenticationError, errors.PermissionDeniedError) as exc:
            self.disabled = (f"Anthropic API refused the credentials ({exc.status_code}): "
                             "check ANTHROPIC_API_KEY in .env")
            raise ModelUnavailable(self.disabled) from exc
        except errors.NotFoundError as exc:
            raise ModelUnavailable(f"model {settings['model']!r} not found: check PHANTOM_*_MODEL in .env") from exc
        except (errors.RateLimitError, errors.APIStatusError, errors.APIConnectionError):
            return None  # transient: this pause goes without a model answer, the next one tries again
        except errors.AnthropicError as exc:
            self.disabled = f"Anthropic API not usable: {exc}"
            raise ModelUnavailable(self.disabled) from exc
        except TypeError as exc:  # how the SDK reports that no credentials could be found
            if "authentication method" not in str(exc):
                raise
            self.disabled = "no Anthropic credentials: add ANTHROPIC_API_KEY to .env"
            raise ModelUnavailable(self.disabled) from exc
        if response.stop_reason != "end_turn":  # refusal, max_tokens: no usable JSON
            return None
        text = next((block.text for block in response.content if block.type == "text"), None)
        try:
            return json.loads(text) if text else None
        except ValueError:
            return None


class ScriptedProvider:
    """Canned answers: the first rule whose "match" text occurs in the prompt (and whose "layer"
    fits, if given) supplies the answer. Lets tests and demos run without an API key."""

    def __init__(self, rules):
        self.rules = rules
        self.calls = []

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text()) if path else [])

    def complete_json(self, settings, system, prompt, schema):
        self.calls.append({"settings": settings, "system": system, "prompt": prompt, "schema": schema})
        for rule in self.rules:
            if rule.get("match", "") in prompt and rule.get("layer", settings["layer"]) == settings["layer"]:
                return rule["answer"]
        return None
