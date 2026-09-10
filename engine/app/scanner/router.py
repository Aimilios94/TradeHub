"""`/scan/*` — Phase 2 scanner endpoints (n8n 08:00 / 11:30 ET crons + owner diagnostics). Bearer token required."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.db import get_client
from app.marketdata import alpaca
from app.scanner import run as scan_run
from app.scanner import universe as universe_job

router = APIRouter(prefix="/scan", tags=["scanner"], dependencies=[Depends(require_token)])


def _statuses(csv: str | None) -> tuple[str, ...]:
    if not csv:
        return scan_run.SCANNED_STATUSES
    s = tuple(x.strip() for x in csv.split(",") if x.strip())
    bad = [x for x in s if x not in ("draft", "backtesting", "paper", "live", "retired")]
    if bad:
        raise HTTPException(422, f"unknown status {bad}")
    return s


@router.post("/run")
def run(kind: str = Query("morning", pattern="^(morning|midday)$"), statuses: str | None = None,
        strategies: str | None = Query(None, description="comma-separated codes, e.g. S1,S3"),
        as_of: str | None = Query(None, pattern=r"^\d{2}:\d{2}$", description="ET time to simulate (diagnostics)"),
        date: dt.date | None = None, notify: bool = True, dry_run: bool = False) -> dict:
    """Morning: screener + detectors + conditions → `scans`. Midday: ADV/ATR exceedance re-scan."""
    now = None
    if as_of or date:
        base = alpaca.now_et()
        d = date or base.date()
        hh, mm = (as_of or base.strftime("%H:%M")).split(":")
        now = alpaca.et_datetime(d, int(hh), int(mm))
    codes = [c.strip().upper() for c in strategies.split(",")] if strategies else None
    st = _statuses(statuses)
    try:
        if kind == "morning":
            res = scan_run.run_morning(now=now, statuses=st, strategy_codes=codes, notify=notify, dry_run=dry_run)
        else:
            res = scan_run.run_midday(now=now, statuses=st, notify=notify, dry_run=dry_run)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e
    return res.as_dict()


@router.post("/universe/run")
def universe_run(lookback_days: int = Query(90, ge=30, le=400), symbols: str | None = None, dry_run: bool = False) -> dict:
    """Nightly: refresh `symbol_stats` for every tradable listed US equity (~3 min)."""
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    try:
        return universe_job.run_universe(lookback_days=lookback_days, symbols=syms, dry_run=dry_run).as_dict()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/universe/{symbol}")
def universe_symbol(symbol: str) -> dict:
    rows = get_client().table("symbol_stats").select("*").eq("symbol", symbol.upper()).execute().data or []
    if not rows:
        raise HTTPException(404, f"{symbol.upper()} not in symbol_stats (not listed/tradable, or nightly job not run)")
    return rows[0]


@router.get("/latest")
def latest(kind: str = Query("morning", pattern="^(morning|midday)$"), date: dt.date | None = None,
           passed_only: bool = True, limit: int = Query(100, le=1000)) -> dict:
    d = (date or alpaca.now_et().date()).isoformat()
    q = (get_client().table("scans").select("strategy_id,symbol,rank,passed,reject_reason,metrics,run_at")
         .eq("scan_date", d).eq("run_kind", kind))
    if passed_only:
        q = q.eq("passed", True)
    rows = q.order("rank", desc=False).limit(limit).execute().data or []
    strategies = {r["id"]: r["code"] for r in (get_client().table("strategy_configs").select("id,code").execute().data or [])}
    for r in rows:
        r["strategy"] = strategies.get(r["strategy_id"])
    return {"scan_date": d, "kind": kind, "count": len(rows), "rows": rows}
