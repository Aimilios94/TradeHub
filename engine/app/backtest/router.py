"""`/backtest/*` — Phase 7 endpoints. Bearer token required.

Nothing here places an order or changes a strategy. `/backtest/promote` is the one endpoint that CAN
flip a status, and it refuses to do so without a `backtests` row whose verdict is `pass` — a promotion
is a status flip backed by evidence (plan §13, §20), never a request.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.backtest import bars as bar_store
from app.backtest import hypotheses as hyp_mod
from app.backtest import run as run_mod
from app.db import get_client
from app.marketdata import alpaca

router = APIRouter(prefix="/backtest", tags=["backtest"], dependencies=[Depends(require_token)])


def _range(date_from: dt.date | None, date_to: dt.date | None, days: int) -> tuple[dt.date, dt.date]:
    to = date_to or (alpaca.now_et().date() - dt.timedelta(days=1))
    return (date_from or (to - dt.timedelta(days=days))), to


@router.post("/run")
def run(strategy: str, date_from: dt.date | None = None, date_to: dt.date | None = None,
        days: int = Query(365, ge=5, le=3650), equity: float = run_mod.DEFAULT_EQUITY,
        universe_cap: int = Query(1500, ge=10, le=15_000), include_inactive: bool = True,
        use_cache: bool = True, persist: bool = True, notify: bool = False,
        trades: bool = Query(False, description="include every trade in the response, not just the metrics"),
        ) -> dict:
    """Replay one strategy over a date range. `strategy` is a code (S1…S8)."""
    lo, hi = _range(date_from, date_to, days)
    try:
        res = run_mod.run_backtest(strategy.upper(), lo, hi, equity=equity, universe_cap=universe_cap,
                                   include_inactive=include_inactive, use_cache=use_cache,
                                   persist=persist, notify=notify)
    except KeyError as e:
        raise HTTPException(404, f"unknown strategy {e}") from e
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e
    out = res.as_dict()
    if not trades:
        out["trades"] = out["trades"][:20]
        out["trades_note"] = f"{res.metrics.get('trades', 0)} trades; pass trades=true for all of them"
    return out


@router.post("/run-all")
def run_all(date_from: dt.date | None = None, date_to: dt.date | None = None,
            days: int = Query(365, ge=5, le=3650), codes: str | None = None,
            equity: float = run_mod.DEFAULT_EQUITY, universe_cap: int = Query(1500, ge=10, le=15_000),
            persist: bool = True) -> dict:
    """Baselines for every strategy (plan §17 acceptance criterion 1)."""
    lo, hi = _range(date_from, date_to, days)
    wanted = [c.strip().upper() for c in codes.split(",")] if codes else None
    return run_mod.run_all(lo, hi, wanted, equity=equity, universe_cap=universe_cap, persist=persist)


@router.post("/ab")
def ab(a: str, b: str, date_from: dt.date | None = None, date_to: dt.date | None = None,
       days: int = Query(365, ge=5, le=3650), equity: float = run_mod.DEFAULT_EQUITY,
       universe_cap: int = Query(1500, ge=10, le=15_000), persist: bool = True) -> dict:
    """Head-to-head over one range and one fill model (plan §13: S6 canonical vs S7 variant)."""
    lo, hi = _range(date_from, date_to, days)
    out = run_mod.run_ab(a.upper(), b.upper(), lo, hi, equity=equity, universe_cap=universe_cap,
                         persist=persist)
    for k in (a.upper(), b.upper()):
        out[k]["trades"] = out[k]["trades"][:20]
    return out


@router.post("/hypothesis/{hypothesis_id}")
def grade_hypothesis(hypothesis_id: str, date_from: dt.date | None = None, date_to: dt.date | None = None,
                     days: int = Query(365, ge=5, le=3650), equity: float = run_mod.DEFAULT_EQUITY,
                     universe_cap: int = Query(1500, ge=10, le=15_000)) -> dict:
    """Grade one `hypotheses` row: baseline config vs proposed config, same range, same fill model.

    Writes the verdict back onto the hypothesis (`validated` | `rejected`) and links the backtest.
    A validated hypothesis is still only a proposal — applying it needs the owner's approval."""
    lo, hi = _range(date_from, date_to, days)
    try:
        return hyp_mod.grade(hypothesis_id, lo, hi, equity=equity, universe_cap=universe_cap)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.post("/queue/run")
