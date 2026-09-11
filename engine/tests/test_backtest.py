"""The backtester (plan §13): fill model, event loop, metrics, verdict, and the four countermeasures.

The tests that matter here are the pessimistic ones. A backtester is not judged on whether it can
produce a number — it is judged on whether it refuses the free money that a careless one hands out:
a limit filled on a touch, an ambiguous bar resolved the flattering way, a universe with no delisted
symbols in it, a verdict passed on an unanswered check.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.backtest import engine, replay
from app.backtest import fills as F
from app.backtest import metrics as M
from app.backtest.engine import BacktestTrade
from app.backtest.fills import FillModel
from app.models import BacktestSpec, StrategyConfig
from app.seed import strategies as seed
from tests.conftest import frame

FM = FillModel()
FEES = {"sec_fee_per_dollar": 0.0000278, "taf_per_share": 0.000166, "taf_max": 8.30}


def bar(o, h, l, c, v=1_000_000) -> pd.Series:
    return pd.Series({"open": float(o), "high": float(h), "low": float(l), "close": float(c),
                      "volume": float(v)})


# =========================================================================================
# countermeasure 3 — fill realism
# =========================================================================================

def test_a_limit_that_was_only_touched_is_not_filled():
    """Plan §31. Price coming down to your level and turning is the losing half of the distribution;
    filling it is how a backtest collects the winners it would never have got."""
    touched = bar(101, 101.5, 100.0, 101)         # low == the limit exactly
    assert F.limit_fill(FM, touched, side="buy", price=100.0, qty=100) is None

    through = bar(101, 101.5, 99.0, 100.5)        # traded well below it, with volume
    fill = F.limit_fill(FM, through, side="buy", price=100.0, qty=100)
    assert fill is not None and fill.price == 100.0


def test_a_limit_is_refused_when_too_little_volume_traded_through_it():
    thin = bar(101, 101.2, 99.9, 100.0, v=50)     # through the level, but only 50 shares printed
    assert F.limit_fill(FM, thin, side="buy", price=100.0, qty=1_000) is None


def test_a_fill_cannot_exceed_a_share_of_the_bars_volume():
    b = bar(101, 101.5, 99.0, 100.5, v=1_000)
    fill = F.limit_fill(FM, b, side="buy", price=100.0, qty=10_000)
    assert fill is not None
    assert fill.qty == 100, "10% of a 1,000-share bar"
    assert fill.partial is True and fill.requested_qty == 10_000


def test_a_stop_fills_through_the_level_never_at_it():
    b = bar(100, 100.5, 97.0, 98.0)
    fill = F.stop_fill(FM, b, side="sell", stop=99.0, qty=100)
    assert fill is not None
    assert fill.price < 99.0, "a stop is not a limit"
    assert fill.slippage > 0


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop():
    """The market never offered your level. Filling at the stop is the single most flattering lie a
    backtest can tell about a gapping low-float name."""
    gapped = bar(90.0, 91.0, 88.0, 89.0)          # opened $9 below a $99 stop
    fill = F.stop_fill(FM, gapped, side="sell", stop=99.0, qty=100)
    assert fill is not None
    assert fill.price == pytest.approx(90.0, abs=0.01)
    assert "gapped" in fill.reason


def test_a_bar_that_touched_both_the_stop_and_the_target_is_booked_as_a_stop():
    both = bar(100, 106, 94, 100)
    assert F.resolve_bar(both, long=True, stop=95.0, target=105.0) == "both"
    assert F.intrabar_outcome(both, long=True, stop=95.0, target=105.0) == "stop"


def test_the_spread_is_wider_on_a_cheap_stock_than_on_an_expensive_one():
    cheap = FM.half_spread(3.0)
    rich = FM.half_spread(300.0)
    assert cheap / 3.0 > rich / 300.0, "a $3 low-float name is not a $300 mega-cap"


def test_regulatory_fees_are_charged_on_sells_only():
    assert F.regulatory_fees(FEES, side="buy", price=100.0, qty=100) == 0.0
    assert F.regulatory_fees(FEES, side="sell", price=100.0, qty=100) > 0.0


def test_the_fill_model_is_reported_with_the_run():
    d = FM.as_dict()
    for key in ("spread_model", "slippage_ticks", "limit_requires_through", "queue_volume_multiple",
                "max_participation_pct", "stop_slippage_pct"):
        assert key in d, f"{key} moves the P&L and must be visible in the result"


# =========================================================================================
# the event loop
# =========================================================================================

def orb_strategy(**over) -> StrategyConfig:
    """S3 with the entry window widened, so a hand-built session can exercise the loop."""
    cfg = seed.S3.model_copy(deep=True)
    cfg.backtest = BacktestSpec(**(cfg.backtest.model_dump() | {"entry_window": "09:30-15:55",
                                                               "opening_range_minutes": 10} | over))
    cfg.entry_conditions.all = []            # day-level gates are the replay's job, not the loop's
    return cfg


def session(closes, **kw) -> pd.DataFrame:
    return frame(closes, start=(9, 30), minutes=5, volume=5_000_000, **kw)


def test_an_opening_range_break_opens_a_position_and_a_stop_closes_it():
    cfg = orb_strategy()
    closes = [100, 101, 104, 103, 99, 98]
    bars = session(closes, highs=[101, 102, 105, 104, 100, 99], lows=[99, 100, 102, 102, 96, 95])
    levels = {"opening_range": {"high": 102.0, "low": 99.0, "minutes": 10}}
    res = engine.run_symbol_day(cfg, "TEST", bars, spec=cfg.backtest, fill_model=FM, fee_model=FEES,
                                day_levels=levels, risk_dollars=500.0, atr_value=2.0)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == "long"
    assert t.entry_price > 102.0, "the buy-stop is above the opening-range high plus slippage"
    assert t.exit_reason.startswith("stop") or t.exit_reason == "end of session"
    assert t.qty > 0


def test_no_stop_means_no_position():
    """Plan sizing rule: a position whose risk cannot be measured is not taken."""
    assert engine.size_for(500.0, 100.0, None, max_notional=100_000) == 0
    assert engine.size_for(500.0, 100.0, 100.0, max_notional=100_000) == 0, "zero risk per share"
    assert engine.size_for(500.0, 100.0, 95.0, max_notional=100_000) == 100


def test_size_respects_the_risk_budget():
    qty = engine.size_for(500.0, 50.0, 45.0, max_notional=1_000_000)
    assert qty * abs(50.0 - 45.0) <= 500.0


def test_the_time_exit_flattens_the_position_before_the_close():
    cfg = orb_strategy(entry_window="09:30-10:30")
    closes = [100, 104] + [104.0] * 70
    bars = session(closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes])
    levels = {"opening_range": {"high": 101.0, "low": 99.0, "minutes": 10}}
    res = engine.run_symbol_day(cfg, "TEST", bars, spec=cfg.backtest, fill_model=FM, fee_model=FEES,
                                day_levels=levels, risk_dollars=500.0, atr_value=2.0)
    assert res.trades, "the break should have triggered"
    t = res.trades[0]
    assert "time exit" in t.exit_reason or t.exit_reason == "end of session"
    assert dt.time(15, 0) <= pd.Timestamp(t.exit_time).time() <= dt.time(16, 0)


def test_a_position_still_open_at_the_end_is_closed_and_labelled():
    cfg = orb_strategy(entry_window="09:30-09:45")
    closes = [100, 104, 105, 106]
    bars = session(closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes])
    levels = {"opening_range": {"high": 101.0, "low": 99.0, "minutes": 5}}
    res = engine.run_symbol_day(cfg, "TEST", bars, spec=cfg.backtest, fill_model=FM, fee_model=FEES,
                                day_levels=levels, risk_dollars=500.0, atr_value=2.0)
    assert res.trades and res.trades[0].exit_reason == "end of session"


def test_max_trades_per_day_is_respected():
    cfg = orb_strategy(entry_window="09:30-15:55", max_trades_per_day=1)
    closes = [100, 104, 99, 104, 99, 104, 99]
    bars = session(closes, highs=[c + 1 for c in closes], lows=[c - 1 for c in closes])
    levels = {"opening_range": {"high": 101.0, "low": 99.0, "minutes": 5}}
    res = engine.run_symbol_day(cfg, "TEST", bars, spec=cfg.backtest, fill_model=FM, fee_model=FEES,
                                day_levels=levels, risk_dollars=500.0, atr_value=2.0)
    assert len(res.trades) <= 1


def test_the_loop_reports_gates_it_cannot_evaluate_rather_than_passing_them():
    cfg = seed.S1.model_copy(deep=True)
    day, bar_level, unsupported = engine.split_conditions(cfg)
    assert {c.detector for c in day} <= engine.DAY_LEVEL_DETECTORS
    assert not unsupported, "S1's hard gates are all day-level"
    assert all(c.detector in engine.BAR_LEVEL_DETECTORS for c in bar_level)


def test_hard_gates_are_split_day_level_versus_bar_level_for_every_seed_strategy():
    for s in seed.all_strategies():
        _day, _bar, unsupported = engine.split_conditions(s)
        assert not unsupported, f"{s.code} has a hard gate the backtester classifies as neither"


def test_pnl_does_not_deduct_the_spread_twice():
    """`pnl_gross` already comes from the fill prices, which contain the spread paid. `spread_cost` is
    an attribution — how much of the gross the spread ate — not a second charge."""
    t = BacktestTrade(symbol="X", strategy="S3", direction="long", entry_time="t0", entry_price=100.0,
                      exit_time="t1", exit_price=110.0, qty=10, pnl_gross=100.0, fees=0.05,
                      spread_cost=3.0, pnl_net=99.95, r_multiple=None, planned_stop=95.0,
                      planned_target=None, exit_reason="target hit", setup_type="x", bars_held=5)
    assert t.pnl_net == pytest.approx(t.pnl_gross - t.fees)
    assert t.pnl_net != pytest.approx(t.pnl_gross - t.fees - t.spread_cost)


# =========================================================================================
# countermeasure 1 — survivorship
# =========================================================================================

def test_a_universe_with_no_delisted_names_is_flagged_as_too_clean():
    cov = replay.Coverage(universe_size=1000, active=1000, inactive=0).finish(expect_delisted=True)
    assert cov.too_clean is True
    assert cov.delisted_pct == 0.0
    assert any("survivorship" in w for w in cov.warnings)


def test_a_universe_with_delisted_names_is_not_flagged():
    cov = replay.Coverage(universe_size=1000, active=900, inactive=100).finish(expect_delisted=True)
    assert cov.too_clean is False
    assert cov.delisted_pct == 10.0


def test_a_watchlist_universe_is_not_asked_for_delistings():
    cov = replay.Coverage(universe_size=8, active=8, inactive=0).finish(expect_delisted=False)
    assert cov.too_clean is False


def test_symbols_without_history_are_reported_not_assumed_to_pass():
    cov = replay.Coverage(universe_size=100, active=100, without_daily_bars=7).finish(expect_delisted=False)
    assert any("no daily history" in w for w in cov.warnings)


# =========================================================================================
# countermeasure 2 — feed mismatch
# =========================================================================================

def test_relative_volume_thresholds_are_rescaled_for_the_history_feed():
    conds = [c for c in seed.S1.entry_conditions.all if c.detector == "relative_volume_premarket"]
    assert conds, "S1 has a relative-volume gate"
    scaled, notes = replay.scale_relvol(conds, 0.5)
    assert scaled[0].value == pytest.approx(2.5)
    assert notes and "IEX" in notes[0]


def test_without_a_calibration_factor_the_threshold_is_left_alone_and_said_so():
    conds = [c for c in seed.S1.entry_conditions.all if c.detector == "relative_volume_premarket"]
    same, notes = replay.scale_relvol(conds, None)
    assert same[0].value == conds[0].value
    assert notes == []


# =========================================================================================
# countermeasure 4 — overfitting guards, and the verdict
# =========================================================================================

def sample_trades(n: int, pnl: float, *, start_year: int = 2024) -> list[dict]:
    out = []
    for i in range(n):
        ts = dt.datetime(start_year, 1, 1, 10, 0, tzinfo=dt.UTC) + dt.timedelta(days=i)
        out.append({"symbol": "X", "strategy": "S3", "direction": "long",
                    "entry_time": ts.isoformat(), "exit_time": (ts + dt.timedelta(hours=1)).isoformat(),
                    "entry_price": 100.0, "exit_price": 100 + pnl, "qty": 1,
                    "pnl_gross": pnl, "fees": 0.0, "spread_cost": 0.0, "pnl_net": pnl,
                    "r_multiple": pnl, "exit_reason": "target hit", "bars_held": 5,
                    "partial_fill": False, "ambiguous_bars": 0})
    return out


CRITERIA = {"require_positive_expectancy_after_costs": True, "min_profit_factor": 1.5,
            "min_trades_day": 100, "min_trades_swing": 40, "max_drawdown_pct": 15.0,
            "must_beat_current_config_ab": False, "_equity": 25_000.0}


def test_a_small_sample_is_inconclusive_not_a_pass():
    m = M.compute(sample_trades(10, 1.0), min_trades=100)
    v, detail = M.verdict(m, CRITERIA, style="day")
    assert v == "inconclusive"
    assert "sample size" in detail["failed"] or "sample size" in detail["unknown"]
    assert not m["sufficient"]
    assert any("provisional" in f for f in m["flags"])


def test_a_losing_strategy_fails_however_many_trades_it_has():
    m = M.compute(sample_trades(150, -1.0), min_trades=100)
    v, detail = M.verdict(m, CRITERIA, style="day")
    assert v == "fail"
    assert "expectancy after costs" in detail["failed"]


def test_win_rate_is_never_a_gate():
    """A 100% win rate with a bad profit factor must not pass, and the verdict must say so."""
    trades = sample_trades(120, 0.01)             # every trade a winner, each worth a cent
    m = M.compute(trades, min_trades=100)
    assert m["win_rate_pct"] == 100.0
    _v, detail = M.verdict(m, CRITERIA, style="day")
    assert "win rate" in detail["note"]
    assert not any("win" in c["check"] for c in detail["checks"])


def test_an_unanswered_check_makes_the_verdict_inconclusive_not_pass():
    m = M.compute(sample_trades(150, 2.0), min_trades=100)
    criteria = CRITERIA | {"must_beat_current_config_ab": True}
    v, detail = M.verdict(m, criteria, style="day")
    assert v == "inconclusive"
    assert detail["unknown"], "the missing A/B must be visible, not silently ignored"
    assert "Unknown is never green" in detail["note"]


def test_a_verdict_can_pass_when_every_check_is_answered():
    m = M.compute(sample_trades(150, 2.0), min_trades=100)
    v, detail = M.verdict(m, CRITERIA, style="day")
    assert v == "pass", detail
    assert not detail["failed"] and not detail["unknown"]


def test_walk_forward_flags_an_edge_that_lived_in_only_one_half():
    good = sample_trades(60, 3.0, start_year=2023)
    bad = sample_trades(60, -3.0, start_year=2024)
    w = M.walk_forward(good + bad, min_trades=40, splits=2)
    assert w["usable"] is True
    assert w["consistent"] is False
    assert w["flags"] and "did not hold" in w["flags"][0]


def test_walk_forward_is_content_when_both_halves_work():
    w = M.walk_forward(sample_trades(60, 2.0, start_year=2023) + sample_trades(60, 2.0, start_year=2024),
                       min_trades=40, splits=2)
    assert w["consistent"] is True and not w["flags"]


def test_walk_forward_refuses_to_split_a_handful_of_trades():
    w = M.walk_forward(sample_trades(3, 1.0), min_trades=100)
    assert w["usable"] is False
    assert "says nothing" in w["note"]


def test_no_trades_is_reported_as_no_trades_not_as_a_zero():
    m = M.compute([], min_trades=100)
    assert m["trades"] == 0
    assert "diagnostics" in m["note"]


def test_ambiguous_bars_are_counted_and_explained():
    trades = sample_trades(5, 1.0)
    trades[0]["ambiguous_bars"] = 4
    m = M.compute(trades, min_trades=5)
    assert m["ambiguous_bars"] == 4
    assert any("too coarse" in f for f in m["flags"])


# ---------- A/B ----------

def test_ab_picks_the_higher_expectancy_and_admits_a_thin_sample():
    a = M.compute(sample_trades(120, 2.0), min_trades=100)
    b = M.compute(sample_trades(120, 1.0), min_trades=100)
    cmp = M.compare(a, b, label_a="S6", label_b="S7")
    assert cmp["winner"] == "S6" and cmp["confident"] is True

    thin = M.compute(sample_trades(4, 5.0), min_trades=100)
    cmp2 = M.compare(thin, b, label_a="S6", label_b="S7")
    assert cmp2["confident"] is False
    assert "small" in cmp2["note"]


def test_ab_uses_expectancy_not_win_rate():
    a = M.compute(sample_trades(100, 0.01), min_trades=100)      # 100% winners, tiny
    b = M.compute(sample_trades(100, 5.0), min_trades=100)       # 100% winners, large
    cmp = M.compare(a, b, label_a="tiny", label_b="large")
    assert cmp["winner"] == "large"


# ---------- regime ----------

def test_regime_map_labels_trend_and_chop():
    up = frame([100 + i for i in range(120)], minutes=1440)
    r = M.regime_map(up)
    assert set(r.values()) <= {"uptrend", "downtrend", "chop"}
    assert list(r.values())[-1] == "uptrend"


# =========================================================================================
# the strategies themselves
# =========================================================================================

def test_every_strategy_but_the_briefing_declares_a_backtest_rule():
    for s in seed.all_strategies():
        if s.code == "S2":
            assert s.backtest is None, "a briefing has nothing to replay"
        else:
            assert s.backtest is not None, f"{s.code} cannot be backtested — plan §17 needs all eight"
            assert s.backtest.notes, f"{s.code} must say what its entry rule stands in for"


def test_the_ab_pair_differs_only_where_the_strategies_differ():
    """S6 vs S7 must measure the strategies, not the fill rules (plan §13)."""
    a, b = seed.S6.backtest.model_dump(), seed.S7.backtest.model_dump()
    differing = {k for k in a if a[k] != b[k]}
    assert differing <= {"max_trades_per_day", "notes"}, differing


def test_the_semi_auto_strategies_do_not_fill_on_the_signal_bar():
    """A human confirms the channel before the tap; that cannot happen inside the bar that fired."""
    for s in seed.all_strategies():
        if s.automation_mode == "semi_auto" and s.backtest:
            assert s.backtest.entry_rule == "next_bar_open", s.code


def test_a_config_diff_merges_nested_blocks_instead_of_replacing_them():
    """A proposal that tightens one risk field must not reset the others to their defaults — that
    would loosen limits the proposal never mentioned (plan §20)."""
    from app.backtest.run import merge_config

    base = seed.S3.model_dump(mode="python")
    merged = merge_config(base, {"risk": {"risk_per_trade_pct": 0.25}})
    assert merged["risk"]["risk_per_trade_pct"] == 0.25
    assert merged["risk"]["max_concurrent"] == base["risk"]["max_concurrent"]
    assert merged["risk"]["daily_loss_limit_r"] == base["risk"]["daily_loss_limit_r"]
    # a list replaces wholesale: an exit chain is ordered
    merged2 = merge_config(base, {"exit_logic": [{"plugin": "time_exit", "params": {}}]})
    assert len(merged2["exit_logic"]) == 1


# =========================================================================================
# the "config that can never fire" guard (decision #63)
# =========================================================================================

REGIME_DETECTORS = {"stoch_quad_extreme", "stoch_embedded", "flagpole_steepness"}


def _is_trigger(c) -> bool:
    """A condition that fires at one instant: the fast stochastic reaching an extreme, or a
    divergence whose value includes its own trigger."""
    if c.detector == "stoch_divergence":
        return True
    if c.detector == "stochastic" and c.op in (">=", "<=") and isinstance(c.value, (int, float)):
        return c.value >= 70 or c.value <= 30
    return False


def test_no_strategy_pairs_a_regime_gate_with_a_trigger_on_the_same_bar():
    """The trap that hid in four phases of green tests.

    A regime gate (the quad under 20, an embedded band, a flagpole) describes a state that the very
    move producing the trigger destroys: the 9-3 crossing UP through 20 is the moment the quad stops
    being under it. Written as two same-bar hard gates the pair is unsatisfiable, and a strategy that
    can never fire is indistinguishable from one whose setup is merely rare — S4 and S8 sat at exactly
    zero signals until the backtester counted them. A regime gate sharing a strategy with a trigger
    must therefore carry `within_bars`.
    """
    offenders = []
    for s in seed.all_strategies():
        gates = s.entry_conditions.all
        if not any(_is_trigger(c) for c in gates):
            continue
        for c in gates:
            if c.detector in REGIME_DETECTORS and not c.within_bars:
                offenders.append(f"{s.code}: {c.detector} is a same-bar gate alongside a trigger")
    assert not offenders, (
        "these hard gates can never be true at the same instant as their own trigger:\n  "
        + "\n  ".join(offenders))


def test_within_bars_makes_a_gate_a_setup_rather_than_a_trigger():
    from app.models import Condition

    setup = Condition(detector="stoch_quad_extreme", op="true", within_bars=5)
    trigger = Condition(detector="stoch_quad_extreme", op="true")
    bars = frame([100.0] * 30)
    ctx = {"symbol": "X"}

    # a gate that held at bar 0 and not since: the setup version still passes, the trigger version does not
    state = {"0:stoch_quad_extreme": 0}
    ok_setup, _ = engine.bar_gates_pass([setup], bars.iloc[:4], ctx, state=dict(state), index=3)
    assert ok_setup is True
    ok_stale, _ = engine.bar_gates_pass([setup], bars.iloc[:20], ctx, state=dict(state), index=19)
    assert ok_stale is False, "a setup goes stale once it is out of the window"
    ok_trigger, _ = engine.bar_gates_pass([trigger], bars.iloc[:4], ctx, state=dict(state), index=3)
    assert ok_trigger is False, "without within_bars the gate must hold on this very bar"


def test_a_profit_factor_with_no_losers_is_none_not_infinity():
    """`float("inf")` is not JSON. It silently rejected a whole `backtests` insert, and would have
    rejected the weekly-review prompt on the first week with no losing trade."""
    import json

    m = M.compute(sample_trades(20, 1.0), min_trades=5)   # every trade a winner
    assert m["losers"] == 0
    assert m["profit_factor"] is None
    json.dumps(m)          # must not raise


def test_non_finite_values_are_scrubbed_before_the_row_is_written():
    import json

    from app.backtest.run import json_safe

    row = {"metrics": {"profit_factor": float("inf"), "ratio": float("nan"), "ok": 1.5},
           "nested": [{"x": float("-inf")}]}
    safe = json_safe(row)
    assert safe["metrics"]["profit_factor"] is None
    assert safe["metrics"]["ratio"] is None
    assert safe["metrics"]["ok"] == 1.5
    assert safe["nested"][0]["x"] is None
    json.dumps(safe)


def test_one_bad_symbol_does_not_cost_the_whole_batch_its_history(monkeypatch, tmp_path):
    """Alpaca refuses an entire multi-symbol request when one ticker is invalid, and the replay
    universe deliberately contains delisted names. Without bisection a single "HYAC_DELISTED" silently
    strips 99 good symbols of their history — re-biasing the universe the survivorship countermeasure
    exists to protect."""
    from app.backtest import bars as bar_store
    from app.marketdata import alpaca as al

    calls = {"n": 0}

    def fake_get_bars(symbols, start, end, *, feed, timeframe):
        calls["n"] += 1
        if "BAD" in symbols:
            raise ValueError('{"message":"invalid symbol: BAD"}')
        return [al.Bar(symbol=s, ts=start, open=1.0, high=1.0, low=1.0, close=1.0, volume=10,
                       vwap=None, trade_count=None, feed=feed) for s in symbols]

    monkeypatch.setattr(bar_store.alpaca, "get_bars", fake_get_bars)
    monkeypatch.setattr(bar_store, "root", lambda tf, fd: tmp_path)

    got = bar_store.fetch(["AAA", "BAD", "CCC", "DDD"], dt.datetime(2026, 1, 2), dt.datetime(2026, 1, 3),
                          timeframe="1Day", feed="sip")
    assert set(got) == {"AAA", "CCC", "DDD"}, "the good symbols must survive the bad one"
    assert "BAD" in bar_store.load_rejected("1Day", "sip"), "and the offender is remembered"

    # a second call skips the known-bad symbol entirely rather than rediscovering it
    before = calls["n"]
    bar_store.fetch(["AAA", "BAD"], dt.datetime(2026, 1, 2), dt.datetime(2026, 1, 3),
                    timeframe="1Day", feed="sip")
    assert calls["n"] - before == 1
