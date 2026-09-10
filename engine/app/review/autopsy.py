"""Template 05 — the per-trade autopsy, run as a batch (plan Phase 6, §10 template 5).

One call per closed trade: did the entry meet the playbook, was the exit plan followed, what behavioural
pattern shows, what to work on, and a coach note. Output lands in `journal.autopsy`.

The original template asked the trader to paste a chart screenshot. That is replaced by a text-serialised
OHLCV window plus the computed indicator values at entry and exit — strictly more reliable for a text
model, and it means the autopsy cites real numbers rather than describing a picture it cannot see.

A trade already carrying an autopsy is skipped, so the weekly job is safe to re-run.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from app.db import get_client
from app.llm import LLMError, complete_json
from app.llm.prompts import load as load_prompt
from app.marketdata import alpaca

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "per_trade_autopsy"
BARS_BEFORE = dt.timedelta(minutes=30)
BARS_AFTER = dt.timedelta(minutes=30)
MAX_BARS = 90


def bars_text(symbol: str, entry: dt.datetime, exit_: dt.datetime | None) -> tuple[str, str]:
    """A compact OHLCV window around the trade. Returns (text, timeframe)."""
    end = (exit_ or entry) + BARS_AFTER
    start = entry - BARS_BEFORE
    try:
        bars = alpaca.get_bars([symbol], start, end, feed="sip", timeframe="1Min")
    except Exception as e:  # noqa: BLE001
        log.warning("bars unavailable for %s: %s", symbol, e)
        return "(bars unavailable)", "1Min"
    if not bars:
        return "(no bars returned for this window)", "1Min"
    rows = [f"{b.ts_et().strftime('%H:%M')} o{b.open} h{b.high} l{b.low} c{b.close} v{b.volume}"
            for b in bars[:MAX_BARS]]
    return "\n".join(rows), "1Min"


def indicators_at(symbol: str, when: dt.datetime | None) -> dict:
    """What the engine can compute at a moment. Anything unavailable is an explicit null, never invented."""
    if when is None:
        return {}
    try:
        stats = (get_client().table("symbol_stats").select("atr14,adv20,prev_close,high_20d,low_20d")
                 .eq("symbol", symbol).execute().data or [])
    except Exception:  # noqa: BLE001
        stats = []
    s = stats[0] if stats else {}
    return {"atr14": s.get("atr14"), "adv20": s.get("adv20"), "prev_close": s.get("prev_close"),
            "high_20d": s.get("high_20d"), "low_20d": s.get("low_20d"),
            "vwap": None, "ema20": None, "ema50": None,
            "note": "VWAP and EMAs arrive with the Phase 7 detectors"}


def rules_text(cfg: dict | None, key: str) -> str:
    if not cfg:
        return "(no strategy config linked to this trade)"
    conditions = ((cfg.get("entry_conditions") or {}).get("all") or []) if key == "entry" else \
                 (cfg.get("exit_logic") or [])
    if not conditions:
        return "(none configured)"
    if key == "entry":
        return "\n".join(f"- {c.get('label') or ''} [{c.get('detector')} {c.get('op')} {c.get('value')}]".rstrip()
                         for c in conditions)
    return "\n".join(f"- {c.get('plugin')} {c.get('params') or {}}" for c in conditions)


def run_one(row: dict, strategy: dict | None) -> tuple[dict, dict]:
    """Autopsy one journal row. Raises LLMError — the caller records the failure per trade."""
    spec = load_prompt(TEMPLATE_SLUG)
    entry = dt.datetime.fromisoformat(str(row["entry_time"]))
    exit_ = dt.datetime.fromisoformat(str(row["exit_time"])) if row.get("exit_time") else None
    text, timeframe = bars_text(row["symbol"], entry, exit_)
    actual = dict(row.get("actual") or {})
    actual |= {"entry_time": str(row["entry_time"]), "entry_price": row["entry_price"],
               "exit_time": str(row.get("exit_time")), "exit_price": row.get("exit_price"),
               "qty": row["qty"], "pnl_net": row.get("pnl_net"), "r_multiple": row.get("r_multiple"),
               "fees": row.get("fees"), "spread_cost": row.get("spread_cost")}

    return complete_json(spec, {
        "symbol": row["symbol"], "direction": row["direction"],
        "trade_date": str(row["entry_time"])[:10],
        "strategy_code": (strategy or {}).get("code", "(none)"),
        "strategy_name": (strategy or {}).get("name", "(none)"),
        "source": row.get("source", "signal"),
        "setup_type": row.get("setup_type") or "(not recorded)",
        "trade_number_of_day": row.get("trade_number_of_day") or 1,
        "running_pnl_r": row.get("running_pnl_r") if row.get("running_pnl_r") is not None else 0,
        "planned_json": json.dumps(row.get("planned") or {}, default=str),
        "entry_rules_text": rules_text(strategy, "entry"),
        "exit_rules_text": rules_text(strategy, "exit"),
        "actual_json": json.dumps(actual, default=str),
        "indicators_at_entry_json": json.dumps(indicators_at(row["symbol"], entry), default=str),
        "indicators_at_exit_json": json.dumps(indicators_at(row["symbol"], exit_), default=str),
        "bar_timeframe": timeframe,
        "bars_window": f"{(entry - BARS_BEFORE).strftime('%H:%M')}-"
                       f"{((exit_ or entry) + BARS_AFTER).strftime('%H:%M')} ET",
        "bars_text": text,
        "subjective_note": row.get("subjective_note") or "(no note written)",
    })


def run_batch(*, since: dt.date, until: dt.date, limit: int = 25,
              redo: bool = False) -> dict:
    """Autopsy every closed trade in the window that does not already have one."""
    q = (get_client().table("journal").select("*")
         .gte("exit_time", f"{since.isoformat()}T00:00:00")
         .lte("exit_time", f"{until.isoformat()}T23:59:59")
         .not_.is_("exit_time", "null").order("entry_time", desc=False).limit(limit))
    rows = q.execute().data or []
    if not redo:
        rows = [r for r in rows if not r.get("autopsy")]
    if not rows:
        return {"autopsied": 0, "skipped": 0, "errors": [], "note": "no trades needing an autopsy"}

    strategies = {r["id"]: r for r in
                  (get_client().table("strategy_configs").select("*").execute().data or [])}
    done, errors = 0, []
    for row in rows:
        try:
            payload, meta = run_one(row, strategies.get(row.get("strategy_id")))
        except LLMError as e:
            errors.append(f"{row['symbol']} {str(row['entry_time'])[:16]}: {str(e)[:160]}")
            continue
        try:
            get_client().table("journal").update({
                "autopsy": payload | {"model": meta.get("model"), "attempts": meta.get("attempts")},
                "autopsy_at": dt.datetime.now(dt.UTC).isoformat(),
            }).eq("id", row["id"]).execute()
            done += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{row['symbol']}: autopsy write failed: {str(e)[:120]}")
    return {"autopsied": done, "skipped": len(rows) - done - len(errors), "errors": errors}