def run_queue(limit: int = Query(5, ge=1, le=20), days: int = Query(365, ge=5, le=3650),
              notify: bool = True) -> dict:
    """Grade every `proposed` hypothesis that has a config diff (plan §20: a hypothesis becomes a
    backtest job, never a live change). Driven by n8n, weekly."""
    return hyp_mod.run_queue(limit=limit, days=days, notify=notify)


@router.get("/list")
def list_backtests(strategy: str | None = None, limit: int = Query(50, le=200)) -> dict:
    q = get_client().table("backtests").select(
        "id,strategy_id,date_from,date_to,verdict,status,created_at,hypothesis_id,"
        "ab_against_backtest_id,coverage_stats,calibration").order("created_at", desc=True).limit(limit)
    rows = q.execute().data or []
    if strategy:
        codes = {r["id"]: r["code"] for r in
                 (get_client().table("strategy_configs").select("id,code").execute().data or [])}
        rows = [r for r in rows if codes.get(r["strategy_id"]) == strategy.upper()]
    return {"backtests": rows, "count": len(rows)}


@router.post("/promote")
def promote(strategy: str, backtest_id: str,
            to_status: str = Query(..., pattern="^(backtesting|paper|live)$"),
            note: str | None = None) -> dict:
    """Flip a strategy's status, but only on evidence (plan §13, §20).

    Refused when: the backtest's verdict is not `pass`; it does not belong to this strategy; or the
    target is `live` — going live is Phase 8's paper month plus a separate, deliberate change, not an
    API call (docs/DECISIONS.md #37)."""
    if to_status == "live":
        raise HTTPException(403, "live promotion is refused here. Live trading is blocked in code until "
                                 "Phase 8's paper month, and lifting it is its own owner-approved change.")
    bt = (get_client().table("backtests").select("*").eq("id", backtest_id).execute().data or [])
    if not bt:
        raise HTTPException(404, "no such backtest")
    bt = bt[0]
    rows = (get_client().table("strategy_configs").select("id,code,status")
            .eq("code", strategy.upper()).execute().data or [])
    if not rows:
        raise HTTPException(404, f"unknown strategy {strategy}")
    sc = rows[0]
    if bt["strategy_id"] != sc["id"]:
        raise HTTPException(409, "that backtest belongs to a different strategy")
    if bt.get("verdict") != "pass":
        raise HTTPException(409, f"the backtest verdict is '{bt.get('verdict')}', not 'pass' — "
                                 f"unmet: {(bt.get('verdict_detail') or {}).get('failed') or []}, "
                                 f"unanswered: {(bt.get('verdict_detail') or {}).get('unknown') or []}")
    get_client().table("strategy_configs").update({"status": to_status}).eq("id", sc["id"]).execute()
    get_client().table("compliance_events").insert({
        "event_time": dt.datetime.now(dt.UTC).isoformat(), "kind": "strategy_promoted",
        "severity": "info", "symbol": None,
        "detail": {"strategy": sc["code"], "from": sc["status"], "to": to_status,
                   "backtest_id": backtest_id, "note": note},
    }).execute()
    return {"strategy": sc["code"], "from": sc["status"], "to": to_status, "backtest_id": backtest_id,
            "note": "paper promotion recorded. Live requires at least four weeks of paper operation "
                    "(plan §20) and a separate change to lift the in-code live lockout."}


@router.get("/data/cache")
def cache() -> dict:
    """What the parquet bar store holds. Deep history lives on disk, not in Supabase (plan §5)."""
    return bar_store.cache_stats()


@router.post("/data/warm")
def warm(symbols: str, date_from: dt.date, date_to: dt.date, timeframe: str = "1Day",
         feed: str = "sip") -> dict:
    """Pull a range into the parquet cache ahead of time, so a later backtest does not wait on Alpaca."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(400, "no symbols")
    _frames, stats = bar_store.get(syms, date_from, date_to, timeframe=timeframe, feed=feed)
    return stats


# Declared last on purpose: a bare "/{backtest_id}" matches any single segment, so it would capture
# "/backtest/data/cache" if it came first. FastAPI matches in declaration order.
@router.get("/{backtest_id}")
def get_backtest(backtest_id: str) -> dict:
    rows = get_client().table("backtests").select("*").eq("id", backtest_id).execute().data or []
    if not rows:
        raise HTTPException(404, "no such backtest")
    return rows[0]
