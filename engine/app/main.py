"""TradeHub engine — FastAPI entry point.

Thin-n8n / fat-Python architecture (decision 2026-09-08): n8n only schedules, alerts and
receives webhooks; every pipeline step is an endpoint here. Phase 0 ships the skeleton:
health, plugin registry, seed strategies. Later phases add routers under app/<module>/router.py.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.config import get_settings
from app.registry import ALL_PLUGINS, DETECTORS, EXITS
from app.seed import frameworks as seed_frameworks
from app.seed import settings as seed_settings
from app.seed import strategies as seed_strategies

app = FastAPI(
    title="TradeHub engine",
    version=__version__,
    description="Deterministic core: rules in code, judgment in Hermes. See tradehub_implementation_plan.md.",
)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "env": s.tradehub_env,
        "paper_mode": s.paper_mode,
        "tz": s.tradehub_tz,
        "supabase_configured": s.supabase_configured,
        "alpaca_configured": s.alpaca_configured,
        "data_feed": s.alpaca_data_feed,
    }


@app.get("/plugins")
def plugins() -> dict:
    return {
        "detectors": [{"number": p["number"], "name": p["name"], "status": p["status"]} for p in DETECTORS],
        "exits": [{"number": p["number"], "name": p["name"], "status": p["status"]} for p in EXITS],
        "total": len(ALL_PLUGINS),
    }


@app.get("/plugins/{name}")
def plugin_detail(name: str) -> dict:
    from fastapi import HTTPException

    for p in ALL_PLUGINS:
        if p["name"] == name:
            return dict(p)
    raise HTTPException(status_code=404, detail=f"unknown plugin '{name}'")


@app.get("/seed/strategies")
def strategies() -> list[dict]:
    """The eight seed strategies as validated config objects (the same data that generates the SQL seed)."""
    return [s.model_dump(mode="json") for s in seed_strategies.all_strategies()]


@app.get("/seed/frameworks")
def frameworks() -> list[dict]:
    return [f.model_dump(mode="json") for f in seed_frameworks.all_frameworks()]


@app.get("/seed/settings")
def settings() -> dict:
    return seed_settings.APP_SETTINGS
