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

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from disco_planner.data.catalog import REPO_ROOT

T = TypeVar("T", bound=BaseModel)
CACHE_DIR = REPO_ROOT / "cache" / "llm"


class LLMError(RuntimeError):
    pass


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, temperature: float) -> str: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str | None = None):
        from google import genai

        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    def complete(self, system: str, user: str, temperature: float) -> str:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=temperature,
                response_mime_type="application/json",
            ),
        )
        return resp.text or ""


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


def default_provider() -> Provider:
    choice = os.getenv("LLM_PROVIDER", "gemini")
    if choice == "anthropic":
        return AnthropicProvider()
    return GeminiProvider()


def _extract_json(text: str) -> str:
    """Models sometimes wrap JSON in ``` fences or add a sentence; keep the object."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def generate(
    provider: Provider,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.2,
    use_cache: bool = True,
) -> T:
    key = hashlib.sha256(f"{provider.model}\n{system}\n{user}\n{temperature}".encode()).hexdigest()[:24]
    path = CACHE_DIR / f"{key}.json"
    if use_cache and path.exists():
        return schema.model_validate_json(path.read_text())

    schema_hint = json.dumps(schema.model_json_schema())
    prompt = f"{user}\n\nReturn ONLY a JSON object matching this JSON Schema:\n{schema_hint}"
    last_error = ""
    for _attempt in range(2):
        raw = provider.complete(system, prompt + last_error, temperature)
        try:
            result = schema.model_validate_json(_extract_json(raw))
            break
        except ValidationError as e:
            last_error = f"\n\nYour previous answer failed validation:\n{e}\nFix it."
    else:
        raise LLMError(f"{schema.__name__}: output failed validation twice: {last_error[:500]}")

    if provider.name != "fake":
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2))
    return result
