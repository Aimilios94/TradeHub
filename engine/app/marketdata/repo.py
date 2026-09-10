"""Supabase persistence for the market data layer (service-role, server-side only)."""

from __future__ import annotations

import datetime as dt
import logging

from app.db import get_client
from app.marketdata.alpaca import now_et

log = logging.getLogger(__name__)

BASELINE_TABLE = "premarket_volume_baseline"
CALIBRATION_TABLE = "feed_calibration"


def upsert_baselines(rows: list[dict]) -> int:
    if not rows:
        return 0
    get_client().table(BASELINE_TABLE).upsert(rows, on_conflict="symbol,trade_date,feed").execute()
    return len(rows)


def upsert_calibrations(rows: list[dict]) -> int:
    if not rows:
        return 0
    get_client().table(CALIBRATION_TABLE).upsert(rows, on_conflict="symbol,trade_date,session_window").execute()
    return len(rows)


def fetch_baseline_rows(symbol: str, feed: str, *, before: dt.date | None = None, limit: int = 30) -> list[dict]:
    q = get_client().table(BASELINE_TABLE).select("*").eq("symbol", symbol).eq("feed", feed)
    if before is not None:
        q = q.lt("trade_date", before.isoformat())
    return q.order("trade_date", desc=True).limit(limit).execute().data or []


def fetch_setting(key: str, default=None):
    try:
        row = get_client().table("app_settings").select("value").eq("key", key).single().execute()
        return row.data["value"]
    except Exception as e:  # noqa: BLE001 — a missing setting falls back to the default; DB errors are logged
        log.warning("app_settings[%s] unavailable (%s); using default", key, type(e).__name__)
        return default


def baseline_universe(scan_days: int = 30) -> list[str]:
    """Symbols the nightly job maintains: default watchlist ∪ `baseline_universe` setting ∪ recent scan symbols."""
    syms: set[str] = set()
    for key in ("watchlist_default", "baseline_universe"):
        v = fetch_setting(key, [])
        if isinstance(v, list):
            syms.update(str(x).upper() for x in v)
    try:
        since = (now_et().date() - dt.timedelta(days=scan_days)).isoformat()
        res = get_client().table("scans").select("symbol").gte("run_at", since).execute()
        syms.update(str(r["symbol"]).upper() for r in (res.data or []))
    except Exception as e:  # noqa: BLE001 — scans table is empty until Phase 2; never block the job on it
        log.warning("scans lookup failed (%s); universe without scan symbols", type(e).__name__)
    return sorted(syms)


def calibration_rows(since: dt.date) -> list[dict]:
    res = (get_client().table(CALIBRATION_TABLE).select("*").gte("trade_date", since.isoformat())
           .order("trade_date", desc=True).limit(5000).execute())
    return res.data or []
