"""Ollama client — local tier (Hermes 3 8B on the owner's RTX 2070).

Runs natively on the Windows host for GPU access while the engine runs in Docker, so the base URL is
`http://host.docker.internal:11434` in .env. That hostname does not resolve when the engine is run
directly on the host (pytest, scripts), so `base_url()` rewrites it — the same rewrite
`scripts/check_env.py` uses, kept here as the single implementation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.config import get_settings
from app.llm.errors import LLMInvalidJSON, LLMUnavailable
from app.ops import resilience

log = logging.getLogger(__name__)

DOCKER_HOST_ALIAS = "host.docker.internal"


def _in_container() -> bool:
    """`host.docker.internal` resolves on a Docker Desktop HOST too, but points at an address where a
    localhost-bound Ollama is not listening — so resolution is not the test. Being inside a container is."""
    if Path("/.dockerenv").exists():
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text()
    except OSError:
        return False


def base_url() -> str:
    """The configured URL, with the Docker-only host alias swapped for localhost when we are on the host."""
    url = get_settings().ollama_base_url.rstrip("/")
    if DOCKER_HOST_ALIAS in url and not _in_container():
        url = url.replace(DOCKER_HOST_ALIAS, "localhost")
    return url


def available() -> tuple[bool, str]:
    """Cheap readiness probe used by the enrich job before it burns a scan's worth of fetches."""
    try:
        r = httpx.get(f"{base_url()}/api/tags", timeout=10)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}"
    names = [m.get("name", "") for m in r.json().get("models", [])]
    want = get_settings().ollama_model
    if not any(want in n for n in names):
        return False, f"model '{want}' not pulled (have: {', '.join(names) or 'none'}) — run: ollama pull {want}"
    return True, f"{want} ready"


def chat_json(system: str, user: str, *, model: str | None = None, timeout: float = 120.0) -> dict:
    """One strict-JSON chat turn. `format: json` + temperature 0 (template 01's Notes).

    Raises LLMUnavailable on transport failure, LLMInvalidJSON when the reply is not a JSON object.
    """
    s = get_settings()
    body = {
        "model": model or s.ollama_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }
    def _post():
        r = httpx.post(f"{base_url()}/api/chat", json=body, timeout=timeout)
        r.raise_for_status()
        return r

    try:
        r = resilience.call_preserving_error("ollama", _post, what=f"chat_json({body['model']})")
    except httpx.ConnectError as e:
        raise LLMUnavailable(
            f"Ollama unreachable at {base_url()} — is it running? (ollama serve; ollama pull {body['model']})"
        ) from e
    except httpx.HTTPStatusError as e:
        raise LLMUnavailable(f"Ollama HTTP {e.response.status_code}: {e.response.text[:200]}") from e
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"{type(e).__name__}: {str(e)[:200]}") from e

    content = (r.json().get("message") or {}).get("content", "")
    if not content.strip():
        raise LLMInvalidJSON("Ollama returned an empty message")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMInvalidJSON(f"not JSON: {str(e)[:120]} — got: {content[:200]}") from e
    if not isinstance(payload, dict):
        raise LLMInvalidJSON(f"expected a JSON object, got {type(payload).__name__}")
    return payload
