"""The nine exit plugins (plan §9).

The invariant every one of them shares: a stop moves toward price or not at all. The Guardian may be
tightened, never loosened, and an exit plugin is no exception — a bug that lets a trail slip backwards
does not look like a bug in a backtest, it looks like a better strategy.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.exits import get as get_exit
from app.exits import implemented
from app.exits.plugins import PositionView
from app.registry import EXIT_NAMES
from tests.conftest import frame


def long_at(price: float, bars: pd.DataFrame, *, stop: float | None = None, idx: int = 0,
            qty: int = 100) -> PositionView:
    return PositionView(symbol="TEST", side="long", entry_price=price, qty=qty, entry_index=idx,
                        stop=stop, opened_at=bars.index[idx])


def short_at(price: float, bars: pd.DataFrame, *, stop: float | None = None, idx: int = 0,
             qty: int = 100) -> PositionView:
    return PositionView(symbol="TEST", side="short", entry_price=price, qty=qty, entry_index=idx,
                        stop=stop, opened_at=bars.index[idx])


def test_every_registered_exit_is_implemented():
    assert set(implemented()) == set(EXIT_NAMES)


# ---------- the shared invariant ----------

def test_a_stop_never_moves_away_from_price():
    bars = frame([100.0])
    long = long_at(100, bars, stop=98.0)
    assert long.tighter(98.0, 99.0) == 99.0      # up is tighter for a long
    assert long.tighter(98.0, 95.0) == 98.0      # down is refused
    short = short_at(100, bars, stop=102.0)
    assert short.tighter(102.0, 101.0) == 101.0  # down is tighter for a short
    assert short.tighter(102.0, 105.0) == 102.0


# ---------- 1. bracket_oco ----------

def test_bracket_oco_derives_a_target_from_r_when_the_plan_has_none():
    bars = frame([100.0] * 5)
    pos = long_at(100.0, bars, stop=98.0)
    lv = get_exit("bracket_oco").levels(pos, bars, target="r_multiple", r_multiple=2.0)
    assert lv["stop"] == 98.0
    assert lv["target"] == pytest.approx(104.0)   # 2 x the $2 risk


def test_bracket_oco_target_atr_next_push_is_the_s5_parameter():
    bars = frame([100.0] * 5)
    pos = long_at(100.0, bars, stop=98.0)
    lv = get_exit("bracket_oco").levels(pos, bars, target="atr_next_push", atr_multiple=0.75,
                                        atr_value=4.0)
    assert lv["target"] == pytest.approx(103.0)


def test_bracket_oco_is_never_marked_extended_hours():
    bars = frame([100.0] * 5)
    order = get_exit("bracket_oco").initial(long_at(100.0, bars, stop=98.0), bars)
    assert order is not None and order.extended_hours is False


# ---------- 2. trailing_pct ----------

def test_trailing_pct_ratchets_up_and_never_down():
    bars = frame([100, 110, 120, 105])
    pos = long_at(100.0, bars, stop=95.0, idx=0)
    cls, state = get_exit("trailing_pct"), {}

    act = cls.on_bar(pos, bars.iloc[:3], state, trail_pct=10.0)
    assert act.action == "move_stop"
    pos.stop = act.price
    raised = pos.stop

    # price falls back: the high-water mark is unchanged, so the stop must not move
    again = cls.on_bar(pos, bars, state, trail_pct=10.0)
    assert again is None or again.price >= raised


# ---------- 3. nbar_trailing_stop ----------

def test_nbar_stop_starts_at_the_lowest_low_of_the_last_n_bars():
    bars = frame([10, 11, 12], lows=[9.0, 9.5, 11.0])
    order = get_exit("nbar_trailing_stop").initial(long_at(12.0, bars, idx=2), bars, n=2)
    assert order.price == pytest.approx(9.5)     # lowest low of bars 1-2, not of bar 0


def test_nbar_stop_ratchets_only_on_a_new_n_bar_high():
    bars = frame([10, 11, 12, 13], lows=[9.0, 9.5, 11.0, 12.0], highs=[10.5, 11.5, 12.5, 13.5])
    cls = get_exit("nbar_trailing_stop")
    pos = long_at(11.0, bars, stop=9.5, idx=1)
    act = cls.on_bar(pos, bars, {}, n=2)         # bar 3 is a new 2-bar high
    assert act.action == "move_stop" and act.price == pytest.approx(11.0)

    flat = frame([10, 11, 12, 11.5], lows=[9.0, 9.5, 11.0, 11.2], highs=[10.5, 11.5, 12.5, 11.8])
    pos2 = long_at(11.0, flat, stop=11.0, idx=1)
    assert cls.on_bar(pos2, flat, {}, n=2) is None, "no new high means no ratchet"


def test_nbar_stop_mirrors_for_a_short():
    bars = frame([13, 12, 11, 10], highs=[14.0, 13.0, 12.0, 11.0], lows=[12.5, 11.5, 10.5, 9.5])
    order = get_exit("nbar_trailing_stop").initial(short_at(11.0, bars, idx=2), bars, n=2)
    assert order.price == pytest.approx(13.0)    # highest high of bars 1-2


# ---------- 4. pattern_low_stop ----------

def test_pattern_low_stop_sits_under_the_pattern_candle_by_the_ticks_given():
    bars = frame([10, 9, 11], lows=[9.5, 8.0, 10.5])
    order = get_exit("pattern_low_stop").initial(long_at(11.0, bars, idx=2), bars, ticks=2,
                                                 tick_size=0.01, lookback_bars=3)
    assert order.price == pytest.approx(8.0 - 0.02)


def test_pattern_low_stop_uses_the_candle_the_entry_recorded():
    bars = frame([10, 9, 11], lows=[9.5, 8.0, 10.5])
    pos = long_at(11.0, bars, idx=2)
    pos.meta["pattern_index"] = 0
    order = get_exit("pattern_low_stop").initial(pos, bars, ticks=1, tick_size=0.01)
    assert order.price == pytest.approx(9.5 - 0.01)


def test_pattern_low_stop_does_not_move_on_its_own():
    bars = frame([10, 11, 12])
    assert get_exit("pattern_low_stop").on_bar(long_at(10.0, bars, stop=9.0), bars, {}) is None


# ---------- 5. stoch_rotation_partial ----------

def test_rotation_partial_fires_once_when_the_fast_stoch_crosses_back_up():
    closes = [100 - i * 0.5 for i in range(30)] + [86 + i * 1.5 for i in range(12)]
    bars = frame(closes)
    cls, state = get_exit("stoch_rotation_partial"), {}
    pos = long_at(86.0, bars, idx=29)
    fired = [cls.on_bar(pos, bars.iloc[:i + 1], state, k=9, d=3, level=80, partial_pct=50)
             for i in range(30, len(bars))]
    hits = [a for a in fired if a is not None]
    assert len(hits) == 1, "a partial is taken once per position, not on every bar above the level"
    assert hits[0].action == "partial" and hits[0].qty_pct == 50


# ---------- 6. scale_out_breakeven ----------

def test_breakeven_waits_for_the_partial_then_fires_once():
    bars = frame([100, 101, 102])
    cls, state = get_exit("scale_out_breakeven"), {}
    pos = long_at(100.0, bars, stop=98.0)
    assert cls.on_bar(pos, bars, state) is None, "no partial yet"

    pos.partials_taken = 1
    act = cls.on_bar(pos, bars, state)
    assert act.action == "move_stop" and act.price == pytest.approx(100.0)
    assert cls.on_bar(pos, bars, state) is None, "it does not fire twice"


def test_breakeven_refuses_to_loosen_a_stop_that_is_already_tighter():
    bars = frame([100, 105])
    pos = long_at(100.0, bars, stop=103.0)     # a trail has already ratcheted past entry
    pos.partials_taken = 1
    act = get_exit("scale_out_breakeven").on_bar(pos, bars, {})
    assert act is None, "moving the stop back down to entry would be loosening it"


# ---------- 7. time_exit ----------

def test_time_exit_closes_at_the_cutoff_and_not_before():
    early = frame([100.0] * 2, start=(15, 0), minutes=5)
    late = frame([100.0] * 2, start=(15, 55), minutes=5)
    cls = get_exit("time_exit")
    assert cls.on_bar(long_at(100.0, early), early, {}, flat_by="15:55") is None
    act = cls.on_bar(long_at(100.0, late), late, {}, flat_by="15:55")
    assert act.action == "close" and "15:55" in act.reason


def test_time_exit_can_block_entries_earlier_than_it_closes():
    bars = frame([100.0], start=(15, 30))
    cls = get_exit("time_exit")
    assert cls.blocks_entry(bars.index[0], flat_by="15:55", no_entries_after="15:00") is True
    assert cls.blocks_entry(bars.index[0], flat_by="15:55") is False


# ---------- 8. synthetic_premarket_stop ----------

def test_synthetic_stop_is_the_only_exit_allowed_in_extended_hours():
    bars = frame([100.0] * 3, start=(7, 0))
    order = get_exit("synthetic_premarket_stop").initial(long_at(100.0, bars, stop=97.0), bars)
    assert order.extended_hours is True
    assert order.price == pytest.approx(97.0)


def test_synthetic_stop_knows_its_own_window():
    cls = get_exit("synthetic_premarket_stop")
    pre = frame([100.0], start=(7, 0)).index[0]
    rth = frame([100.0], start=(10, 0)).index[0]
    assert cls.active_at(pre) is True
    assert cls.active_at(rth) is False


def test_synthetic_stop_without_a_plan_stop_places_nothing():
    """A position with no stop on today's plan is left unwatched and reported, never given an
    invented level (docs/DECISIONS.md #41)."""
    bars = frame([100.0] * 3, start=(7, 0))
    assert get_exit("synthetic_premarket_stop").initial(long_at(100.0, bars, stop=None), bars) is None


# ---------- 9. rotation_complete_exit ----------

def test_rotation_complete_covers_the_short_when_the_fast_stoch_comes_back_down():
    closes = [100 + i * 0.5 for i in range(30)] + [115 - i * 1.5 for i in range(12)]
    bars = frame(closes)
    cls = get_exit("rotation_complete_exit")
    pos = short_at(115.0, bars, idx=29)
    acts = [cls.on_bar(pos, bars.iloc[:i + 1], {}, k=9, d=3, complete_level=20)
            for i in range(30, len(bars))]
    hits = [a for a in acts if a is not None]
    assert hits and hits[0].action == "close" and hits[0].qty_pct == 100
