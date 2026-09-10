"""`/enrich/*` — Phase 3 catalyst & sentiment endpoints. Bearer token required.

Called by n8n workflow 02 straight after the morning scan, and by the owner for diagnostics.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.config import get_settings
from app.db import get_client
from app.llm import ollama
from app.marketdata import alpaca
from app.sentiment import run as enrich_run
from app.sentiment import sources as news_sources

router = APIRouter(prefix="/enrich", tags=["catalyst & sentiment"], dependencies=[Depends(require_token)])


def _sources(csv: str | None) -> list[str] | None:
    if not csv:
        return None
    names = [x.strip().lower() for x in csv.split(",") if x.strip()]
    bad = [n for n in names if n not in news_sources.KNOWN_SOURCES]
    if bad:
        raise HTTPException(422, f"unknown source {bad} (known: {list(news_sources.KNOWN_SOURCES)})")
    return names


@router.post("/run")
def run(date: dt.date | None = None,
        symbols: str | None = Query(None, description="comma-separated; overrides scan-based selection"),
        strategies: str | None = Query(None, description="comma-separated codes, e.g. S1,S3"),
        sources: str | None = Query(None, description="comma-separated; default from app_settings"),
        cap: int | None = Query(None, ge=1, le=25, description="candidates per strategy (default 5)"),
        notify: bool = True, dry_run: bool = False) -> dict:
    """Condense today's top candidates' news into `news_sentiment` and attach the catalyst to `scans`.

    Never changes `scans.passed` — it fills the catalyst and clears the `news_catalyst` pending gate.
    """
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    codes = [c.strip().upper() for c in strategies.split(",") if c.strip()] if strategies else None
    try:
        res = enrich_run.run_enrich(date=date, symbols=syms, strategy_codes=codes,
                                    source_names=_sources(sources), cap=cap, notify=notify, dry_run=dry_run)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e
    return res.as_dict()


@router.get("/status")
def status() -> dict:
    """Is the local condenser reachable, and which sources are configured? Cheap pre-flight for the owner."""
    ready, detail = ollama.available()
    cfg = enrich_run.config()
    s = get_settings()
    credentials = {
        "finnhub": "configured" if s.finnhub_api_key else "missing FINNHUB_API_KEY",
        "yahoo": "configured (no key required)",
        "reddit": "configured" if (s.reddit_client_id and s.reddit_client_secret)
                  else "missing REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET",
    }
    return {"ollama": {"ready": ready, "detail": detail, "base_url": ollama.base_url()},
            "enabled_sources": list(cfg["sources"]), "known_sources": list(news_sources.KNOWN_SOURCES),
            "credentials": credentials, "candidates_per_strategy": cfg["candidates_per_strategy"],
            "lookback_days": cfg["lookback_days"]}


@router.get("/{symbol}")
def symbol_rows(symbol: str, date: dt.date | None = None) -> dict:
    """Today's (or `?date=`) condensed rows for one symbol, one per source."""
    day = (date or alpaca.now_et().date()).isoformat()
    rows = (get_client().table("news_sentiment").select("*")
            .eq("symbol", symbol.upper()).eq("trade_date", day).execute().data or [])
    return {"symbol": symbol.upper(), "trade_date": day, "count": len(rows), "rows": rows}
