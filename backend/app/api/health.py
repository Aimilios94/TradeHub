"""Health checks. Each dependency reports its real state; nothing is assumed to be up."""

import httpx
from fastapi import APIRouter, Response
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine

router = APIRouter(tags=["health"])


def _check_database() -> dict:
    try:
        with get_engine().connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        return {"ok": True, "schema_revision": revision}
    except Exception as exc:  # report the real error, never hide it
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


def _check_http(url: str) -> dict:
    try:
        response = httpx.get(url, timeout=5)
        response.raise_for_status()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


@router.get("/health")
def health(response: Response) -> dict:
    """Liveness plus the database, which the API cannot work without."""
    database = _check_database()
    if not database["ok"]:
        response.status_code = 503
    return {"ok": database["ok"], "database": database}


@router.get("/health/deps")
def health_deps() -> dict:
    """State of every service the stack depends on."""
    settings = get_settings()
    return {
        "database": _check_database(),
        "searxng": _check_http(f"{settings.searxng_base_url}/healthz"),
        "ollama": _check_http(f"{settings.ollama_base_url}/api/version"),
    }
