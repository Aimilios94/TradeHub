"""OpenRouter client — hosted tier (Hermes 3 70B / 405B), for judgment work.

Same contract as `app/llm/ollama.py` so `complete_json` can route on the template's `tier` without
either side knowing about the other: strict JSON out, transport failures raise `LLMUnavailable`,
unparseable output raises `LLMInvalidJSON`, and nothing is ever fabricated on failure.

Cost is deliberately visible: `chat_json_with_usage` returns the token counts OpenRouter reports so the
caller can log what a morning actually cost. Hermes 3 70B is ~$0.70/M tokens (verified 2026-09-10), so a
full morning is roughly a cent — but a runaway loop should still be legible in the logs.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings
from app.llm.errors import LLMInvalidJSON, LLMUnavailable

log = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1"
# Optional attribution headers OpenRouter uses for its dashboard; harmless and helps identify our traffic.
APP_HEADERS = {"HTTP-Referer": "https://github.com/Aimilios94/TradeHub", "X-Title": "TradeHub"}


def available() -> tuple[bool, str]:
    """Readiness probe: is the key valid, and is there credit left? Cheap, no model call."""
    s = get_settings()
    if not s.openrouter_api_key:
        return False, "OPENROUTER_API_KEY not set"
    try:
        r = httpx.get(f"{BASE_URL}/key", headers={"Authorization": f"Bearer {s.openrouter_api_key}"}, timeout=15)
        r.raise_for_status()
        d = r.json()["data"]
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:140]}"
    limit, used = d.get("limit"), d.get("usage")
    if limit is not None and used is not None and used >= limit:
        return False, f"credit exhausted: used {used} of {limit}"
    remaining = "unlimited" if limit is None else f"{limit - used:.2f} of {limit} left"
    return True, f"key ok, {remaining}"


def chat_json_with_usage(system: str, user: str, *, model: str | None = None,
                         timeout: float = 180.0) -> tuple[dict, dict]:
    """One strict-JSON chat turn. Returns (payload, usage). Temperature 0, JSON response format."""
    s = get_settings()
    if not s.openrouter_api_key:
        raise LLMUnavailable("OPENROUTER_API_KEY not set — the hosted tier cannot run")
    use_model = model or s.openrouter_model_judgment
    body = {
        "model": use_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {s.openrouter_api_key}", "Content-Type": "application/json", **APP_HEADERS}
    try:
        r = httpx.post(f"{BASE_URL}/chat/completions", json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300]
        if e.response.status_code in (401, 403):
            raise LLMUnavailable(f"OpenRouter rejected the key (HTTP {e.response.status_code}): {detail}") from e
        if e.response.status_code == 402:
            raise LLMUnavailable(f"OpenRouter credit exhausted: {detail}") from e
        if e.response.status_code == 429:
            raise LLMUnavailable(f"OpenRouter rate limited: {detail}") from e
        raise LLMUnavailable(f"OpenRouter HTTP {e.response.status_code}: {detail}") from e
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"{type(e).__name__}: {str(e)[:200]}") from e

    # OpenRouter reports upstream provider errors inside a 200 body
    if "error" in data and not data.get("choices"):
        raise LLMUnavailable(f"OpenRouter error: {str(data['error'])[:300]}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMInvalidJSON(f"unexpected response shape: {str(data)[:250]}") from e
    if not (content or "").strip():
        raise LLMInvalidJSON("OpenRouter returned an empty message")

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMInvalidJSON(f"not JSON: {str(e)[:120]} — got: {content[:200]}") from e
    if not isinstance(payload, dict):
        raise LLMInvalidJSON(f"expected a JSON object, got {type(payload).__name__}")

    u = data.get("usage") or {}
    usage = {"model": data.get("model", use_model), "prompt_tokens": u.get("prompt_tokens"),
             "completion_tokens": u.get("completion_tokens"), "total_tokens": u.get("total_tokens"),
             "generation_id": data.get("id")}
    return payload, usage


def chat_json(system: str, user: str, *, model: str | None = None, timeout: float = 180.0) -> dict:
    """Ollama-compatible signature so `complete_json` can treat both providers identically."""
    payload, usage = chat_json_with_usage(system, user, model=model, timeout=timeout)
    log.info("openrouter %s: %s tokens", usage["model"], usage.get("total_tokens"))
    return payload
