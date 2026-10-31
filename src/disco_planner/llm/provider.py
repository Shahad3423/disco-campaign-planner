"""LLM access behind one small interface (DECISIONS D9).

Design:
- Providers only turn (system, user) text into text. Schema handling is ours:
  we ask for JSON, validate with Pydantic, and retry once with the validation
  error. That works the same on Gemini, Claude, or OpenAI.
- Results are cached on disk keyed by a hash of (model, prompt, input). Same input,
  same output: reproducible demos and evals, and the hosted app keeps working
  when the free-tier quota runs out.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from disco_planner.data.catalog import REPO_ROOT

T = TypeVar("T", bound=BaseModel)
CACHE_DIR = REPO_ROOT / "cache" / "llm"
# The disk cache makes evals reproducible and avoids paying twice for identical calls. A "live" run
# (the app's toggle) sets this to False for its own context: no cache read, no cache write.
CACHE_ENABLED: contextvars.ContextVar[bool] = contextvars.ContextVar("cache_enabled", default=True)
# Per-run call accounting, so the UI can say truthfully how an answer was produced.
CALL_STATS: contextvars.ContextVar[dict | None] = contextvars.ContextVar("call_stats", default=None)


def _count(kind: str) -> None:
    stats = CALL_STATS.get()
    if stats is not None:
        stats[kind] = stats.get(kind, 0) + 1


def _load_dotenv() -> None:
    """Tiny .env reader (KEY=value lines) so local runs need no extra dependency."""
    env = REPO_ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv()


class LLMError(RuntimeError):
    pass


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, temperature: float) -> str: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str | None = None, backend: str = "aistudio"):
        """backend="aistudio": free-tier GEMINI_API_KEY. backend="vertex": Google Cloud project
        (GOOGLE_CLOUD_PROJECT) billed to its credits, authenticated by VERTEX_API_KEY if set,
        else by the local gcloud login (application-default credentials). Same models either way."""
        from google import genai
        from google.genai import types

        # Fallback chain: free-tier models get 503 "high demand" and 429 quota errors, so a
        # busy model shouldn't break the demo. gemini-2.5-flash is retired for new keys.
        default_chain = ("gemini-3.5-flash,gemini-3.5-flash-lite" if backend == "vertex"  # Vertex has no *-latest aliases
                         else "gemini-3.5-flash,gemini-flash-lite-latest,gemini-flash-latest")
        chain = model or os.getenv("GEMINI_MODELS", default_chain)
        self.models = [m.strip() for m in chain.split(",")]
        self.model = self.models[0]  # stable id for the cache key (same model on both backends)
        self.backend = backend
        self.last_model_used: str | None = None
        http = types.HttpOptions(timeout=60_000, retry_options=types.HttpRetryOptions(attempts=2))
        if backend == "vertex":
            if os.getenv("VERTEX_API_KEY"):
                self._client = genai.Client(vertexai=True, api_key=os.environ["VERTEX_API_KEY"], http_options=http)
            else:
                self._client = genai.Client(
                    vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                    location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"), http_options=http,
                )
        else:
            self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=http)

    def complete(self, system: str, user: str, temperature: float) -> str:
        from google.genai import errors, types

        last_error: Exception | None = None
        for attempt, model in enumerate(self.models * 2):  # walk the chain twice, backing off
            if attempt >= len(self.models):
                time.sleep(5 * (attempt - len(self.models) + 1))
            try:
                resp = self._client.models.generate_content(
                    model=model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=temperature,
                        response_mime_type="application/json",
                    ),
                )
                self.last_model_used = model
                return resp.text or ""
            except (errors.ServerError, errors.ClientError) as e:
                if getattr(e, "code", None) not in (429, 500, 503, 504):
                    raise
                last_error = e  # overloaded or out of quota: try the next model
        raise LLMError(f"All Gemini models failed; last error: {last_error}")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic

        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
        self._client = anthropic.Anthropic()

    def complete(self, system: str, user: str, temperature: float) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=4096, temperature=temperature,
            system=system, messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")


class FakeProvider:
    """For tests: returns canned JSON chosen by a substring of the user prompt."""

    name = "fake"
    model = "fake"

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[str] = []

    def complete(self, system: str, user: str, temperature: float) -> str:
        self.calls.append(user)
        for key, value in self.responses.items():
            if key in user:
                return value
        raise LLMError("FakeProvider: no canned response matched")


class ChainProvider:
    """Try providers in order (e.g. Vertex on cloud credits, then the free AI Studio key).
    Same model id first, so the disk cache is shared."""

    name = "gemini"

    def __init__(self, providers: list):
        self.providers = providers
        self.model = providers[0].model
        self.last_backend_used: str | None = None

    def complete(self, system: str, user: str, temperature: float) -> str:
        errors_seen = []
        for p in self.providers:
            try:
                out = p.complete(system, user, temperature)
                self.last_backend_used = getattr(p, "backend", p.name)
                return out
            except Exception as e:  # any backend failure: try the next one
                errors_seen.append(f"{getattr(p, 'backend', p.name)}: {str(e)[:160]}")
        raise LLMError("All backends failed: " + " | ".join(errors_seen))


def default_provider() -> Provider:
    choice = os.getenv("LLM_PROVIDER", "gemini")
    if choice == "anthropic":
        return AnthropicProvider()
    providers = []
    if os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEX_API_KEY"):
        try:
            providers.append(GeminiProvider(backend="vertex"))
        except Exception:  # e.g. no gcloud login yet: fall back to the free key, don't crash
            pass
    if os.getenv("GEMINI_API_KEY"):
        providers.append(GeminiProvider(backend="aistudio"))
    if not providers:
        raise LLMError("No LLM credentials: set GOOGLE_CLOUD_PROJECT (Vertex) or GEMINI_API_KEY (AI Studio)")
    return providers[0] if len(providers) == 1 else ChainProvider(providers)


def _extract_json(text: str) -> str:
    """Models sometimes wrap JSON in ``` fences or add a sentence; keep the JSON value."""
    match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    return match.group(0) if match else text


def _coerce(raw: str, schema: type[BaseModel]) -> str:
    """Fix the most common shape slip: for a wrapper schema with one list field (e.g.
    ReviewSet.reviews), models sometimes return the bare list, or a single item."""
    list_fields = [n for n, f in schema.model_fields.items() if getattr(f.annotation, "__origin__", None) is list]
    if len(schema.model_fields) != 1 or len(list_fields) != 1:
        return raw
    field = list_fields[0]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, list):
        return json.dumps({field: data})
    if isinstance(data, dict) and field not in data:
        return json.dumps({field: [data]})
    return raw


def generate(
    provider: Provider,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.2,
    use_cache: bool | None = None,
) -> T:
    if use_cache is None:
        use_cache = CACHE_ENABLED.get()  # per session/thread: a live run in one app session doesn't affect others
    key = hashlib.sha256(f"{provider.model}\n{system}\n{user}\n{temperature}".encode()).hexdigest()[:24]
    path = CACHE_DIR / f"{key}.json"
    if use_cache and path.exists():
        _count("cached")
        return schema.model_validate_json(path.read_text())
    _count("live")

    schema_hint = json.dumps(schema.model_json_schema())
    prompt = f"{user}\n\nReturn ONLY a JSON object matching this JSON Schema:\n{schema_hint}"
    last_error = ""
    for _attempt in range(2):
        raw = provider.complete(system, prompt + last_error, temperature)
        try:
            result = schema.model_validate_json(_coerce(_extract_json(raw), schema))
            break
        except ValidationError as e:
            last_error = f"\n\nYour previous answer failed validation:\n{e}\nFix it."
    else:
        raise LLMError(f"{schema.__name__}: output failed validation twice: {last_error[:500]}")

    if provider.name != "fake" and use_cache:  # a live run never overwrites the cached answers evals rely on
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2))
    return result
