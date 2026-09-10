"""Assemble everything the hosted prompts are shown. Pure assembly — no LLM calls, no writes.

Template 02's first hard rule is that Hermes never calculates: every price it may quote has to come from
a level supplied here, and size is capped by `max_size_by_risk` computed here. So this module is where
the honesty of the whole phase lives — anything it cannot compute is passed as an explicit null with a
note, never omitted and never guessed (template 02 system rule 8).

Known nulls in Phase 4, each waiting on a later phase:
  vwap / poc / vah / val   — detectors 3, 23 are 'planned' (Phase 7)
  journal_lessons          — `journal` fills from Phase 6 autopsies; empty until then
  economic_events          — populated in Phase 6 (§29)
  analyst_report_summary   — E2 (the Financial Analyst extension module, plan §36) — later phase
  daily_pnl_r              — needs closed trades in `journal`
"""

from __future__ import annotations

import datetime as dt
import logging

from app.broker import account as broker_account
from app.db import get_client
from app.marketdata import alpaca
from app.marketdata import repo as md_repo

log = logging.getLogger(__name__)

ASSUMED_STOP_ATR_MULT = 1.0     # sizing needs a stop distance before Hermes states one; 1 ATR is the proxy
ROUND_NUMBER_SPAN = 0.05        # round levels within ±5% of price


def round_numbers(price: float | None) -> list[float]:
    """Whole/half-dollar levels near the price — the 'round numbers' the playbooks watch."""
    if not price or price <= 0:
        return []
    # low-priced names need a finer grid: at $3.70 a 0.50 step leaves NO round level within ±5%,
    # which silently strips the candidate of half its usable structure (observed on TNON 2026-09-10).
    step = 0.10 if price < 5 else (0.25 if price < 10 else (0.5 if price < 20 else (1.0 if price < 100 else 5.0)))
    lo, hi = price * (1 - ROUND_NUMBER_SPAN), price * (1 + ROUND_NUMBER_SPAN)
    start = int(lo / step) * step
    out, v = [], start
    while v <= hi:
        if v >= lo and v > 0:
            out.append(round(v, 2))
        v += step
    return out[:12]


def atr_extensions(price: float | None, atr: float | None) -> dict:
    """Measured-move levels at 1/2/3 ATR either side of price.

    A target is by definition beyond current structure, so without these the only levels on offer sit at
    or behind the price and every target Hermes writes fails the ±3% level check (template 02 Notes).
    Code computes them, Hermes only chooses — the division of labour the template demands.
    """
    if not price or not atr or atr <= 0:
        return {}
    mults = (0.5, 1, 1.5, 2, 3)
    fmt = lambda n: str(n).replace(".", "_")
    return {f"plus_{fmt(n)}_atr": round(price + n * atr, 2) for n in mults} | \
           {f"minus_{fmt(n)}_atr": round(price - n * atr, 2) for n in mults if price - n * atr > 0}


