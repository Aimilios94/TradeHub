"""Phase 6: round-trip matching, the fee/ledger maths, analytics, and the guards on the weekly review.

Offline throughout. The properties that matter here are arithmetic honesty — a number is either computed
from real fills or reported as unknown — and that the feedback loop cannot quietly change a strategy.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analytics import stats
from app.broker import activities as broker_activities
from app.config import get_settings
from app.journal import build as jbuild
from app.journal import ledger as jledger
from app.main import app
from app.review import weekly as weekly_mod

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_settings().engine_api_token}"}
UTC = dt.UTC


def fill(hh: int, mm: int, side: str, qty: int, price: float, oid: str = "o1") -> dict:
    return {"transaction_time": dt.datetime(2026, 9, 10, hh, mm, tzinfo=UTC).isoformat(),
            "side": side, "qty": str(qty), "price": str(price), "order_id": oid, "symbol": "AAPL"}


# ---------- round-trip matching ----------

def test_a_simple_long_round_trip():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(15, 0, "sell", 100, 11.0)])
    closed, still_open = jbuild.match_round_trips(legs)
    assert len(closed) == 1 and not still_open
    t = closed[0]
    assert t.direction == "long" and t.qty == 100
    assert t.entry_price == 10.0 and t.exit_price == 11.0
    assert t.pnl_gross() == 100.0


def test_a_short_round_trip_profits_when_price_falls():
    legs = jbuild.parse_fills([fill(14, 0, "sell", 50, 20.0), fill(15, 0, "buy", 50, 18.0)])
    closed, _ = jbuild.match_round_trips(legs)
    assert closed[0].direction == "short" and closed[0].pnl_gross() == 100.0


def test_scaling_in_averages_the_entry():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(14, 30, "buy", 100, 12.0),
                               fill(15, 0, "sell", 200, 11.0)])
    closed, _ = jbuild.match_round_trips(legs)
    assert len(closed) == 1
    assert closed[0].qty == 200 and closed[0].entry_price == 11.0
    assert closed[0].pnl_gross() == 0.0


def test_a_partial_exit_leaves_the_trip_open():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(15, 0, "sell", 40, 11.0)])
    closed, still_open = jbuild.match_round_trips(legs)
    assert not closed and len(still_open) == 1
    assert still_open[0].qty == 100 and not still_open[0].closed


def test_scaling_out_completes_the_trip_and_averages_the_exit():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(15, 0, "sell", 40, 11.0),
                               fill(15, 30, "sell", 60, 12.0)])
    closed, still_open = jbuild.match_round_trips(legs)
    assert len(closed) == 1 and not still_open
    assert closed[0].exit_price == pytest.approx(11.6)
    assert len(closed[0].exit_legs) == 2


def test_two_separate_trips_in_one_day_are_two_rows():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 10, 10.0), fill(14, 5, "sell", 10, 10.5),
                               fill(15, 0, "buy", 10, 11.0), fill(15, 5, "sell", 10, 10.0)])
    closed, _ = jbuild.match_round_trips(legs)
    assert len(closed) == 2
    assert [round(t.pnl_gross(), 2) for t in closed] == [5.0, -10.0]


def test_flipping_from_long_straight_to_short_splits_the_trips():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(15, 0, "sell", 150, 11.0)])
    closed, still_open = jbuild.match_round_trips(legs)
    assert len(closed) == 1 and closed[0].qty == 100
    assert len(still_open) == 1 and still_open[0].direction == "short" and still_open[0].qty == 50


def test_an_unparseable_fill_is_skipped_not_fatal():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), {"transaction_time": "nonsense"}])
    assert len(legs) == 1


@pytest.mark.parametrize("hh,mm,expected", [
    (8, 0, "premarket"), (9, 30, "09:30-10:30"), (10, 45, "10:30-11:30"),
    (13, 0, "12:30-13:30"), (15, 0, "14:30-16:00"), (17, 0, "afterhours"),
])
def test_time_blocks_match_the_spec(hh, mm, expected):
    when = dt.datetime(2026, 9, 10, hh, mm, tzinfo=jbuild.alpaca.ET)
    assert jbuild.time_block(when) == expected


# ---------- r-multiple, spread, deviations ----------

def test_r_multiple_needs_a_planned_stop():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(15, 0, "sell", 100, 11.0)])
    trip = jbuild.match_round_trips(legs)[0][0]
    assert jbuild.r_multiple(trip, 9.0, 100.0) == pytest.approx(1.0)      # risked 1.00 x 100
    assert jbuild.r_multiple(trip, None, 100.0) is None                   # no plan, no invented R
    assert jbuild.r_multiple(trip, 10.0, 100.0) is None                   # zero-width stop


def test_spread_cost_uses_the_midpoint_recorded_at_submit():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.05, "oA"), fill(15, 0, "sell", 100, 10.95, "oB")])
    trip = jbuild.match_round_trips(legs)[0][0]
    orders = [{"alpaca_order_id": "oA", "quote_mid_at_submit": 10.00},
              {"alpaca_order_id": "oB", "quote_mid_at_submit": 11.00}]
    assert jbuild.spread_cost(trip, orders) == pytest.approx(10.0)        # 0.05 + 0.05, x100
    assert jbuild.spread_cost(trip, []) == 0.0                            # unknown, not guessed


def test_net_pnl_does_not_deduct_the_spread_twice():
    """Regression, found in Phase 7. `pnl_gross` is computed from the ACTUAL fill prices, so the spread
    paid is already inside it; `spread_cost` measures how much of that gross the spread ate. Deducting
    it again understated every net figure and made journal and backtest numbers non-comparable — the
    comparison the promotion workflow (plan §13) is built on."""
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.05, "oA"), fill(15, 0, "sell", 100, 10.95, "oB")])
    trip = jbuild.match_round_trips(legs)[0][0]
    orders = [{"alpaca_order_id": "oA", "quote_mid_at_submit": 10.00},
              {"alpaca_order_id": "oB", "quote_mid_at_submit": 11.00}]
    row = jbuild.build_row(trip, dt.date(2026, 9, 10), plan=None, orders=orders, fees=0.5, trade_number=1)
    assert row["pnl_gross"] == pytest.approx(90.0)        # (10.95 - 10.05) x 100
    assert row["spread_cost"] == pytest.approx(10.0)      # reported, for awareness
    assert row["pnl_net"] == pytest.approx(89.5)          # gross less fees only
    assert row["pnl_net"] != pytest.approx(79.5)


def test_deviations_name_what_went_off_plan():
    legs = jbuild.parse_fills([fill(14, 0, "buy", 50, 10.0), fill(15, 0, "sell", 50, 10.4)])
    trip = jbuild.match_round_trips(legs)[0][0]
    out = jbuild.deviations(trip, {"size": 100, "entry_price": 10.0, "stop": 9.0, "target": 11.0})
    assert any("size 50 vs planned 100" in d for d in out)
    assert any("discretionary exit" in d for d in out)
    assert jbuild.deviations(trip, None) == ["no plan on file for this trade (manual or unlinked)"]


# ---------- fees ----------

def test_sec_and_taf_are_charged_on_sells_only():
    model = {"sec_fee_per_dollar": 27.80 / 1e6, "taf_per_share": 0.000166, "taf_max": 8.30}
    buy = broker_activities.estimate_fees(side="buy", qty=100, price=50.0, model=model)
    assert buy["total"] == 0.0 and "buys incur no" in buy["basis"]
    sell = broker_activities.estimate_fees(side="sell", qty=100, price=50.0, model=model)
    assert sell["sec"] == pytest.approx(5000 * 27.80 / 1e6, abs=1e-6)
    assert sell["taf"] == pytest.approx(0.0166, abs=1e-6)
    assert sell["estimated"] is True


def test_the_taf_is_capped():
    model = {"sec_fee_per_dollar": 0.0, "taf_per_share": 0.000166, "taf_max": 8.30}
    assert broker_activities.estimate_fees(side="sell", qty=10_000_000, price=1.0,
                                           model=model)["taf"] == 8.30


def test_a_paper_account_fee_is_marked_estimated(monkeypatch):
    legs = jbuild.parse_fills([fill(14, 0, "buy", 100, 10.0), fill(15, 0, "sell", 100, 11.0)])
    trip = jbuild.match_round_trips(legs)[0][0]
    trip.symbol = "AAPL"
    model = {"sec_fee_per_dollar": 27.80 / 1e6, "taf_per_share": 0.000166, "taf_max": 8.30}
    total, detail = jledger.fees_for_trip(trip, {}, False, model)
    assert total > 0 and detail["estimated"] is True
    real_total, real_detail = jledger.fees_for_trip(trip, {"AAPL": 1.23}, True, model)
    assert real_total == 1.23 and real_detail["estimated"] is False


# ---------- analytics ----------

def _frame(rows: list[dict]) -> pd.DataFrame:
    base = {"fees": 0.0, "spread_cost": 0.0, "r_multiple": None, "qty": 1, "symbol": "AAA",
            "time_block": "09:30-10:30", "setup_type": "ORB", "market_regime": None,
            "strategy_id": None, "exit_time": "2026-09-10T18:00:00Z"}
    return stats.to_frame([base | r for r in rows])


def test_expectancy_is_after_costs_and_win_rate_is_only_information():
    df = _frame([{"pnl_net": 100.0, "entry_time": "2026-09-10T14:00:00Z"},
                 {"pnl_net": -40.0, "entry_time": "2026-09-10T15:00:00Z"},
                 {"pnl_net": -40.0, "entry_time": "2026-09-10T16:00:00Z"}])
    a = stats._agg(df)
    assert a["trades"] == 3 and a["winners"] == 1 and a["losers"] == 2
    assert a["win_rate_pct"] == pytest.approx(33.33, abs=0.01)
    assert a["expectancy"] == pytest.approx(6.6667, abs=0.001)     # profitable despite a 33% win rate
    assert a["profit_factor"] == pytest.approx(1.25)
    assert "never a promotion gate" in a["note"]


def test_largest_win_comes_from_the_winners_not_the_least_bad_loss():
    a = stats._agg(_frame([{"pnl_net": -5.0, "entry_time": "2026-09-10T14:00:00Z"},
                           {"pnl_net": -1.0, "entry_time": "2026-09-10T15:00:00Z"}]))
    assert a["largest_win"] is None and a["largest_loss"] == -5.0


def test_drawdown_counts_a_losing_first_trade():
    assert stats.max_drawdown(pd.Series([-0.17])) == -0.17
    assert stats.max_drawdown(pd.Series([5.0, -2.0, -1.0, 4.0])) == -3.0
    assert stats.max_drawdown(pd.Series([1.0, 2.0])) == 0.0
    assert stats.max_drawdown(pd.Series(dtype=float)) is None


def test_streaks_count_consecutive_runs():
    df = _frame([{"pnl_net": v, "entry_time": f"2026-09-10T1{i}:00:00Z"}
                 for i, v in enumerate([1.0, 1.0, -1.0, -1.0, -1.0, 1.0])])
    s = stats.streaks(df)
    assert s["longest_win_streak"] == 2 and s["longest_loss_streak"] == 3
    assert s["current_streak"] == 1


def test_an_empty_journal_reports_that_rather_than_failing():
    rep = stats.report(df=pd.DataFrame())
    assert rep["overall"]["trades"] == 0
    assert "no closed trades" in rep["flags"][0]
    assert stats.written_summary(rep) == "No closed trades in this period."


def test_small_samples_are_labelled_provisional(monkeypatch):
    monkeypatch.setattr(stats, "min_sample", lambda: (100, 40))
    monkeypatch.setattr(stats, "strategy_codes", dict)
    df = _frame([{"pnl_net": -1.0, "entry_time": "2026-09-10T14:00:00Z"}])
    rep = stats.report(df=df)
    assert any("provisional" in f for f in rep["flags"])
    assert all(b["sufficient"] is False for b in rep["by_time_block"])


def test_costs_eating_the_edge_is_flagged(monkeypatch):
    monkeypatch.setattr(stats, "min_sample", lambda: (1, 1))
    monkeypatch.setattr(stats, "strategy_codes", dict)
    df = _frame([{"pnl_net": 1.0, "fees": 1.0, "spread_cost": 1.0,
                  "entry_time": "2026-09-10T14:00:00Z"}])
    rep = stats.report(df=df)
    assert any("eating" in f for f in rep["flags"])


# ---------- the weekly review's guards ----------

CONFIGS = {"S1": {"code": "S1", "risk": {"risk_per_trade_pct": 0.5, "daily_loss_limit_r": 2.0,
                                         "max_concurrent": 2, "spread_ceiling_pct": 2.0}}}


def test_a_proposal_that_loosens_risk_is_discarded():
    h = {"strategy_code": "S1", "title": "Risk more",
         "proposed_change": {"risk": {"risk_per_trade_pct": 2.0}}}
    reason = weekly_mod.vet(h, CONFIGS, [])
    assert reason and "loosens a risk limit" in reason


def test_tightening_risk_is_allowed():
    h = {"strategy_code": "S1", "title": "Risk less",
         "proposed_change": {"risk": {"risk_per_trade_pct": 0.25}}}
    assert weekly_mod.vet(h, CONFIGS, []) is None


def test_a_proposal_naming_an_unknown_plugin_is_discarded():
    h = {"strategy_code": "S1", "title": "Use magic",
         "proposed_change": {"entry_conditions": [{"detector": "crystal_ball", "op": ">", "value": 1}]}}
    reason = weekly_mod.vet(h, CONFIGS, [])
    assert reason and "not in the plugin registry" in reason


def test_a_real_detector_is_accepted():
    h = {"strategy_code": "S1", "title": "Raise relvol",
         "proposed_change": {"entry_conditions": [{"detector": "relative_volume_premarket",
                                                   "op": ">=", "value": 7}]}}
    assert weekly_mod.vet(h, CONFIGS, []) is None


def test_an_unknown_strategy_is_discarded():
    assert "unknown strategy" in weekly_mod.vet({"strategy_code": "S99", "title": "x"}, CONFIGS, [])


def test_a_previously_rejected_idea_cannot_come_back_unchanged():
    rejected = [{"title": "Raise relvol"}]
    h = {"strategy_code": "S1", "title": "raise relvol",
         "proposed_change": {"entry_conditions": [{"detector": "relative_volume_premarket",
                                                   "op": ">=", "value": 7}]}}
    reason = weekly_mod.vet(h, CONFIGS, rejected)
    assert reason and "already proposed and rejected" in reason


def test_an_observation_needs_no_config_diff():
    assert weekly_mod.vet({"strategy_code": "S1", "kind": "observe", "title": "watch this"},
                          CONFIGS, []) is None


def test_a_parameter_change_with_no_diff_is_useless():
    reason = weekly_mod.vet({"strategy_code": "S1", "title": "make it better"}, CONFIGS, [])
    assert reason and "no config diff" in reason


def test_degradation_raises_its_own_review_without_a_model():
    report = {"by_strategy": [
        {"strategy": "S1", "expectancy": -1.2, "trades": 12, "profit_factor": 0.4, "max_drawdown": -30},
        {"strategy": "S3", "expectancy": 0.8, "trades": 12},
        {"strategy": "S2", "expectancy": -5.0, "trades": 2},          # too few trades to judge
    ]}
    flags = weekly_mod.degradation_flags(report, 100)
    assert [f["strategy_code"] for f in flags] == ["S1"]
    assert flags[0]["source"] == "degradation_alert" and flags[0]["kind"] == "review"


def test_week_bounds_cover_a_completed_week():
    start, end = weekly_mod.week_bounds(dt.date(2026, 9, 13))       # a Sunday
    assert end == dt.date(2026, 9, 12) and start == dt.date(2026, 9, 6)
    assert (end - start).days == 6


# ---------- endpoints ----------

def test_phase6_endpoints_require_auth():
    for method, path in (("post", "/journal/eod"), ("get", "/journal"),
                         ("get", "/analytics/report"), ("get", "/analytics/ledger"),
                         ("post", "/review/weekly"), ("get", "/review/hypotheses")):
        assert getattr(client, method)(path).status_code == 401


def test_a_hypothesis_decision_must_be_one_of_the_three_words():
    r = client.post("/review/hypotheses/abc/decide?decision=maybe", headers=AUTH)
    assert r.status_code == 422
