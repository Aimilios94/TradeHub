"""Phase 7 detectors (plan §9 items 1-7, 13, 17, 19-23).

Every test is offline and deterministic. The ones that matter most are not "does it compute a number"
but "does it refuse to compute one it cannot know": a detector that guesses is worse than a detector
that is missing, because nothing downstream can tell the difference.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.detectors import get
from app.detectors.indicators import stoch_frame, swing_points
from tests.conftest import frame

# ---------- shared fixtures ----------

@pytest.fixture
def divergence_bars() -> pd.DataFrame:
    """A textbook bullish divergence, ending on the trigger bar.

    Price makes a LOWER low (89.40 vs 89.70) while the 9-3 makes a HIGHER low (11.9 vs 1.5), and %K
    then turns up through 20 on the final bar.
    """
    closes = [100 - i * 0.5 for i in range(20)]
    closes += [90.20, 89.95, 89.75]                                   # capitulation; swing low at idx 22
    closes += [90.10, 90.50, 90.90, 91.15, 91.20, 91.00, 90.60, 90.20]  # the bounce
    closes += [89.62]                                                 # the retest: lower low, close off it
    closes += [89.90]                                                 # the trigger bar
    lows = [c * 0.9995 for c in closes]
    highs = [c * 1.0005 for c in closes]
    lows[22] = 89.70
    lows[-2] = 89.40
    return frame(closes, highs=highs, lows=lows)


# ---------- ema / rsi / vwap ----------

def test_ema_reports_which_side_of_it_price_is_on(uptrend_bars):
    r = get("ema")(uptrend_bars, period=20)
    assert r.fields["price_above"] is True
    assert r.fields["price_below"] is False
    assert r.fields["price_below_or_at"] is False
    assert r.value < float(uptrend_bars["close"].iloc[-1])


def test_ema_that_has_not_warmed_up_says_so_instead_of_pretending():
    r = get("ema")(frame([100.0] * 10), period=200)
    assert r.quality == "approximate"
    assert "warming up" in r.notes


def test_rsi_is_bounded_and_flags_the_extremes():
    up = get("rsi")(frame([100 + i for i in range(60)]))
    down = get("rsi")(frame([160 - i for i in range(60)]))
    assert 0 <= up.value <= 100 and 0 <= down.value <= 100
    assert up.fields["overbought"] and not up.fields["oversold"]
    assert down.fields["oversold"] and not down.fields["overbought"]


def test_vwap_is_volume_weighted_not_a_mean():
    # one huge-volume bar at 90 drags VWAP far below the arithmetic mean of the closes
    bars = frame([100, 100, 90, 100, 100], volume=[10, 10, 10_000, 10, 10])
    r = get("vwap")(bars)
    assert r.value < 95
    assert r.fields["price_above"] is True


def test_vwap_on_iex_is_labelled_approximate():
    bars = frame([100.0] * 20)
    assert get("vwap")(bars, feed="sip").quality == "exact"
    iex = get("vwap")(bars, feed="iex")
    assert iex.quality == "approximate"
    assert "slice of the tape" in iex.notes


def test_vwap_does_not_mix_the_premarket_block_into_the_session():
    pm = frame([50.0] * 6, start=(8, 0), minutes=5, volume=1_000_000)
    rth = frame([100.0] * 6, start=(9, 30), minutes=5, volume=1_000)
    both = pd.concat([pm, rth])
    r = get("vwap")(both, anchor="session")
    assert r.value == pytest.approx(100.0, abs=0.5), "the 08:00 block must not be inside the 09:30 anchor"


# ---------- the HPS stochastic family ----------

def test_stochastic_pins_at_the_extremes():
    assert get("stochastic")(frame([100 + i for i in range(30)])).value > 95
    assert get("stochastic")(frame([130 - i for i in range(30)])).value < 5


def test_a_flat_window_holds_the_previous_value_rather_than_dividing_by_zero():
    f = stoch_frame(frame([100.0] * 30), 9, 3)
    assert f["k"].notna().all()
    assert (f["k"] == 50.0).all(), "with no range at all the only honest reading is the midpoint"


def test_quad_extreme_needs_all_four_bands(downtrend_bars):
    r = get("stoch_quad_extreme")(downtrend_bars, side="under", threshold=20)
    assert r.value is True
    assert r.fields["how_many"] == 4 and r.fields["of"] == 4
    assert set(r.fields["values"]) == {"9-3", "14-3", "40-4", "60-10"}


def test_quad_extreme_is_false_when_only_the_fast_band_is_extreme():
    # a long uptrend with a shallow pullback: the 9-3 drops under 20, the slow bands stay high
    closes = [100 + i * 0.5 for i in range(80)] + [139.0, 138.0, 137.0]
    r = get("stoch_quad_extreme")(frame(closes), side="under", threshold=20)
    assert r.value is False
    assert r.fields["values"]["9-3"] < 20 < r.fields["values"]["60-10"]
    assert 0 < r.fields["how_many"] < 4


def test_quad_extreme_flags_a_band_that_has_not_warmed_up():
    r = get("stoch_quad_extreme")(frame([100 - i for i in range(30)]))
    assert r.quality == "approximate"
    assert "warming up" in r.notes


def test_embedded_counts_only_a_run_that_reaches_the_current_bar():
    up = frame([100 + i for i in range(60)])
    r = get("stoch_embedded")(up, k=14, d=3, side="over", threshold=80, min_bars=3)
    assert r.value is True and r.fields["run"] >= 3

    # the same strong run, then a collapse: the run is broken, so the answer is False
    broken = frame([100 + i for i in range(60)] + [120.0, 110.0])
    assert get("stoch_embedded")(broken, k=14, d=3, side="over", threshold=80, min_bars=3).value is False


def test_embedded_respects_the_threshold_it_is_given():
    up = frame([100 + i * 0.4 for i in range(80)])
    assert get("stoch_embedded")(up, k=60, d=10, threshold=85, min_bars=3).value is True
    assert get("stoch_embedded")(up, k=60, d=10, threshold=99.9, min_bars=3).value is False


def test_swing_points_need_confirmation_on_both_sides():
    lows, highs = swing_points(pd.Series([5, 4, 3, 2, 1, 2, 3, 4, 5], dtype=float), lookback=4)
    assert lows == [4] and highs == []
    # the same V, but the low is the LAST bar: not yet confirmed, so it is not a swing point
    lows2, _ = swing_points(pd.Series([5, 4, 3, 2, 1], dtype=float), lookback=4)
    assert lows2 == []


def test_divergence_fires_on_a_lower_price_low_with_a_higher_stoch_low(divergence_bars):
    r = get("stoch_divergence")(divergence_bars, k=9, d=3, swing_lookback=5, trigger_level=20)
    assert r.fields["divergence"] is True
    assert r.fields["trigger"] is True
    assert r.value is True
    assert r.fields["recent_swing"]["price"] < r.fields["prior_swing"]["price"]
    assert r.fields["recent_swing"]["stoch"] > r.fields["prior_swing"]["stoch"]


def test_divergence_is_false_without_the_trigger_even_when_price_diverges(divergence_bars):
    """The divergence is the setup; the 9-3 turning up through 20 is the entry. One is not the other."""
    before_trigger = divergence_bars.iloc[:-1]
    r = get("stoch_divergence")(before_trigger, k=9, d=3, swing_lookback=5, trigger_level=20)
    assert r.fields["divergence"] is True
    assert r.fields["trigger"] is False
    assert r.value is False


def test_divergence_is_false_in_a_clean_downtrend(downtrend_bars):
    r = get("stoch_divergence")(downtrend_bars)
    assert r.value is False
    assert r.fields["divergence"] is False


def test_divergence_says_when_there_is_not_enough_history():
    r = get("stoch_divergence")(frame([100.0] * 6))
    assert r.value is None and r.quality == "degraded"
    assert "not enough bars" in r.notes


# ---------- flagpole ----------

def test_flagpole_measures_steepness_against_the_symbols_own_baseline():
    quiet = [100 + (i % 2) * 0.05 for i in range(60)]     # 60 bars of noise = a low baseline
    pole = [100 + i * 1.5 for i in range(8)]              # then a sharp move off the EMA
    r = get("flagpole_steepness")(frame(quiet + pole), ema_period=20, baseline_bars=50, min_multiple=1.5)
    assert r.value is True
    assert r.fields["multiple"] > 1.5

    calm = get("flagpole_steepness")(frame(quiet), ema_period=20, baseline_bars=50, min_multiple=1.5)
    assert calm.value is False


def test_flagpole_reports_no_pole_when_price_is_sitting_on_the_ema():
    """Price at the EMA right now means the pole has not started — 0, not a small number."""
    r = get("flagpole_steepness")(frame([100.0] * 60), ema_period=20, baseline_bars=50)
    assert r.value is False
    assert r.fields["bars_since_ema_touch"] == 0
    assert "no pole yet" in r.notes


# ---------- structure ----------

def test_opening_range_uses_only_the_first_n_minutes():
    bars = frame([10, 11, 12, 20, 5], start=(9, 30), minutes=5)
    r = get("opening_range")(bars, minutes=15)
    assert r.fields["high"] == pytest.approx(12 * 1.001, rel=1e-6)
    assert r.fields["bars_in_window"] == 3
    assert r.fields["broke_up"] is True and r.fields["broke_down"] is True


def test_opening_range_without_regular_session_bars_is_degraded():
    r = get("opening_range")(frame([10.0] * 5, start=(7, 0)), minutes=30)
    assert r.value is None and r.quality == "degraded"


def test_double_bottom_needs_the_two_lows_to_match_within_tolerance():
    v = [10, 9, 8, 7, 6, 7, 8, 9, 10, 9, 8, 7, 6, 7, 8, 9, 10]
    bars = frame(v, lows=[float(x) for x in v])
    assert get("double_top_bottom")(bars, tolerance_pct=0.5, swing_lookback=4).fields["double_bottom"] is True
    v2 = list(v)
    v2[12] = 4.0                        # the second low is 33% lower: not a double bottom
    bars2 = frame(v2, lows=[float(x) for x in v2])
    assert get("double_top_bottom")(bars2, tolerance_pct=0.5, swing_lookback=4).fields["double_bottom"] is False


def test_breakout_52w_reports_partial_history_as_partial():
    daily = frame([100 + i * 0.01 for i in range(100)], minutes=1440)
    r = get("breakout_52w_base")(None, daily=daily)
    assert r.quality == "approximate"
    assert "partial" in r.notes


def test_sector_relative_strength_ranks_and_refuses_to_guess_a_missing_sector():
    strong = frame([100 + i for i in range(10)], minutes=1440)
    weak = frame([100 - i for i in range(10)], minutes=1440)
    r = get("sector_relative_strength")(None, sector_daily={"XLK": strong, "XLU": weak}, sector_etf="XLK")
    assert r.fields["strongest"] == "XLK" and r.fields["weakest"] == "XLU"
    assert r.fields["rank"] == 1

    unknown = get("sector_relative_strength")(None, sector_daily={"XLK": strong, "XLU": weak})
    assert unknown.value is None and unknown.quality == "degraded"
    assert unknown.fields["strongest"] == "XLK", "the ranking is still useful market context"


def test_cvd_is_always_approximate_and_marked_display_only():
    r = get("cvd")(frame([100.0] * 20))
    assert r.quality == "approximate"
    assert r.fields["display_only"] is True
    assert "never an entry rule" in r.notes


def test_cvd_refuses_the_tiers_the_free_plan_cannot_support():
    r = get("cvd")(frame([100.0] * 20), tier=3)
    assert r.value is None and r.quality == "degraded"


def test_volume_profile_finds_the_price_where_the_volume_traded():
    # 30 bars parked at 100 with big volume, 3 bars at 110 with tiny volume
    bars = frame([100.0] * 30 + [110.0] * 3, volume=[100_000] * 30 + [10] * 3)
    r = get("volume_profile")(bars, bins=50)
    assert 99 < r.fields["poc"] < 101
    assert r.fields["val"] <= r.fields["poc"] <= r.fields["vah"]
    assert 60 <= r.fields["value_area_volume_pct"] <= 100


def test_volume_profile_with_no_volume_is_degraded():
    r = get("volume_profile")(frame([100.0] * 10, volume=0))
    assert r.value is None and r.quality == "degraded"


# ---------- the rule that binds all of them ----------

@pytest.mark.parametrize("name", [
    "ema", "rsi", "vwap", "stochastic", "stoch_quad_extreme", "stoch_divergence", "stoch_embedded",
    "opening_range", "double_top_bottom", "flagpole_steepness", "cvd", "volume_profile",
])
def test_no_detector_invents_a_value_from_nothing(name):
    """Plan §33: a missing figure is reported missing. Empty input must never produce a number."""
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    r = get(name)(empty)
    assert r.value is None
    assert r.quality == "degraded"
    assert r.notes, f"{name} returned no value and no reason"
