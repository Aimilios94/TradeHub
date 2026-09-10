"""`/journal/*`, `/analytics/*` and `/review/*` — Phase 6 endpoints. Bearer token required.

Read and record only. Nothing here can place an order or change a strategy: the weekly review writes
`hypotheses` rows, which are proposals awaiting a backtest and the owner's approval.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import stats as analytics
from app.auth import require_token
from app.db import get_client
from app.journal import eod as eod_mod
from app.journal import ledger as ledger_mod
from app.marketdata import alpaca
from app.review import autopsy as autopsy_mod
from app.review import weekly as weekly_mod

router = APIRouter(tags=["journal & review"], dependencies=[Depends(require_token)])


@router.post("/journal/eod")
def journal_eod(date: dt.date | None = None, notify: bool = True, dry_run: bool = False) -> dict:
    """16:30 ET: the day's closed round trips → `journal`, their costs → `guardian_ledger`."""
    try:
        return eod_mod.run_eod(date=date, notify=notify, dry_run=dry_run).as_dict()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/journal")
def list_journal(since: dt.date | None = None, until: dt.date | None = None,
                 limit: int = Query(100, le=500)) -> dict:
    q = get_client().table("journal").select("*").order("entry_time", desc=True).limit(limit)
    if since:
        q = q.gte("entry_time", f"{since.isoformat()}T00:00:00")
    if until:
        q = q.lte("entry_time", f"{until.isoformat()}T23:59:59")
    rows = q.execute().data or []
    codes = analytics.strategy_codes()
    for r in rows:
        r["strategy"] = codes.get(r.get("strategy_id"))
    return {"count": len(rows), "rows": rows}


@router.patch("/journal/{journal_id}/note")
def set_note(journal_id: str, note: str = Query(..., description="your own words about the trade")) -> dict:
    """The subjective note — the one input only you can give. It feeds tilt detection in the autopsy."""
    get_client().table("journal").update({"subjective_note": note[:4000]}).eq("id", journal_id).execute()
    return {"journal_id": journal_id, "subjective_note": note[:4000]}


@router.get("/analytics/report")
def analytics_report(since: dt.date | None = None, until: dt.date | None = None,
                     days: int = Query(90, ge=1, le=1095)) -> dict:
    """The full §14 block: overall, by strategy / time block / weekday / setup / symbol, plus flags."""
    if since is None:
        since = alpaca.now_et().date() - dt.timedelta(days=days)
    rep = analytics.report(since=since, until=until)
    return rep | {"written_summary": analytics.written_summary(rep)}


@router.get("/analytics/ledger")
def analytics_ledger(days: int = Query(30, ge=1, le=1095)) -> dict:
    """Guardian Layer 2: realized P&L, fees, spread cost and the display-only tax provision."""
    return ledger_mod.summary(days)


@router.post("/review/autopsy")
def review_autopsy(since: dt.date | None = None, until: dt.date | None = None,
                   limit: int = Query(25, ge=1, le=100), redo: bool = False) -> dict:
    """Template 05, batched: one coaching review per closed trade → `journal.autopsy`."""
    today = alpaca.now_et().date()
    return autopsy_mod.run_batch(since=since or today - dt.timedelta(days=7), until=until or today,
                                 limit=limit, redo=redo)


@router.post("/review/weekly")
def review_weekly(week_start: dt.date | None = None, week_end: dt.date | None = None,
                  run_autopsies: bool = True, notify: bool = True, dry_run: bool = False) -> dict:
    """Sunday: autopsies → analytics → lessons + `hypotheses`. Proposals only; nothing is applied."""
    try:
        return weekly_mod.run_weekly(week_start=week_start, week_end=week_end,
                                     run_autopsies=run_autopsies, notify=notify,
                                     dry_run=dry_run).as_dict()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/review/hypotheses")
def list_hypotheses(status: str | None = None, limit: int = Query(100, le=500)) -> dict:
    q = get_client().table("hypotheses").select("*").order("created_at", desc=True).limit(limit)
    if status:
        q = q.eq("status", status)
    rows = q.execute().data or []
    codes = analytics.strategy_codes()
    for r in rows:
        r["strategy"] = codes.get(r.get("strategy_id"))
    return {"count": len(rows), "rows": rows}


@router.post("/review/hypotheses/{hypothesis_id}/decide")
def decide_hypothesis(hypothesis_id: str,
                      decision: str = Query(..., pattern="^(rejected|validated|promoted)$"),
                      note: str = "") -> dict:
    """Your call on a proposal. A rejection is kept forever so the same idea cannot come back unchanged.

    `promoted` records the decision only — actually applying a config diff waits for the Phase 7
    backtest that grades it, so nothing here edits a strategy.
    """
    patch = {"status": decision, "decision_note": note[:2000],
             "decided_at": dt.datetime.now(dt.UTC).isoformat()}
    if decision == "promoted":
        patch["promoted_at"] = patch["decided_at"]
    get_client().table("hypotheses").update(patch).eq("id", hypothesis_id).execute()
    return {"hypothesis_id": hypothesis_id} | patch
