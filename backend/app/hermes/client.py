"""LLM clients behind one small interface: local Ollama, OpenRouter, and a mock for tests and CI.

A client makes exactly one call and returns the text with what the provider reported about it.
It never retries and never repairs output: validation, the retry and the trace row belong to the
caller. Cost is what the provider reports; when it reports none, cost is unknown (None), never a
guess. A local Ollama call costs 0.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.ingest.base import scrub

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_TIMEOUT = 300.0  # a local model on CPU can take minutes


class LlmError(Exception):
    """The call itself failed (network, HTTP status, empty answer). Not a bad answer."""


@dataclass(frozen=True)
class LlmResult:
    text: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None


class LlmClient(Protocol):
    tier: str  # 'cheap' or 'better'
    model: str

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> LlmResult: ...


def _post(client: httpx.Client, url: str, what: str, secret: str = "", **kwargs) -> dict:
    try:
        response = client.post(url, timeout=LLM_TIMEOUT, **kwargs)
    except httpx.HTTPError as exc:
        raise LlmError(scrub(f"{what}: {type(exc).__name__}: {exc}", (secret,))) from exc
    if response.status_code != 200:
        body = response.text[:300].replace("\n", " ")
        raise LlmError(scrub(f"{what}: HTTP {response.status_code}: {body}", (secret,)))
    try:
        payload = response.json()
    except ValueError as exc:
        raise LlmError(f"{what}: the response is not JSON") from exc
    if not isinstance(payload, dict):
        raise LlmError(f"{what}: expected an object, got {type(payload).__name__}")
    return payload


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class OllamaClient:
    """A model served by Ollama on the host. Nothing leaves the machine."""

    def __init__(self, client: httpx.Client, base_url: str, model: str, tier: str = "cheap"):
        self._client = client
        self._url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.tier = tier

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> LlmResult:
        body = {
            "model": self.model,
            "stream": False,
            # A schema makes Ollama constrain the answer to that shape; "json" only to valid JSON.
            "format": json_schema or "json",
            "options": {"temperature": 0, "seed": 0},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        started = time.perf_counter()
        payload = _post(self._client, self._url, f"Ollama {self.model}", json=body)
        latency = int((time.perf_counter() - started) * 1000)
        text = (payload.get("message") or {}).get("content")
        if not isinstance(text, str) or not text.strip():
            raise LlmError(f"Ollama {self.model}: the answer is empty")
        return LlmResult(
            text=text,
            latency_ms=latency,
            prompt_tokens=_int_or_none(payload.get("prompt_eval_count")),
            completion_tokens=_int_or_none(payload.get("eval_count")),
            cost_usd=0.0,
        )


class OpenRouterClient:
    def __init__(self, client: httpx.Client, api_key: str, model: str, tier: str):
        self._client = client
        self._api_key = api_key
        self.model = model
        self.tier = tier

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> LlmResult:
        if not self._api_key:
            raise LlmError("OpenRouter: OPENROUTER_API_KEY is not set")
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        started = time.perf_counter()
        payload = _post(
            self._client,
            OPENROUTER_URL,
            f"OpenRouter {self.model}",
            secret=self._api_key,
            json=body,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        latency = int((time.perf_counter() - started) * 1000)
        if payload.get("error"):
            raise LlmError(scrub(f"OpenRouter {self.model}: {payload['error']}", (self._api_key,)))
        choices = payload.get("choices") or []
        text = ((choices[0].get("message") or {}).get("content")) if choices else None
        if not isinstance(text, str) or not text.strip():
            raise LlmError(f"OpenRouter {self.model}: the answer is empty")
        usage = payload.get("usage") or {}
        cost = usage.get("cost")
        return LlmResult(
            text=text,
            latency_ms=latency,
            prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
            completion_tokens=_int_or_none(usage.get("completion_tokens")),
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )


class MockClient:
    """Deterministic stand-in: answers from a function of the prompt. Never calls a network."""

    def __init__(
        self, respond: Callable[[str, str], str], model: str = "mock", tier: str = "cheap"
    ):
        self._respond = respond
        self.model = model
        self.tier = tier
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, json_schema: dict | None = None) -> LlmResult:
        self.calls.append((system, user))
        text = self._respond(system, user)
        return LlmResult(text, latency_ms=0, prompt_tokens=0, completion_tokens=0, cost_usd=0.0)


def build_cheap_client(settings: Settings, client: httpx.Client) -> LlmClient:
    """The condenser's model. OPENROUTER_MODEL_CHEAP wins when set; otherwise OLLAMA_MODEL.

    Which one is better is decided by eval_models.py in Phase 4; until then it is whichever the
    owner configured.
    """
    if settings.openrouter_model_cheap:
        return OpenRouterClient(
            client, settings.openrouter_api_key, settings.openrouter_model_cheap, "cheap"
        )
    if settings.ollama_model:
        return OllamaClient(client, settings.ollama_base_url, settings.ollama_model, "cheap")
    raise LlmError("No cheap-tier model is configured: set OLLAMA_MODEL or OPENROUTER_MODEL_CHEAP")
