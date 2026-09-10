"""Prompt template loading + rendering.

At BUILD time `scripts/gen_seed.py` parses `prompt-library/hermes-core/*.md` into `0002_seed.sql`.
At RUN time the DB row is the source of truth, so editing a template means re-seeding, not redeploying.
`load()` falls back to parsing the markdown when the DB is unreachable, so tests and the offline
diagnostics path keep working (the fallback is reported in `PromptSpec.origin`, never hidden).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_HEADER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_BLOCK_RE = re.compile(r"^## (?P<title>[^\n]+)\n+```(?P<lang>\w*)\n(?P<body>.*?)\n```", re.DOTALL | re.MULTILINE)


@dataclass(slots=True)
class PromptSpec:
    slug: str
    name: str
    tier: str
    model: str
    version: int
    system_prompt: str
    user_template: str
    output_schema: dict
    origin: str = "db"          # "db" | "markdown" — which copy answered

    def variables(self) -> set[str]:
        return set(_VAR_RE.findall(self.user_template))


def render(template: str, variables: dict[str, Any]) -> str:
    """Substitute {{var}}. Every placeholder must be supplied — a missing one is a bug, not a blank."""
    missing = set(_VAR_RE.findall(template)) - set(variables)
    if missing:
        raise KeyError(f"prompt variables not supplied: {sorted(missing)}")
    return _VAR_RE.sub(lambda m: str(variables[m.group(1)]), template)


def _from_markdown(slug: str) -> PromptSpec:
    root = get_settings().prompt_library_path
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        head = _HEADER_RE.match(text)
        if not head or not re.search(rf"^slug:\s*{re.escape(slug)}\s*$", head.group(1), re.MULTILINE):
            continue
        meta = dict(re.findall(r"^(\w+):\s*(.+)$", head.group(1), re.MULTILINE))
        blocks = {m.group("title").strip().lower(): m.group("body") for m in _BLOCK_RE.finditer(text)}
        return PromptSpec(
            slug=slug, name=meta.get("name", slug), tier=meta.get("tier", "local"),
            model=meta.get("model", ""), version=int(meta.get("version", 1)),
            system_prompt=blocks["system prompt"], user_template=blocks["user prompt template"],
            output_schema=json.loads(blocks["output schema"]), origin="markdown",
        )
    raise KeyError(f"prompt template '{slug}' not found in {root}")


@lru_cache(maxsize=32)
def load(slug: str) -> PromptSpec:
    """The seeded DB row, falling back to the markdown source when Supabase is unreachable."""
    try:
        from app.db import get_client

        rows = (get_client().table("prompt_templates").select("*").eq("slug", slug).eq("active", True)
                .execute().data or [])
        if rows:
            r = rows[0]
            return PromptSpec(slug=r["slug"], name=r["name"], tier=r["tier"], model=r["model"],
                              version=r["version"], system_prompt=r["system_prompt"],
                              user_template=r["user_template"], output_schema=r["output_schema"], origin="db")
    except Exception as e:  # noqa: BLE001 — offline / not seeded yet; the markdown holds the same content
        log.info("prompt '%s': DB lookup failed (%s: %s), falling back to the markdown source",
                 slug, type(e).__name__, str(e)[:120])
    return _from_markdown(slug)
