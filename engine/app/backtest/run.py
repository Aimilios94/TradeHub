"""The backtest orchestrator (plan §13) — universe replay, event loop, metrics, verdict, `backtests` row.

What a run does, in order:
  1. load the strategy config and refuse, with the reason, anything that has no `backtest` block;
  2. build the replay universe and its coverage stats (countermeasure 1);
  3. read the IEX↔SIP calibration factor and rescale relative-volume thresholds (countermeasure 2);
  4. for every session in the range, screen the universe on that morning's information only, cap the
     focus list at the strategy's own `max_concurrent`, and replay each survivor bar by bar through
     `engine.run_symbol_day` with the explicit fill model (countermeasure 3);
  5. score the result, run the walk-forward split, and grade it against `promotion_criteria`
     (countermeasure 4) — where win rate is displayed and never consulted;
  6. write the whole thing to `backtests`, including the parameters, the coverage and the warnings,
     so a number can always be traced back to the assumptions that produced it.

Nothing here changes a strategy. A `pass` verdict is evidence for a promotion the owner still has to
make — Hermes proposes, the backtester disposes, the database remembers.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

import pandas as pd

from app.backtest import bars as bar_store
from app.backtest import engine, replay
from app.backtest import metrics as M
from app.backtest.fills import FillModel
from app.db import get_client
from app.marketdata import alpaca
from app.marketdata import repo as md_repo
from app.models import BacktestSpec, ScreenerUniverse, StrategyConfig
from app.notify import send_telegram

log = logging.getLogger(__name__)

DEFAULT_EQUITY = 25_000.0
BENCHMARK = "SPY"


@dataclass
class RunResult:
    strategy: str
    strategy_id: str | None
    date_from: str
    date_to: str
    status: str = "done"
    error: str | None = None
    trades: list[dict] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    verdict: str | None = None
    verdict_detail: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    calibration: dict = field(default_factory=dict)
    fill_model: dict = field(default_factory=dict)
    config_snapshot: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    walk_forward: dict = field(default_factory=dict)
    backtest_id: str | None = None
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["trades"] = self.trades
        return d

    def summary(self) -> str:
        if self.status != "done":
            return f"🧪 Backtest {self.strategy} {self.date_from}→{self.date_to}: {self.status} — {self.error}"
        m = self.metrics
        if not m.get("trades"):
            return (f"🧪 Backtest {self.strategy} {self.date_from}→{self.date_to}: NO TRADES.\n"
                    f"{self.diagnostics.get('funnel_note', '')}")
        lines = [
            f"🧪 Backtest {self.strategy} {self.date_from}→{self.date_to} — verdict: {self.verdict.upper()}",
            (f"{m['trades']} trades · expectancy {m['expectancy']:+.2f}/trade · profit factor "
             f"{m.get('profit_factor')} · max DD {m.get('max_drawdown')}"),
            f"win rate {m.get('win_rate_pct')}% (information only) · total {m.get('total_pnl'):+.2f}",
        ]
        if self.verdict_detail.get("failed"):
            lines.append("failed: " + ", ".join(self.verdict_detail["failed"]))
        if self.verdict_detail.get("unknown"):
            lines.append("unanswered: " + ", ".join(self.verdict_detail["unknown"]))
        for w in (self.coverage.get("warnings") or [])[:2]:
            lines.append("⚠️ " + w)
        for f in (m.get("flags") or [])[:2]:
            lines.append("⚠️ " + f)
        return "\n".join(lines)


# ---------- loading ----------

def load_strategy(code: str | None = None, strategy_id: str | None = None) -> tuple[str | None, StrategyConfig]:
    """From Supabase when it is reachable, else from the seed source — and the result says which."""
    try:
        q = get_client().table("strategy_configs").select("*")
        q = q.eq("id", strategy_id) if strategy_id else q.eq("code", code)
        rows = q.execute().data or []
        if rows:
            r = rows[0]
            cfg = StrategyConfig(**{k: r[k] for k in StrategyConfig.model_fields if k in r and r[k] is not None})
            return r["id"], cfg
    except Exception as e:  # noqa: BLE001
        log.warning("strategy_configs unreachable (%s) — falling back to the seed source", e)
    from app.seed import strategies as seed

    for s in seed.all_strategies():
        if s.code == code:
            return None, s
    raise KeyError(f"unknown strategy {code or strategy_id}")


def _fill_model(overrides: dict | None) -> FillModel:
    base = md_repo.fetch_setting("backtest", {}) or {}
    params = dict(base.get("fill_model") or {})
    params.update(overrides or {})
    known = {f for f in FillModel.__dataclass_fields__}
    return FillModel(**{k: v for k, v in params.items() if k in known})


def merge_config(base: dict, diff: dict) -> dict:
    """Apply a hypothesis's config diff to a strategy config.

    Nested dicts merge KEY BY KEY. A shallow `base | diff` would replace a whole block, so a proposal
    that only wants to tighten `risk.risk_per_trade_pct` would silently drop `max_concurrent`,
    `daily_loss_limit_r` and the rest back to their defaults — quietly loosening limits the proposal
    never mentioned, which is precisely what plan §20 forbids.

    Lists replace wholesale on purpose: an exit chain is ordered, and merging it item by item would
    build a chain nobody proposed.
    """
    out = dict(base)
    for k, v in (diff or {}).items():
        out[k] = merge_config(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _sessions_in(frames: dict[str, pd.DataFrame]) -> list[dt.date]:
    days: set[dt.date] = set()
    for df in frames.values():
        days |= set(pd.Series(df.index.date).unique().tolist())
    return sorted(days)


# ---------- the run ----------

def run_backtest(code: str, date_from: dt.date, date_to: dt.date, *, strategy_id: str | None = None,
                 fill_model_overrides: dict | None = None, equity: float = DEFAULT_EQUITY,
                 universe_cap: int = 1500, include_inactive: bool = True, use_cache: bool = True,
                 feed: str = "sip", persist: bool = True, notify: bool = False,
                 hypothesis_id: str | None = None, config_override: dict | None = None,
                 label: str | None = None) -> RunResult:
    t0 = time.time()
    sid, cfg = load_strategy(code, strategy_id)
    if config_override:
        cfg = StrategyConfig(**merge_config(cfg.model_dump(mode="python"), config_override))
    res = RunResult(strategy=label or cfg.code, strategy_id=sid, date_from=date_from.isoformat(),
                    date_to=date_to.isoformat(), config_snapshot=cfg.model_dump(mode="json"))

    spec: BacktestSpec | None = cfg.backtest
    if spec is None:
        res.status = "skipped"
        res.error = (f"{cfg.code} has no `backtest` block: it declares no deterministic entry rule, so "
                     "there is nothing to replay. Strategies whose only output is a briefing (S2) are "
                     "meant to be in this state.")
        res.elapsed_s = round(time.time() - t0, 2)
        return res

    fm = _fill_model(fill_model_overrides)
    res.fill_model = fm.as_dict()
    criteria = dict(md_repo.fetch_setting("promotion_criteria", {}) or {})
    criteria["_equity"] = equity
    fee_model = md_repo.fetch_setting("fee_model", {}) or {}
    risk_dollars = round(equity * cfg.risk.risk_per_trade_pct / 100.0, 2)

    # --- universe + coverage (countermeasure 1) ---
    try:
        symbols, cov = replay.universe_symbols(cfg, cap=universe_cap, include_inactive=include_inactive)
    except Exception as e:  # noqa: BLE001
        res.status = "error"
        res.error = f"could not build the replay universe: {e}"
        res.elapsed_s = round(time.time() - t0, 2)
        return res
    is_screener = isinstance(cfg.universe, ScreenerUniverse)

    # --- calibration (countermeasure 2) ---
    cal = replay.feed_calibration("premarket")
    day_conds, bar_conds, unsupported = engine.split_conditions(cfg)
    day_conds, cal_notes = replay.scale_relvol(day_conds, cal.get("factor"))
    cal["applied_to"] = cal_notes
    res.calibration = cal

    # --- data ---
    warm_from = date_from - dt.timedelta(days=400)
    daily, daily_stats = bar_store.get(symbols, warm_from, date_to, timeframe="1Day", feed=feed,
                                       use_cache=use_cache)
    cov.with_daily_bars = len(daily)
    cov.without_daily_bars = len(symbols) - len(daily)
    bench, _ = bar_store.get([BENCHMARK], warm_from, date_to, timeframe="1Day", feed=feed, use_cache=use_cache)
    regimes = M.regime_map(bench.get(BENCHMARK)) if bench.get(BENCHMARK) is not None else {}

    trading_days = [d for d in _sessions_in(daily) if date_from <= d <= date_to]
    max_focus = min(spec.max_symbols_per_day, cfg.risk.max_concurrent)

    all_trades: list[engine.BacktestTrade] = []
    funnel = {"sessions": len(trading_days), "stage_a": 0, "screened": 0, "candidates": 0,
              "replayed": 0, "no_intraday_bars": 0, "loss_limit_days": 0}
    notes: list[str] = []
    if unsupported:
        notes.append("hard gates the replay cannot evaluate and did NOT treat as passed: "
                     + ", ".join(c.detector for c in unsupported))

    pm_baselines: dict[str, dict[dt.date, int]] = {}

    for day in trading_days:
        # stage A: the cheap local filter, on stats computed from bars up to the previous session
        stage_a: list[str] = []
        for s, df in daily.items():
            st = replay.stats_as_of(df, day)
            if st is None:
                continue
            if is_screener:
                kept, _ = replay.stage_a_filter(cfg.universe.params, {s: st})
                if not kept:
                    continue
            stage_a.append(s)
        funnel["stage_a"] += len(stage_a)
        if not stage_a:
            continue

        intraday, _ = bar_store.day_frames(stage_a, day, timeframe=spec.bar_timeframe, feed=feed,
                                           use_cache=use_cache)
        if not intraday:
            funnel["no_intraday_bars"] += 1
            continue

        candidates: list[replay.DayCandidate] = []
        for s in stage_a:
            df = intraday.get(s)
            if df is None or df.empty:
                continue
            pm, session = replay.split_session(df)
            pm_baselines.setdefault(s, {})[day] = int(pm["volume"].sum()) if not pm.empty else 0
            avg, used = replay.premarket_baseline(pm_baselines[s], day)
            funnel["screened"] += 1
            c = replay.screen_day(cfg, s, day, daily=daily[s], pm=pm, session=session, baseline_avg=avg,
                                  baseline_days=used, day_conds=day_conds,
                                  opening_range_minutes=spec.opening_range_minutes)
            if c is not None:
                candidates.append(c)
        candidates.sort(key=lambda c: -c.score)
        focus = candidates[:max_focus]
        funnel["candidates"] += len(focus)

        day_r = 0.0
        for c in focus:
            if day_r <= -cfg.risk.daily_loss_limit_r:
                funnel["loss_limit_days"] += 1
                break
            df = intraday.get(c.symbol)
            sym_res = engine.run_symbol_day(cfg, c.symbol, df, spec=spec, fill_model=fm,
                                            fee_model=fee_model, day_levels=c.day_levels,
                                            risk_dollars=risk_dollars, atr_value=c.atr)
            funnel["replayed"] += 1
            for t in sym_res.trades:
                all_trades.append(t)
                day_r += t.r_multiple or 0.0

    trades = [t.as_dict() for t in all_trades]
    res.trades = trades
    min_trades = int(criteria.get("min_trades_swing" if cfg.style == "swing" else "min_trades_day", 100))
    res.metrics = M.compute(trades, min_trades=min_trades, regimes=regimes)
    res.walk_forward = M.walk_forward(trades, min_trades=min_trades)
    v, detail = M.verdict(res.metrics, criteria, style=str(cfg.style), walk=res.walk_forward)
    res.verdict, res.verdict_detail = v, detail
    res.coverage = cov.finish(expect_delisted=is_screener and include_inactive).as_dict()
    res.diagnostics = {
        "funnel": funnel, "notes": notes, "bars": daily_stats,
        "parquet": bar_store.cache_stats(),
        "entry_rule": spec.entry_rule, "bar_timeframe": spec.bar_timeframe,
        "risk_dollars_per_trade": risk_dollars, "equity_sized_against": equity,
        "max_focus_per_day": max_focus,
        "funnel_note": _funnel_note(funnel, bar_conds),
        "direction_note": ("this strategy trades both ways; the replay takes the LONG side only, so a "
                           "short-side edge is neither counted nor claimed"
                           if cfg.entry_conditions.direction == "both" else None),
    }
    res.elapsed_s = round(time.time() - t0, 2)

    if persist:
        try:
            res.backtest_id = write_row(res, cfg, hypothesis_id=hypothesis_id, feed=feed)
        except Exception as e:  # noqa: BLE001
            log.warning("could not write the backtests row: %s", e)
            res.diagnostics["persist_error"] = str(e)
    if notify:
        send_telegram(res.summary())
    return res


def _funnel_note(f: dict, bar_conds: list) -> str:
    if f["stage_a"] == 0:
        return "no symbol passed the screener's price/liquidity filter on any session — check the universe params."
    if f["screened"] == 0:
        return "no intraday bars were returned for any stage-A survivor; the range may predate the data or the feed may be wrong."
    if f["candidates"] == 0:
        return "symbols had bars but none cleared the day-level hard gates. That is a real answer: on this history the screener found nothing."
    if f["replayed"] and not bar_conds:
        return "candidates were replayed but produced no trades — the entry rule never triggered inside the entry window."
    return "candidates were replayed but the bar-level entry gates never aligned inside the entry window."



def json_safe(value):
    """Replace non-finite floats with None, recursively.

    Postgres JSON has no `inf` or `nan`, so one of them anywhere in a metrics tree rejects the whole
    `backtests` insert — losing the entire run to a log line, which is the opposite of what plan §33
    asks for. The values that can be non-finite here (a profit factor with no losers, a ratio over an
    empty sample) all mean "not computable", and None says that.
    """
    import math

    if isinstance(value, float):
        return None if (math.isinf(value) or math.isnan(value)) else value
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


# ---------- persistence ----------

def write_row(res: RunResult, cfg: StrategyConfig, *, hypothesis_id: str | None, feed: str,
              ab_against: str | None = None) -> str | None:
    if not res.strategy_id:
        log.info("no strategy_id (seed fallback) — the backtests row is not written")
        return None
    row = {
        "strategy_id": res.strategy_id,
        "hypothesis_id": hypothesis_id,
        "config_snapshot": res.config_snapshot,
        "date_from": res.date_from, "date_to": res.date_to, "feed": feed,
        "fill_model": res.fill_model,
        "metrics": res.metrics | {"walk_forward": res.walk_forward, "diagnostics": res.diagnostics,
                                  "trades_sample": res.trades[:50]},
        "coverage_stats": res.coverage,
        "calibration": res.calibration,
        "verdict": res.verdict, "verdict_detail": res.verdict_detail,
        "ab_against_backtest_id": ab_against,
        "status": res.status, "error": res.error,
        "started_at": dt.datetime.now(alpaca.ET).isoformat(),
        "finished_at": dt.datetime.now(alpaca.ET).isoformat(),
    }
    out = get_client().table("backtests").insert(json_safe(row)).execute()
    return (out.data or [{}])[0].get("id")


# ---------- A/B ----------

def run_ab(code_a: str, code_b: str, date_from: dt.date, date_to: dt.date, **kw) -> dict:
    """Two strategies over the same range, same fill model, same universe rules (plan §13, S6 vs S7)."""
    a = run_backtest(code_a, date_from, date_to, **kw)
    b = run_backtest(code_b, date_from, date_to, **kw)
    cmp = M.compare(a.metrics, b.metrics, label_a=code_a, label_b=code_b)
    if a.backtest_id and b.backtest_id:
        try:
            get_client().table("backtests").update({"ab_against_backtest_id": b.backtest_id}) \
                .eq("id", a.backtest_id).execute()
        except Exception as e:  # noqa: BLE001
            log.warning("could not link the A/B rows: %s", e)
    return {"comparison": cmp, code_a: a.as_dict(), code_b: b.as_dict()}


def run_all(date_from: dt.date, date_to: dt.date, codes: list[str] | None = None, **kw) -> dict:
    """Baselines for every strategy (plan §17 acceptance criterion 1). Skips are reported, not hidden."""
    from app.seed import strategies as seed

    wanted = codes or [s.code for s in seed.all_strategies()]
    out: dict = {"from": date_from.isoformat(), "to": date_to.isoformat(), "runs": [], "skipped": []}
    for c in wanted:
        try:
            r = run_backtest(c, date_from, date_to, **kw)
        except Exception as e:  # noqa: BLE001
            out["runs"].append({"strategy": c, "status": "error", "error": str(e)})
            continue
        if r.status == "skipped":
            out["skipped"].append({"strategy": c, "reason": r.error})
            continue
        out["runs"].append({"strategy": c, "verdict": r.verdict, "trades": r.metrics.get("trades", 0),
                            "expectancy": r.metrics.get("expectancy"),
                            "profit_factor": r.metrics.get("profit_factor"),
                            "win_rate_pct": r.metrics.get("win_rate_pct"),
                            "max_drawdown": r.metrics.get("max_drawdown"),
                            "coverage_warnings": r.coverage.get("warnings"),
                            "backtest_id": r.backtest_id, "elapsed_s": r.elapsed_s})
    return out
