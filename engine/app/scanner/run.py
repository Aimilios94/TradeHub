"""The scan runner (plan Phase 2, §7 S1/S3, §8 F-A). Config-driven: every filter comes from `strategy_configs`.

Morning (08:00 ET):
  1. load strategies to scan (screener universes + full_auto watchlists; semi_auto intraday strategies are
     Phase 7's live-detector loop, not a screener)
  2. stage A — local pre-filter on `symbol_stats` (price band, ADV, ATR) → candidate set per strategy
  3. stage B — one bulk SIP fetch of today's pre-market 1-min bars (04:00 → now, ~15 min delayed) for the union
  4. per strategy × symbol: run the implemented detectors, evaluate `entry_conditions.all` + screener params,
     score with the soft conditions, rank, cap → write `scans` (passed + rejected-with-reason)
  5. Telegram summary

Midday (11:30 ET): for today's passed symbols + watchlists, regular-session volume vs ADV and range vs ATR
(F-A "names exceeding full-day ADV/ATR") → `scans` rows with run_kind='midday'.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd

from app.db import get_client
from app.detectors import DetectorResult
from app.detectors import get as get_detector
from app.detectors import implemented as implemented_detectors
from app.marketdata import alpaca
from app.marketdata import repo as md_repo
from app.marketdata.premarket import (
    DEFAULT_CHECKPOINTS,
    PREMARKET_END,
    PREMARKET_START,
    REGULAR_END,
    baseline_from_rows,
    checkpoint_for,
)
from app.models import Condition, ScreenerUniverse, StrategyConfig, WatchlistUniverse
from app.notify import send_telegram
from app.scanner import universe as universe_job
from app.scanner.conditions import ConditionOutcome, evaluate, summarize

log = logging.getLogger(__name__)

PM_BATCH = 400          # symbols per pre-market bars request
DAILY_BATCH = 500
SCANNED_STATUSES = ("backtesting", "paper", "live")   # backtesting strategies are scanned (no orders) so the scanner proves itself
MAX_PASSED_PER_STRATEGY = 25


# ---------- data access ----------

def load_strategies(statuses: tuple[str, ...] = SCANNED_STATUSES) -> list[tuple[str, StrategyConfig]]:
    rows = get_client().table("strategy_configs").select("*").in_("status", list(statuses)).execute().data or []
    out = []
    for r in rows:
        cfg = StrategyConfig(**{k: r[k] for k in StrategyConfig.model_fields if k in r and r[k] is not None})
        out.append((r["id"], cfg))
    return sorted(out, key=lambda x: x[1].code)


def is_scannable(cfg: StrategyConfig) -> bool:
    if isinstance(cfg.universe, ScreenerUniverse):
        return True
    return cfg.automation_mode == "full_auto"


def load_symbol_stats(price_min: float | None = None, price_max: float | None = None, adv_min: int | None = None,
                      atr_min: float | None = None, symbols: list[str] | None = None) -> dict[str, dict]:
    q = get_client().table("symbol_stats").select("*").eq("tradable", True)
    if symbols:
        q = q.in_("symbol", symbols)
    if price_min is not None:
        q = q.gte("prev_close", price_min)
    if price_max is not None:
        q = q.lte("prev_close", price_max)
    if adv_min is not None:
        q = q.gte("adv20", adv_min)
    if atr_min is not None:
        q = q.gte("atr14", atr_min)
    out: dict[str, dict] = {}
    page = 1000
    off = 0
    while True:
        rows = q.range(off, off + page - 1).execute().data or []
        for r in rows:
            out[r["symbol"]] = r
        if len(rows) < page:
            break
        off += page
    return out


def premarket_frames(symbols: list[str], day: dt.date, end: dt.datetime, feed: str) -> dict[str, pd.DataFrame]:
    start = alpaca.et_datetime(day, PREMARKET_START.hour, PREMARKET_START.minute)
    end = min(end, alpaca.et_datetime(day, PREMARKET_END.hour, PREMARKET_END.minute))
    frames: dict[str, list[dict]] = {}
    for i in range(0, len(symbols), PM_BATCH):
        for b in alpaca.get_bars(symbols[i:i + PM_BATCH], start, end, feed=feed, timeframe="1Min"):
            frames.setdefault(b.symbol, []).append({"ts": b.ts_et(), "open": b.open, "high": b.high, "low": b.low,
                                                     "close": b.close, "volume": b.volume})
    return {s: pd.DataFrame(rows).set_index("ts").sort_index() for s, rows in frames.items()}


def regular_frames(symbols: list[str], day: dt.date, end: dt.datetime, feed: str) -> dict[str, pd.DataFrame]:
    start = alpaca.et_datetime(day, PREMARKET_END.hour, PREMARKET_END.minute)
    end = min(end, alpaca.et_datetime(day, REGULAR_END.hour, REGULAR_END.minute))
    frames: dict[str, list[dict]] = {}
    for i in range(0, len(symbols), PM_BATCH):
        for b in alpaca.get_bars(symbols[i:i + PM_BATCH], start, end, feed=feed, timeframe="1Min"):
            frames.setdefault(b.symbol, []).append({"ts": b.ts_et(), "open": b.open, "high": b.high, "low": b.low,
                                                     "close": b.close, "volume": b.volume})
    return {s: pd.DataFrame(rows).set_index("ts").sort_index() for s, rows in frames.items()}


def daily_frames(symbols: list[str], day: dt.date, lookback_days: int = 100) -> dict[str, pd.DataFrame]:
    start = alpaca.et_datetime(day - dt.timedelta(days=lookback_days), 0, 0)
    end = alpaca.et_datetime(day - dt.timedelta(days=1), 23, 59)
    grouped: dict[str, list[alpaca.Bar]] = {}
    for i in range(0, len(symbols), DAILY_BATCH):
        for b in alpaca.get_bars(symbols[i:i + DAILY_BATCH], start, end, feed="sip", timeframe="1Day"):
            grouped.setdefault(b.symbol, []).append(b)
    return {s: universe_job.daily_frame(bl) for s, bl in grouped.items()}


def baseline_rows_bulk(symbols: list[str], feed: str, before: dt.date, limit_days: int = 30) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {s: [] for s in symbols}
    since = (before - dt.timedelta(days=limit_days * 2)).isoformat()
    for i in range(0, len(symbols), 200):
        rows = (get_client().table("premarket_volume_baseline").select("symbol,trade_date,feed,premarket_volume,cum_profile")
                .in_("symbol", symbols[i:i + 200]).eq("feed", feed).gte("trade_date", since).lt("trade_date", before.isoformat())
                .execute().data or [])
        for r in rows:
            out.setdefault(r["symbol"], []).append(r)
    return out


# ---------- stage A: screener params → candidates ----------

def stage_a_filter(params: dict, stats: dict[str, dict]) -> tuple[dict[str, dict], dict]:
    """Apply the cheap, local screener params. Float is enforced only when known (unknown → keep, flag)."""
    keep: dict[str, dict] = {}
    unknown_float = 0
    for sym, s in stats.items():
        pc = s.get("prev_close")
        if pc is None:
            continue
        if params.get("price_min") is not None and pc < params["price_min"]:
            continue
        if params.get("price_max") is not None and pc > params["price_max"]:
            continue
        if params.get("adv_min_shares") is not None and (s.get("adv20") or 0) < params["adv_min_shares"]:
            continue
        if params.get("atr_min_usd") is not None and (s.get("atr14") or 0) < params["atr_min_usd"]:
            continue
        fl = s.get("float_shares") or s.get("shares_outstanding")
        if params.get("float_max_shares") is not None:
            if fl is None:
                unknown_float += 1
            elif fl > params["float_max_shares"]:
                continue
        keep[sym] = s
    return keep, {"candidates": len(keep), "unknown_float": unknown_float}


def gap_filter(params: dict, gap: float | None) -> bool:
    if gap is None:
        return False
    if params.get("gap_pct_min") is not None and gap < params["gap_pct_min"]:
        return False
    if params.get("gap_pct_max") is not None and gap > params["gap_pct_max"]:
        return False
    if params.get("gap_abs_pct_min") is not None and abs(gap) < params["gap_abs_pct_min"]:
        return False
    return True


# ---------- per-symbol evaluation ----------

@dataclass(slots=True)
class SymbolEval:
    symbol: str
    metrics: dict
    outcomes: list[ConditionOutcome]
    verdict: dict
    score: float
    passed: bool
    reject_reason: str | None = None


def run_detector(name: str, pm: pd.DataFrame | None, **ctx) -> DetectorResult | None:
    if name not in implemented_detectors():
        return None
    try:
        return get_detector(name)(pm, **ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("detector %s failed for %s: %s", name, ctx.get("symbol"), e)
        return None


def evaluate_symbol(cfg: StrategyConfig, symbol: str, *, stats: dict, pm: pd.DataFrame | None, daily: pd.DataFrame | None,
                    baseline_rows: list[dict], feed: str, checkpoints: tuple[str, ...], as_of: dt.time,
                    baseline_days_default: int = 14) -> SymbolEval:
    prev_close = float(stats["prev_close"]) if stats.get("prev_close") is not None else None
    ctx_base = {"symbol": symbol, "stats": stats, "daily": daily, "prev_close": prev_close}

    # detectors the strategy references (hard + soft), each run once with the strategy's params
    conds: list[tuple[str, Condition]] = [("all", c) for c in cfg.entry_conditions.all] + \
                                         [("soft", c) for c in cfg.entry_conditions.soft]
    results: dict[str, DetectorResult | None] = {}
    # always compute the core metrics even if the strategy does not list them (they feed ranking + the signal card)
    core = {"gap_pct": {}, "premarket_high_low": {}, "atr": {"period": 14}, "adv": {"lookback_days": 20},
            "prior_day_levels": {}}
    for name, params in core.items():
        results[name] = run_detector(name, pm, **ctx_base, **params)
    for _, c in conds:
        params = dict(c.params)
        if c.detector == "relative_volume_premarket":
            n = int(params.get("baseline_days", baseline_days_default))
            cp = checkpoint_for(as_of, checkpoints)
            b = baseline_from_rows(baseline_rows, feed, checkpoint=cp, n=n, before=None)
            adv_res = results.get("adv")
            params.update({"baseline_avg": b.average, "baseline_days_used": b.days_used, "baseline_days": n,
                           "checkpoint": cp, "feed": feed, "adv_value": adv_res.value if adv_res else stats.get("adv20")})
        elif c.detector == "distance_to_level_atr":
            pdl = results.get("prior_day_levels")
            atr_res = results.get("atr")
            # "battlefield" levels are DAILY structure (plan §8 F-A): prior-day H/L/C, 20/60-day extremes, round numbers.
            # Pre-market H/L are excluded — the latest print is always at/near them, which would make every gap "at a level".
            levels = {}
            if pdl:
                levels.update({"prior_high": pdl.fields.get("high"), "prior_low": pdl.fields.get("low"),
                               "prior_close": pdl.fields.get("close")})
            for k in ("high_20d", "low_20d", "high_60d", "low_60d"):
                if stats.get(k) is not None:
                    levels[k] = float(stats[k])
            params.update({"levels": levels, "atr_value": atr_res.value if atr_res else stats.get("atr14"),
                           "price": results["gap_pct"].fields.get("price") if results.get("gap_pct") else None})
        if c.detector not in results or c.params or c.detector == "relative_volume_premarket":
            results[c.detector] = run_detector(c.detector, pm, **ctx_base, **params)

    outcomes = [evaluate(c, results.get(c.detector)) for kind, c in conds if kind == "all"]
    soft = [evaluate(c, results.get(c.detector)) for kind, c in conds if kind == "soft"]
    verdict = summarize(outcomes)

    g = results.get("gap_pct")
    rv = results.get("relative_volume_premarket")
    pmhl = results.get("premarket_high_low")
    metrics = {
        "feed": feed, "as_of": as_of.strftime("%H:%M"), "prev_close": prev_close,
        "price": g.fields.get("price") if g else None, "gap_pct": g.value if g else None,
        "premarket_volume": pmhl.fields.get("volume") if pmhl else 0,
        "premarket_prints": pmhl.fields.get("print_count") if pmhl else 0,
        "premarket_high": pmhl.fields.get("high") if pmhl else None, "premarket_low": pmhl.fields.get("low") if pmhl else None,
        "relvol": rv.value if rv else None, "relvol_checkpoint": rv.fields.get("checkpoint") if rv else None,
        "relvol_baseline_days": rv.fields.get("days_used") if rv else None,
        "pct_of_adv": rv.fields.get("pct_of_adv") if rv else None,
        "adv20": results["adv"].value if results.get("adv") else stats.get("adv20"),
        "atr14": results["atr"].value if results.get("atr") else stats.get("atr14"),
        "float_shares": results["float_shares"].value if results.get("float_shares") else None,
        "float_source": results["float_shares"].fields.get("source") if results.get("float_shares") else None,
        "prior_day": results["prior_day_levels"].fields if results.get("prior_day_levels") else None,
        "nearest_level": results["distance_to_level_atr"].fields if results.get("distance_to_level_atr") else None,
        "conditions": [o.as_dict() for o in outcomes], "soft": [o.as_dict() for o in soft],
        "pending_gates": verdict["pending"] + ([cfg.entry_conditions.hermes_gate] if cfg.entry_conditions.hermes_gate else []),
        "quality_flags": verdict["approximate"],
        "shortable": stats.get("shortable"), "easy_to_borrow": stats.get("easy_to_borrow"),
    }
    # score: relvol dominates, then gap magnitude, then participation; soft passes add a little
    score = (metrics["relvol"] or 0) * 10 + abs(metrics["gap_pct"] or 0) + (metrics["pct_of_adv"] or 0) / 10
    score += sum(1 for o in soft if o.status == "pass")
    return SymbolEval(symbol=symbol, metrics=metrics, outcomes=outcomes, verdict=verdict, score=round(score, 3),
                      passed=verdict["passed"], reject_reason=None if verdict["passed"] else "; ".join(verdict["failed"]))


# ---------- the runs ----------

@dataclass(slots=True)
class ScanResult:
    kind: str
    scan_date: str
    as_of: str
    strategies: list[dict] = field(default_factory=list)
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {"kind": self.kind, "scan_date": self.scan_date, "as_of": self.as_of, "strategies": self.strategies,
                "rows_written": self.rows_written, "errors": self.errors, "elapsed_s": self.elapsed_s}

    def summary(self) -> str:
        lines = [f"{'🌅' if self.kind == 'morning' else '🕦'} Scan {self.kind} {self.scan_date} @ {self.as_of} ET"]
        for s in self.strategies:
            top = ", ".join(f"{t['symbol']} ({t.get('gap_pct') if t.get('gap_pct') is not None else '?'}% · rv {t.get('relvol') if t.get('relvol') is not None else '?'})"
                            for t in s.get("top", [])[:5])
            lines.append(f"{s['code']}: {s['passed']} passed / {s['evaluated']} evaluated" + (f" → {top}" if top else ""))
        if self.errors:
            lines.append(f"⚠️ {len(self.errors)} error(s): {self.errors[0][:120]}")
        return "\n".join(lines)


def _write_scans(rows: list[dict]) -> int:
    if not rows:
        return 0
    client = get_client()
    n = 0
    for i in range(0, len(rows), 500):
        client.table("scans").upsert(rows[i:i + 500], on_conflict="strategy_id,scan_date,symbol,run_kind").execute()
        n += len(rows[i:i + 500])
    return n


def run_morning(*, now: dt.datetime | None = None, statuses: tuple[str, ...] = SCANNED_STATUSES,
                strategy_codes: list[str] | None = None, notify: bool = True, dry_run: bool = False,
                max_candidates: int = 3000) -> ScanResult:
    import time

    t0 = time.time()
    now = now or alpaca.now_et()
    day = now.date()
    res = ScanResult(kind="morning", scan_date=day.isoformat(), as_of=now.strftime("%H:%M"))
    cfg_feed = md_repo.fetch_setting("data_feed", {}) or {}
    feed = cfg_feed.get("premarket_volume", "sip")
    checkpoints = tuple(cfg_feed.get("premarket_checkpoints") or DEFAULT_CHECKPOINTS)
    baseline_days = int(cfg_feed.get("baseline_days", 14))

    try:
        strategies = [(sid, c) for sid, c in load_strategies(statuses) if is_scannable(c)
                      and (not strategy_codes or c.code in strategy_codes)]
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"strategies: {type(e).__name__}: {str(e)[:160]}")
        return res
    if not strategies:
        res.errors.append("no scannable strategies in statuses " + ",".join(statuses))
        return res

    # stage A per strategy
    stage: dict[str, tuple[dict[str, dict], dict]] = {}
    all_stats: dict[str, dict] = {}
    try:
        for sid, cfg in strategies:
            u = cfg.universe
            if isinstance(u, WatchlistUniverse):
                st = load_symbol_stats(symbols=u.symbols)
                stage[cfg.code] = (st, {"candidates": len(st), "unknown_float": 0})
            else:
                p = u.params
                st = load_symbol_stats(price_min=p.get("price_min"), price_max=p.get("price_max"),
                                       adv_min=p.get("adv_min_shares"), atr_min=p.get("atr_min_usd"))
                stage[cfg.code] = stage_a_filter(p, st)
            all_stats.update(stage[cfg.code][0])
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"symbol_stats: {type(e).__name__}: {str(e)[:160]}")
        return res
    union = sorted(all_stats)
    if len(union) > max_candidates:
        # keep the most liquid; the rest cannot realistically pass a gap+volume screen anyway
        union = sorted(sorted(union, key=lambda s: -(all_stats[s].get("adv20") or 0))[:max_candidates])

    # stage B: pre-market bars for the union (one bulk pass), then gap filter per strategy
    try:
        pm = premarket_frames(union, day, now, feed)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"premarket bars: {type(e).__name__}: {str(e)[:160]}")
        return res
    as_of_t = min(now.time(), PREMARKET_END)
    if feed == "sip" and pm:
        latest = max(df.index[-1] for df in pm.values())
        if latest.time() < as_of_t:
            as_of_t = (latest + dt.timedelta(minutes=1)).time()  # delayed feed: only the window we actually have

    survivors: dict[str, list[str]] = {}
    for sid, cfg in strategies:
        cands, _ = stage[cfg.code]
        if isinstance(cfg.universe, WatchlistUniverse):
            survivors[cfg.code] = sorted(cands)
            continue
        keep = []
        for sym, st in cands.items():
            df = pm.get(sym)
            px = float(df["close"].iloc[-1]) if df is not None and not df.empty else None
            gap = (px / float(st["prev_close"]) - 1) * 100 if px and st.get("prev_close") else None
            if gap_filter(cfg.universe.params, gap):
                keep.append(sym)
        survivors[cfg.code] = sorted(keep)
    need = sorted({s for v in survivors.values() for s in v})

    # context for survivors: daily bars, baselines, shares outstanding (budgeted)
    daily: dict[str, pd.DataFrame] = {}
    baselines: dict[str, list[dict]] = {}
    try:
        if need:
            daily = daily_frames(need, day)
            baselines = baseline_rows_bulk(need, feed, day)
            wants_float = [s for s in need if any(
                isinstance(c.universe, ScreenerUniverse) and c.universe.params.get("float_max_shares") is not None
                and s in survivors.get(c.code, []) for _, c in strategies)]
            if wants_float and not dry_run:
                so = universe_job.enrich_shares_outstanding(wants_float)
                for s, v in so.items():
                    if v:
                        all_stats[s]["shares_outstanding"] = v
                        all_stats[s].setdefault("float_source", "fmp_shares_outstanding")
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"context: {type(e).__name__}: {str(e)[:160]}")

    rows: list[dict] = []
    run_at = now.isoformat()
    for sid, cfg in strategies:
        evals: list[SymbolEval] = []
        for sym in survivors.get(cfg.code, []):
            st = all_stats.get(sym, {})
            # screener float cap re-checked now that shares outstanding may be known
            if isinstance(cfg.universe, ScreenerUniverse):
                fmax = cfg.universe.params.get("float_max_shares")
                fl = st.get("float_shares") or st.get("shares_outstanding")
                if fmax is not None and fl is not None and fl > fmax:
                    evals.append(SymbolEval(sym, {"float_shares": fl, "float_source": st.get("float_source")}, [],
                                            {"passed": False, "failed": ["float cap"], "pending": [], "approximate": []},
                                            0.0, False, f"float {fl:,} > {fmax:,}"))
                    continue
            evals.append(evaluate_symbol(cfg, sym, stats=st, pm=pm.get(sym), daily=daily.get(sym),
                                         baseline_rows=baselines.get(sym, []), feed=feed, checkpoints=checkpoints,
                                         as_of=as_of_t, baseline_days_default=baseline_days))
        passed = sorted([e for e in evals if e.passed], key=lambda e: -e.score)
        for rank, e in enumerate(passed[:MAX_PASSED_PER_STRATEGY], start=1):
            rows.append({"strategy_id": sid, "run_at": run_at, "scan_date": day.isoformat(), "run_kind": "morning",
                         "symbol": e.symbol, "metrics": e.metrics | {"score": e.score}, "rank": rank, "passed": True})
        for e in [x for x in evals if not x.passed]:
            rows.append({"strategy_id": sid, "run_at": run_at, "scan_date": day.isoformat(), "run_kind": "morning",
                         "symbol": e.symbol, "metrics": e.metrics | {"score": e.score}, "rank": None, "passed": False,
                         "reject_reason": e.reject_reason})
        res.strategies.append({
            "code": cfg.code, "strategy_id": sid, "universe": cfg.universe.type, "stage_a": stage[cfg.code][1],
            "gap_survivors": len(survivors.get(cfg.code, [])), "evaluated": len(evals), "passed": len(passed),
            "top": [{"symbol": e.symbol, "gap_pct": e.metrics.get("gap_pct"), "relvol": e.metrics.get("relvol"),
                     "pct_of_adv": e.metrics.get("pct_of_adv"), "score": e.score,
                     "pending": e.metrics.get("pending_gates")} for e in passed[:10]],
            "rejects_sample": [{"symbol": e.symbol, "reason": e.reject_reason} for e in evals if not e.passed][:5],
        })
    if not dry_run:
        try:
            res.rows_written = _write_scans(rows)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"scans write: {type(e).__name__}: {str(e)[:160]}")
    else:
        res.rows_written = len(rows)
    res.elapsed_s = round(time.time() - t0, 1)
    if notify:
        send_telegram(res.summary())
    return res


def run_midday(*, now: dt.datetime | None = None, statuses: tuple[str, ...] = SCANNED_STATUSES, notify: bool = True,
               dry_run: bool = False) -> ScanResult:
    """F-A midday re-scan: flag today's names already exceeding full-day ADV or ATR (plan §7 S3, §8)."""
    import time

    t0 = time.time()
    now = now or alpaca.now_et()
    day = now.date()
    res = ScanResult(kind="midday", scan_date=day.isoformat(), as_of=now.strftime("%H:%M"))
    try:
        strategies = [(sid, c) for sid, c in load_strategies(statuses) if is_scannable(c)]
        morning = (get_client().table("scans").select("strategy_id,symbol").eq("scan_date", day.isoformat())
                   .eq("run_kind", "morning").eq("passed", True).execute().data or [])
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"load: {type(e).__name__}: {str(e)[:160]}")
        return res
    by_strat: dict[str, set[str]] = {}
    for r in morning:
        by_strat.setdefault(r["strategy_id"], set()).add(r["symbol"])
    for sid, cfg in strategies:
        if isinstance(cfg.universe, WatchlistUniverse):
            by_strat.setdefault(sid, set()).update(cfg.universe.symbols)
    need = sorted({s for v in by_strat.values() for s in v})
    if not need:
        res.errors.append("nothing to re-scan (no morning passes, no watchlists)")
        return res
    try:
        stats = load_symbol_stats(symbols=need)
        reg = regular_frames(need, day, now, "sip")
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"data: {type(e).__name__}: {str(e)[:160]}")
        return res
    rows: list[dict] = []
    run_at = now.isoformat()
    for sid, cfg in strategies:
        syms = sorted(by_strat.get(sid, set()))
        flagged = []
        for sym in syms:
            st = stats.get(sym, {})
            df = reg.get(sym)
            vol = int(df["volume"].sum()) if df is not None and not df.empty else 0
            rng = float(df["high"].max() - df["low"].min()) if df is not None and not df.empty else 0.0
            adv20, atr14 = st.get("adv20") or 0, float(st.get("atr14") or 0)
            m = {"volume_so_far": vol, "adv20": adv20, "pct_of_adv": round(vol / adv20 * 100, 2) if adv20 else None,
                 "range_so_far": round(rng, 4), "atr14": atr14, "range_atr": round(rng / atr14, 3) if atr14 else None,
                 "last": float(df["close"].iloc[-1]) if df is not None and not df.empty else None,
                 "exceeds_adv": bool(adv20 and vol >= adv20), "exceeds_atr": bool(atr14 and rng >= atr14),
                 "prints": len(df) if df is not None else 0}
            passed = m["exceeds_adv"] or m["exceeds_atr"]
            if passed:
                flagged.append({"symbol": sym, "pct_of_adv": m["pct_of_adv"], "range_atr": m["range_atr"]})
            rows.append({"strategy_id": sid, "run_at": run_at, "scan_date": day.isoformat(), "run_kind": "midday",
                         "symbol": sym, "metrics": m, "rank": None, "passed": passed,
                         "reject_reason": None if passed else "below full-day ADV and ATR"})
        res.strategies.append({"code": cfg.code, "strategy_id": sid, "evaluated": len(syms), "passed": len(flagged),
                               "top": [{"symbol": f["symbol"], "gap_pct": None, "relvol": f["pct_of_adv"]} for f in flagged[:10]]})
    if not dry_run:
        try:
            res.rows_written = _write_scans(rows)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"scans write: {type(e).__name__}: {str(e)[:160]}")
    else:
        res.rows_written = len(rows)
    res.elapsed_s = round(time.time() - t0, 1)
    if notify:
        send_telegram(res.summary())
    return res
