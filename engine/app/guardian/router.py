"""`/guardian/*` — Phase 6.5 endpoints. Bearer token required.

Layer 3 (the plain-language briefing) and the monthly rules-freshness check. Read-only: nothing here
places an order, changes a limit or edits the rules file.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.guardian import briefing as briefing_mod
from app.guardian import gate as gate_mod
from app.guardian import rules as rules_mod

router = APIRouter(prefix="/guardian", tags=["guardian"], dependencies=[Depends(require_token)])


@router.post("/briefing")
def briefing(question: str = Query("", description="an optional question to answer alongside the report"),
             notify: bool = True, store: bool = True) -> dict:
    """Layer 3: the account's state and risks in plain language. A monitor, never an advisor."""
    try:
        return briefing_mod.run_briefing(question=question, notify=notify, store=store)
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/state")
def state() -> dict:
    """The same figures the briefing is built from, without spending a model call."""
    return briefing_mod.gather()


@router.post("/rules-check")
def rules_check(notify: bool = True,
                rebaseline: bool = Query(False, description="accept current pages as the new baseline "
                                                            "after you have read and applied a change")) -> dict:
    """Monthly: have the broker's rule pages moved? Flags for a human; decides nothing."""
    try:
        return rules_mod.run_check(notify=notify, rebaseline=rebaseline).as_dict()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/rules")
def rules() -> dict:
    """Rule-file freshness, what is watched, and anything the last check flagged."""
    return rules_mod.rules_summary()


@router.post("/gate/check")
def gate_check(symbol: str = Query(...), direction: str = Query("long", pattern="^(long|short)$"),
               qty: int = Query(..., ge=1), entry: float = Query(...), stop: float = Query(...),
               strategy: str | None = None) -> dict:
    """Run Layer 1 against a hypothetical order. Submits nothing — for checking a manual idea."""
    from app.db import get_client

    risk = {}
    if strategy:
        rows = (get_client().table("strategy_configs").select("risk")
                .eq("code", strategy.upper()).execute().data or [])
        risk = (rows[0].get("risk") or {}) if rows else {}
    res = gate_mod.evaluate(symbol=symbol.upper(), direction=direction, qty=qty, entry=entry,
                            stop=stop, risk=risk)
    return {"symbol": symbol.upper(), "direction": direction, "qty": qty} | res.as_dict()
