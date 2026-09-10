"""Template 03 — the S2 pre-market watchlist briefing.

A ranked table plus market context: what is driving each name, which levels matter, which setups might
develop, priority High/Med/Low. Decision support only — a briefing has no entry, stop or size, and
nothing can be executed from it, which is why it lives in `briefings` rather than `signals`
(docs/DECISIONS.md #30).
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from app.db import get_client
from app.llm import complete_json
from app.llm.prompts import load as load_prompt
from app.marketdata import alpaca

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "watchlist_briefing"
INDEX_SYMBOLS = ("SPY", "QQQ", "IWM")


def market_context_inputs(day: dt.date, events: list[dict]) -> dict:
    """SPY/QQQ/IWM pre-market move. VIX is not on Alpaca's equity feed — passed as an explicit null."""
    out: dict = {"indices": {}, "vix": None,
                 "vix_note": "not available on the Alpaca equity feed",
                 "economic_events": events or [],
                 "sector_strength": None,
                 "sector_note": "sector ranking arrives with detector 20 (Phase 7)"}
    try:
        snaps = alpaca.get_snapshots(list(INDEX_SYMBOLS), feed="iex")
    except Exception as e:  # noqa: BLE001
        log.warning("index snapshots unavailable: %s", e)
        out["indices_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out
    for sym in INDEX_SYMBOLS:
        # shape comes from alpaca.get_snapshots: trade{price}, daily{o,h,l,c,v}, prev_daily{o,h,l,c,v}
        s = snaps.get(sym) or {}
        daily = s.get("daily") or {}
        prev = s.get("prev_daily") or {}
        last = (s.get("trade") or {}).get("price") or daily.get("c")
        prev_close = prev.get("c")
        out["indices"][sym] = {
            "last": last, "prev_close": prev_close,
            "session_open": daily.get("o"), "session_high": daily.get("h"), "session_low": daily.get("l"),
            "change_pct": round((last / prev_close - 1) * 100, 3) if last and prev_close else None,
        }
    return out


def watchlist_payload(candidates: list[dict]) -> list[dict]:
    """Template 03's documented shape — no guardian block, no sizing: it is not proposing trades."""
    rows = []
    for c in candidates:
        m, lv = c.get("metrics") or {}, c.get("levels") or {}
        rows.append({
            "symbol": c["symbol"],
            "premarket": {"price": m.get("price"), "change_pct": m.get("gap_pct"),
                          "volume": m.get("premarket_volume"), "relvol": m.get("relvol"),
                          "print_count": m.get("premarket_prints")},
            "levels": {k: lv.get(k) for k in ("prior_high", "prior_low", "prior_close", "premarket_high",
                                              "premarket_low", "poc", "vah", "val")} | {
                "round_numbers": lv.get("round_numbers") or []},
            "indicators": {"atr": m.get("atr"), "ema20": None, "ema50": None, "ema200": None,
                           "note": "EMAs arrive with detector 1 (Phase 7)"},
            "catalysts": c.get("catalysts") or [],
        })
    return rows


def run_briefing(*, day: dt.date, as_of_et: str, candidates: list[dict],
                 events: list[dict]) -> tuple[dict, dict, dict]:
    """Call template 03. Returns (payload, meta, inputs). Raises LLMError."""
    spec = load_prompt(TEMPLATE_SLUG)
    ctx = market_context_inputs(day, events)
    rows = watchlist_payload(candidates)
    payload, meta = complete_json(spec, {
        "trade_date": day.isoformat(),
        "as_of_et": as_of_et,
        "market_context_json": json.dumps(ctx, separators=(",", ":"), default=str),
        "watchlist_json": json.dumps(rows, separators=(",", ":"), default=str),
    })
    return payload, meta, {"market_context": ctx, "watchlist": rows}


def write_briefing(*, day: dt.date, as_of_et: str, payload: dict, meta: dict, inputs: dict,
                   strategy_id: str | None, template_id: str | None) -> int:
    row = {
        "briefing_date": day.isoformat(), "kind": "watchlist", "strategy_id": strategy_id,
        "as_of_et": as_of_et, "market_context": payload.get("market_context"),
        "rows": payload.get("rows") or [], "inputs": inputs, "model": meta.get("model"),
        "prompt_template_id": template_id, "raw_llm_response": payload,
        "usage": {"attempts": meta.get("attempts"), "tier": meta.get("tier")},
    }
    get_client().table("briefings").upsert([row], on_conflict="briefing_date,kind").execute()
    return 1


def summary_lines(payload: dict) -> list[str]:
    order = {"High": 0, "Medium": 1, "Low": 2}
    rows = sorted(payload.get("rows") or [], key=lambda r: order.get(r.get("priority"), 3))
    out = []
    for r in rows:
        out.append(f"{r.get('priority', '?')[:1]} {r.get('symbol'):<6} {r.get('bias', '')} · "
                   f"{(r.get('catalyst') or 'no catalyst')[:48]} · {', '.join(r.get('setups') or [])[:40]}")
    return out
