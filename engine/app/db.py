"""Supabase access for the engine (service-role key, server-side only).

The engine is the only writer besides n8n. Reads for the frontend go through the
Edge Function proxy / supabase-js, never through here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:  # pragma: no cover
    from supabase import Client


@lru_cache
def get_client() -> "Client":
    s = get_settings()
    if not s.supabase_configured:
        raise RuntimeError("Supabase is not configured: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
    from supabase import create_client

    return create_client(s.supabase_url, s.supabase_service_role_key)


def kill_switch_engaged() -> bool:
    """Plan §11/§12: every execution path checks this first. Fails SAFE (True) if the DB is unreachable."""
    try:
        row = get_client().table("app_settings").select("value").eq("key", "kill_switch").single().execute()
        return bool(row.data["value"])
    except Exception:
        return True
