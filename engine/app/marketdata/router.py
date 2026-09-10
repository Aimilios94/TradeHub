"""`/data/*` — market data endpoints (Phase 1). All require the engine bearer token.

The FRONTEND never calls these; it uses the Supabase Edge Function proxy (supabase/functions/market-proxy).
These serve n8n crons, the scanner (Phase 2) and the owner's diagnostics.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.marketdata import alpaca, jobs, repo
from app.marketdata.premarket import (
    DEFAULT_BASELINE_DAYS,
    DEFAULT_CHECKPOINTS,
    PREMARKET_END,
    PREMARKET_START,
    baseline_from_rows,
    checkpoint_for,
    cumulative_to,
    parse_hhmm,
    relvol,
)

router = APIRouter(prefix="/data", tags=["market data"], dependencies=[Depends(require_token)])


def _symbols(csv: str) -> list[str]:
    syms = sorted({s.strip().upper() for s in csv.split(",") if s.strip()})
    if not syms:
        raise HTTPException(422, "symbols is empty")
    if len(syms) > 200:
        raise HTTPException(422, "max 200 symbols per request")
    return syms


def _feed(feed: str) -> str:
    if feed not in alpaca.FEEDS:
        raise HTTPException(422, f"feed must be one of {alpaca.FEEDS}")
    return feed


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/quotes")
def quotes(symbols: str = Query(..., description="comma-separated"), feed: str = "iex") -> dict:
    return {"feed": _feed(feed), "quotes": _wrap(alpaca.get_latest_quotes, _symbols(symbols), feed=feed)}


@router.get("/snapshots")
def snapshots(symbols: str = Query(...), feed: str = "iex") -> dict:
    return {"feed": _feed(feed), "snapshots": _wrap(alpaca.get_snapshots, _symbols(symbols), feed=feed)}


@router.get("/bars")
def bars(symbols: str = Query(...), start: dt.datetime = Query(...), end: dt.datetime | None = None,
         timeframe: str = "1Min", feed: str = "iex", limit: int = Query(5000, le=50_000)) -> dict:
    if start.tzinfo is None:
        start = start.replace(tzinfo=alpaca.ET)
    end = end or alpaca.now_et()
    if end.tzinfo is None:
        end = end.replace(tzinfo=alpaca.ET)
    if end <= start:
        raise HTTPException(422, "end must be after start")
    out = _wrap(alpaca.get_bars, _symbols(symbols), start, end, feed=_feed(feed), timeframe=timeframe)
    return {"feed": feed, "timeframe": timeframe, "count": min(len(out), limit), "bars": [b.as_dict() for b in out[:limit]]}


@router.get("/premarket/{symbol}")
def premarket(symbol: str, date: dt.date | None = None, as_of: str | None = Query(None, pattern=r"^\d{2}:\d{2}$"),
              feed: str | None = None, baseline_days: int = Query(DEFAULT_BASELINE_DAYS, ge=1, le=60)) -> dict:
    """Today's cumulative pre-market volume (live from Alpaca) vs the stored same-feed, same-checkpoint baseline.

    Default feed = app_settings.data_feed.premarket_volume (SIP, ~15 min delayed on the free plan). With SIP,
    `as_of` is clamped to the feed's latest bar so the ratio never compares a truncated window to a full one.
    """
    symbol = symbol.upper()
    cfg = repo.fetch_setting("data_feed", {}) or {}
    feed = _feed(feed or cfg.get("premarket_volume", "sip"))
    checkpoints = tuple(cfg.get("premarket_checkpoints") or DEFAULT_CHECKPOINTS)
    now = alpaca.now_et()
    day = date or now.date()
    as_of_t = parse_hhmm(as_of) if as_of else (now.time() if day == now.date() else PREMARKET_END)
    as_of_t = min(as_of_t, PREMARKET_END)

    start = alpaca.et_datetime(day, PREMARKET_START.hour, PREMARKET_START.minute)
    end = alpaca.et_datetime(day, as_of_t.hour, as_of_t.minute)
    if end <= start:
        raise HTTPException(422, "as_of is before the 04:00 ET pre-market open")
    live = _wrap(alpaca.get_bars, [symbol], start, end, feed=feed, timeframe="1Min")
    latest_bar = max((b.ts_et() for b in live), default=None)
    effective = as_of_t
    if feed == "sip" and latest_bar is not None and latest_bar.time() < as_of_t:
        # delayed feed: only trust the window we actually have
        effective = (latest_bar + dt.timedelta(minutes=1)).time()
    cp = checkpoint_for(effective, checkpoints)
    current = cumulative_to(live, day, effective)
    rows = _wrap(repo.fetch_baseline_rows, symbol, feed, before=day, limit=max(baseline_days * 2, 30))
    base = baseline_from_rows(rows, feed, checkpoint=cp, n=baseline_days, before=day)
    return {
        "symbol": symbol, "date": day.isoformat(), "feed": feed, "as_of_requested": as_of_t.strftime("%H:%M"),
        "as_of_effective": effective.strftime("%H:%M"), "latest_bar": latest_bar.isoformat() if latest_bar else None,
        "checkpoint": cp, "current_volume": current, "print_count": len(live), "baseline": base.as_dict(),
        "relvol": relvol(current, base.average), "baseline_days_requested": baseline_days,
        "note": None if base.days_used >= baseline_days else f"baseline has only {base.days_used}/{baseline_days} days — run the nightly job with days=20 to backfill",
    }


@router.post("/premarket-baseline/run")
def premarket_baseline_run(date: dt.date | None = None, days: int = Query(1, ge=1, le=60), symbols: str | None = None,
                           notify: bool = True, dry_run: bool = False) -> dict:
    """Nightly job (n8n 23:00 ET). `days>1` backfills; `symbols` overrides the universe; `dry_run` skips DB writes."""
    cfg = repo.fetch_setting("data_feed", {}) or {}
    checkpoints = tuple(cfg.get("premarket_checkpoints") or DEFAULT_CHECKPOINTS)
    res = jobs.run_premarket_baseline(end_date=date, days=days, symbols=_symbols(symbols) if symbols else None,
                                      checkpoints=checkpoints, notify=notify, dry_run=dry_run)
    return res.as_dict()


@router.get("/calibration/summary")
def calibration_summary(days: int = Query(30, ge=1, le=365)) -> dict:
    return _wrap(jobs.calibration_summary, days)


@router.get("/universe")
def universe() -> dict:
    syms = _wrap(repo.baseline_universe)
    return {"count": len(syms), "symbols": syms}
