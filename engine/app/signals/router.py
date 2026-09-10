"""`/signals/*` — Phase 4 decision-node endpoints. Bearer token required.

Called by n8n workflow 02 after the morning scan and enrichment, and by the owner for diagnostics.
Everything here is read or propose. No endpoint in this router can place, modify or cancel an order.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.db import get_client
from app.llm import openrouter
from app.marketdata import alpaca
from app.signals import run as signals_run

router = APIRouter(prefix="/signals", tags=["decision node"], dependencies=[Depends(require_token)])


@router.post("/plan")
def plan(date: dt.date | None = None,
         strategies: str | None = Query(None, description="comma-separated codes, e.g. S1,S3"),
         cap: int | None = Query(None, ge=1, le=25, description="candidates per strategy (default 5)"),
         skip_gate: bool = Query(False, description="skip the template-04 in-play gate (diagnostics)"),
         briefing: bool = True, notify: bool = True, dry_run: bool = False) -> dict:
    """In-play gate → trade plans → `signals`, plus the S2 watchlist briefing → `briefings`.

    Proposals only. Nothing is executed; approval and execution are Phase 5.
    """
    codes = [c.strip().upper() for c in strategies.split(",") if c.strip()] if strategies else None
    try:
        res = signals_run.run_signals(date=date, strategy_codes=codes, cap=cap, skip_gate=skip_gate,
                                      briefing=briefing, notify=notify, dry_run=dry_run)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e
    return res.as_dict()


@router.get("/status")
def status() -> dict:
    """Is the hosted tier reachable and funded? Cheap pre-flight before spending a morning's calls."""
    ready, detail = openrouter.available()
    cfg = signals_run.config()
    return {"openrouter": {"ready": ready, "detail": detail}, "config": cfg,
            "note": "signals are proposals; execution arrives in Phase 5"}


@router.get("/latest")
def latest(date: dt.date | None = None, status_filter: str | None = Query(None, alias="status"),
           limit: int = Query(100, le=500)) -> dict:
    d = (date or alpaca.now_et().date()).isoformat()
    q = get_client().table("signals").select("*").eq("signal_date", d)
    if status_filter:
        q = q.eq("status", status_filter)
    rows = q.order("confidence", desc=True).limit(limit).execute().data or []
    codes = {r["id"]: r["code"] for r in
             (get_client().table("strategy_configs").select("id,code").execute().data or [])}
    for r in rows:
        r["strategy"] = codes.get(r["strategy_id"])
    return {"signal_date": d, "count": len(rows), "rows": rows}


@router.get("/briefing")
def briefing_latest(date: dt.date | None = None, kind: str = "watchlist") -> dict:
    d = (date or alpaca.now_et().date()).isoformat()
    rows = (get_client().table("briefings").select("*")
            .eq("briefing_date", d).eq("kind", kind).execute().data or [])
    if not rows:
        raise HTTPException(404, f"no {kind} briefing for {d}")
    return rows[0]