def size_by_stop_distance(equity: float | None, risk_pct: float, price: float | None,
                          atr: float | None) -> list[dict]:
    """Max shares for a range of plausible stop distances.

    `max_size_by_risk` assumes a 1-ATR stop, but Hermes often chooses a wider one — and then its size,
    correct for the assumed stop, breaches the real risk budget. Handing it the whole table lets it read
    off the right size for the stop it actually picks, instead of guessing (docs/DECISIONS.md #33).
    """
    if not equity or not price or not atr or atr <= 0:
        return []
    budget = equity * (risk_pct / 100.0)
    affordable = int(equity // price)
    out = []
    for mult in (0.5, 1.0, 1.5, 2.0, 3.0):
        dist = atr * mult
        out.append({"stop_distance": round(dist, 4), "atr_multiple": mult,
                    "max_shares": max(0, min(int(budget // dist), affordable))})
    return out


def stop_options(price: float | None, levels: dict, equity: float | None, risk_pct: float,
                 direction: str = "long") -> list[dict]:
    """Every sane stop level, each already paired with the share count the risk budget allows for it.

    This exists because the alternative asks the model to do arithmetic. Template 02 forbids Hermes
    calculating anything, yet a stop it picks changes the correct size — so unless code pre-computes the
    pairing, the model must either invent a stop distance or mis-size against a stale cap. Both were
    observed on 2026-09-10 (docs/DECISIONS.md #33). With this table it copies two numbers off one row.
    """
    if not price or not equity:
        return []
    budget = equity * (risk_pct / 100.0)
    affordable = int(equity // price)
    candidates: list[tuple[str, float]] = []
    for key in ("premarket_low", "prior_low", "low_20d") if direction == "long" else \
               ("premarket_high", "prior_high", "high_20d"):
        if levels.get(key) is not None:
            candidates.append((key, float(levels[key])))
    for key, value in (levels.get("atr_extensions") or {}).items():
        if (direction == "long" and key.startswith("minus_")) or \
           (direction == "short" and key.startswith("plus_")):
            candidates.append((f"atr_extensions.{key}", float(value)))

    out = []
    for basis, level in candidates:
        distance = (price - level) if direction == "long" else (level - price)
        if distance <= 0:
            continue          # a "stop" on the wrong side of price is not a stop
        out.append({"stop": round(level, 4), "basis": basis, "stop_distance": round(distance, 4),
                    "max_shares": max(0, min(int(budget // distance), affordable)),
                    "risk_at_max_shares": round(min(int(budget // distance), affordable) * distance, 2)})
    out.sort(key=lambda r: r["stop_distance"])
    return out


def levels_for(metrics: dict, stats: dict) -> dict:
    """Every level Hermes is allowed to reference. Unavailable ones are explicit nulls."""
    prior = metrics.get("prior_day") or {}
    price = metrics.get("price")
    atr = metrics.get("atr14") or stats.get("atr14")
    return {
        "atr_extensions": atr_extensions(price, float(atr) if atr is not None else None),
        "premarket_high": metrics.get("premarket_high"),
        "premarket_low": metrics.get("premarket_low"),
        "prior_high": prior.get("high") if prior.get("high") is not None else stats.get("prev_high"),
        "prior_low": prior.get("low") if prior.get("low") is not None else stats.get("prev_low"),
        "prior_close": prior.get("close") if prior.get("close") is not None else stats.get("prev_close"),
        "high_20d": stats.get("high_20d"), "low_20d": stats.get("low_20d"),
        "high_60d": stats.get("high_60d"), "low_60d": stats.get("low_60d"),
        "round_numbers": round_numbers(price),
        "vwap": None, "poc": None, "vah": None, "val": None,   # Phase 7 detectors
    }


def spread_pct(symbol: str, quotes: dict) -> float | None:
    """`alpaca.get_latest_quotes` already computes this; fall back only if the field is absent."""
    q = quotes.get(symbol) or {}
    if q.get("spread_pct") is not None:
        return q["spread_pct"]
    bid, ask = q.get("bid"), q.get("ask")
    if not bid or not ask or ask <= 0:
        return None
    return round((ask - bid) / ask * 100, 4)


def max_size_by_risk(equity: float | None, risk_pct: float, price: float | None, atr: float | None) -> dict:
    """Shares the risk budget allows, using 1 ATR as the stop-distance proxy.

    Hermes has not stated a stop yet when it is shown this number, so the proxy is stated openly in the
    payload; `plan.validate_plan` re-checks the real size against the real stop once the plan comes back.
    """
    if not equity or not price or not atr or atr <= 0:
        return {"shares": None, "risk_dollars": None, "assumed_stop_distance": None,
                "basis": "unknown — missing equity, price or ATR"}
    risk_dollars = equity * (risk_pct / 100.0)
    stop_distance = atr * ASSUMED_STOP_ATR_MULT
    shares = int(risk_dollars // stop_distance)
    affordable = int(equity // price) if price else shares
    return {"shares": max(0, min(shares, affordable)), "risk_dollars": round(risk_dollars, 2),
            "assumed_stop_distance": round(stop_distance, 4),
            "basis": f"{risk_pct}% of equity / (1 x ATR14); re-checked against the stop Hermes returns"}


def advisory_precheck(*, symbol: str, direction_hint: str, acct: broker_account.AccountSnapshot,
                      stats: dict, spread: float | None, risk: dict, kill_switch: bool) -> dict:
    """The cheap, read-only slice of the Phase 5 Guardian gate — ADVISORY ONLY.

    It cannot block anything and it is not the Guardian gate; it exists so template 02 sees real numbers
    instead of blanks (docs/DECISIONS.md #31). Phase 5 replaces it with the enforcing version.
    """
    checks: list[dict] = []

    def add(name, verdict, detail):
        checks.append({"check": name, "verdict": verdict, "detail": detail})

    add("kill_switch", "red" if kill_switch else "green",
        "engaged — no orders may be placed" if kill_switch else "not engaged")

    if acct.ok:
        cap = risk.get("max_concurrent")
        room = None if cap is None else cap - acct.open_positions
        add("max_concurrent", "green" if room is None or room > 0 else "red",
            f"{acct.open_positions} open, strategy cap {cap}" + ("" if room is None else f", room {room}"))
        add("account_status", "red" if (acct.trading_blocked or acct.account_blocked) else "green",
            f"trading_blocked={acct.trading_blocked} account_blocked={acct.account_blocked}")
    else:
        add("max_concurrent", "unknown", f"broker unreachable: {acct.error}")
        add("account_status", "unknown", f"broker unreachable: {acct.error}")

    ceiling = risk.get("spread_ceiling_pct")
    if spread is None:
        add("spread", "unknown", "no quote available")
    elif ceiling is None:
        add("spread", "green", f"{spread}% (no ceiling set for this strategy)")
    else:
        add("spread", "green" if spread <= ceiling else "red", f"{spread}% vs ceiling {ceiling}%")

    if direction_hint == "short":
        etb = stats.get("easy_to_borrow")
        add("shortable", "green" if (stats.get("shortable") and etb) else "red",
            f"shortable={stats.get('shortable')} easy_to_borrow={etb}")

    add("daily_loss_limit", "unknown", "no closed trades yet — `journal` fills from Phase 6")

    order = {"red": 0, "unknown": 1, "green": 2}
    worst = min((c["verdict"] for c in checks), key=lambda v: order.get(v, 1))
    return {"verdict": {"red": "red", "unknown": "yellow", "green": "green"}[worst],
            "advisory": True, "note": "advisory only — the enforcing Guardian gate is Phase 5",
            "checks": checks}


def journal_lessons(limit: int = 10) -> list[str]:
    """Recent lessons from the trader's own autopsies. Empty until Phase 6 writes them."""
    try:
        rows = (get_client().table("journal").select("symbol,entry_time,autopsy,subjective_note")
                .not_.is_("autopsy", "null").order("entry_time", desc=True).limit(limit).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.info("journal lessons unavailable: %s", e)
        return []
    out = []
    for r in rows:
        a = r.get("autopsy") or {}
        lesson = a.get("lesson") or a.get("takeaway") or r.get("subjective_note")
        if lesson:
            out.append(f"{r['symbol']} {str(r.get('entry_time'))[:10]}: {lesson}")
    return out


def economic_events(day: dt.date) -> list[dict]:
    """Today's high-impact releases. Empty until Phase 6 populates `economic_events` (§29)."""
    try:
        rows = (get_client().table("economic_events").select("*")
                .gte("event_time", f"{day.isoformat()}T00:00:00")
                .lte("event_time", f"{day.isoformat()}T23:59:59").execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.info("economic events unavailable: %s", e)
        return []
    return [{"time": r.get("event_time"), "name": r.get("name"), "impact": r.get("impact")} for r in rows]


def catalysts_for(symbols: list[str], day: dt.date) -> dict[str, list[dict]]:
    """The Phase 3 `news_sentiment` rows, per symbol, in the shape templates 02/04 expect."""
    if not symbols:
        return {}
    out: dict[str, list[dict]] = {s: [] for s in symbols}
    try:
        rows = (get_client().table("news_sentiment").select("*")
                .eq("trade_date", day.isoformat()).in_("symbol", symbols).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("catalysts unavailable: %s", e)
        return out
    for r in rows:
        out.setdefault(r["symbol"], []).append({
            "source": r["source"], "catalyst_type": r.get("catalyst_type"),
            "sentiment_score": float(r["sentiment_score"]) if r.get("sentiment_score") is not None else None,
            "key_facts": r.get("key_facts") or [],
            "one_sentence_reason": r.get("one_sentence_reason"),
            "freshness": (r.get("raw_items") or {}).get("freshness"),
        })
    return out


def symbol_stats_for(symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    rows = (get_client().table("symbol_stats").select("*").in_("symbol", symbols).execute().data or [])
    return {r["symbol"]: r for r in rows}


def build_candidates(scan_rows: list[dict], *, day: dt.date, risk: dict, acct: broker_account.AccountSnapshot,
                     kill_switch: bool, direction_hint: str = "long") -> list[dict]:
    """One fully computed candidate object per scan row, in template 02's documented shape."""
    symbols = [r["symbol"] for r in scan_rows]
    stats_by = symbol_stats_for(symbols)
    cats = catalysts_for(symbols, day)
    try:
        quotes = alpaca.get_latest_quotes(symbols, feed="iex") if symbols else {}
    except Exception as e:  # noqa: BLE001
        log.warning("quotes unavailable: %s", e)
        quotes = {}

    risk_pct = float(risk.get("risk_per_trade_pct") or 0.5)
    out = []
    for r in scan_rows:
        sym = r["symbol"]
        m = r.get("metrics") or {}
        st = stats_by.get(sym, {})
        sp = spread_pct(sym, quotes)
        price = m.get("price") or st.get("prev_close")
        atr = m.get("atr14") or st.get("atr14")
        levels = levels_for(m, st)
        out.append({
            "symbol": sym,
            "rank": r.get("rank"),
            "metrics": {
                "gap_pct": m.get("gap_pct"), "relvol": m.get("relvol"),
                "float": m.get("float_shares") or st.get("float_shares") or st.get("shares_outstanding"),
                "float_is_approximate": bool(m.get("float_source") == "fmp_shares_outstanding"
                                             or st.get("float_source") == "fmp_shares_outstanding"),
                "atr": float(atr) if atr is not None else None,
                "adv": m.get("adv20") or st.get("adv20"),
                "premarket_volume": m.get("premarket_volume"),
                "premarket_volume_pct_adv": m.get("pct_of_adv"),
                "premarket_prints": m.get("premarket_prints"),
                "price": price, "spread_pct": sp,
            },
            "levels": levels,
            "catalysts": cats.get(sym, []),
            "guardian_precheck": advisory_precheck(symbol=sym, direction_hint=direction_hint, acct=acct,
                                                   stats=st, spread=sp, risk=risk, kill_switch=kill_switch),
            "max_size_by_risk": max_size_by_risk(acct.equity, risk_pct, price,
                                                 float(atr) if atr is not None else None),
            "size_by_stop_distance": size_by_stop_distance(acct.equity, risk_pct, price,
                                                           float(atr) if atr is not None else None),
            "stop_options": stop_options(price, levels, acct.equity, risk_pct, direction_hint),
            "scan_score": m.get("score"),
            "pending_gates": m.get("pending_gates") or [],
            "analyst_report_summary": None,      # E2 (Financial Analyst module, §36) — later phase
        })
    return out


def account_block(acct: broker_account.AccountSnapshot) -> dict:
    return {"buying_power": acct.buying_power, "equity": acct.equity,
            "open_positions": acct.open_positions,
            "daily_pnl_r": None,      # needs closed trades in `journal` (Phase 6)
            "broker_ok": acct.ok, "broker_error": acct.error}


def kill_switch_engaged() -> bool:
    return bool(md_repo.fetch_setting("kill_switch", False))
