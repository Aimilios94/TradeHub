"""Phase 2 detectors (app/detectors/basic.py) — pure functions on synthetic frames."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.detectors import get, implemented
from app.detectors.basic import wilder_atr
from app.marketdata.alpaca import ET


def pm_frame(rows: list[tuple[str, float, int]], day=dt.date(2026, 9, 9)) -> pd.DataFrame:
    """rows: (HH:MM, price, volume) → pre-market OHLCV frame indexed by ET timestamp."""
    recs = []
    for hhmm, px, vol in rows:
        hh, mm = map(int, hhmm.split(":"))
        recs.append({"ts": dt.datetime.combine(day, dt.time(hh, mm), ET), "open": px, "high": px * 1.01, "low": px * 0.99,
                     "close": px, "volume": vol})
    return pd.DataFrame(recs).set_index("ts")


def daily_frame(closes: list[float], vol: int = 1_000_000, rng: float = 1.0) -> pd.DataFrame:
    start = dt.date(2026, 6, 1)
    recs = [{"date": start + dt.timedelta(days=i), "open": c, "high": c + rng / 2, "low": c - rng / 2, "close": c, "volume": vol}
            for i, c in enumerate(closes)]
    return pd.DataFrame(recs).set_index("date")


def test_phase2_subset_is_registered():
    assert {"gap_pct", "adv", "atr", "relative_volume_premarket", "float_shares", "prior_day_levels",
            "premarket_high_low", "distance_to_level_atr", "nbar_high_low"} <= set(implemented())


def test_gap_pct():
    r = get("gap_pct")(pm_frame([("07:00", 11.0, 100)]), prev_close=10.0)
    assert r.value == 10.0 and r.fields["abs"] == 10.0 and r.fields["direction"] == "up" and r.quality == "exact"
    r = get("gap_pct")(None, prev_close=10.0, price=9.5)
    assert r.value == -5.0 and r.fields["direction"] == "down"
    r = get("gap_pct")(None, prev_close=10.0)
    assert r.value is None and r.quality == "degraded"


def test_adv_and_atr():
    d = daily_frame([10.0] * 30, vol=2_000_000, rng=1.0)
    assert get("adv")(None, daily=d, lookback_days=20).value == 2_000_000
    a = get("adv")(None, daily=d.tail(5), lookback_days=20)
    assert a.quality == "approximate" and a.fields["sessions"] == 5
    assert get("adv")(None, daily=None).quality == "degraded"
    atr = get("atr")(None, daily=d, period=14)
    assert atr.value == pytest.approx(1.0, abs=1e-6) and atr.fields["atr_pct"] == pytest.approx(10.0, abs=1e-3)
    assert wilder_atr(d.head(1)) is None


def test_prior_day_and_nbar_levels():
    d = daily_frame([1, 2, 3, 4, 5], rng=0.2)
    p = get("prior_day_levels")(None, daily=d)
    assert p.fields["close"] == 5 and p.fields["high"] == 5.1 and p.fields["low"] == 4.9
    n = get("nbar_high_low")(None, daily=d, n=3)
    assert (n.fields["high"], n.fields["low"], n.fields["sessions"]) == (5.1, 2.9, 3)


def test_premarket_high_low():
    r = get("premarket_high_low")(pm_frame([("06:00", 10.0, 500), ("08:00", 12.0, 700)]))
    assert r.fields["volume"] == 1200 and r.fields["print_count"] == 2 and r.fields["last"] == 12.0
    assert r.fields["high"] == pytest.approx(12.12) and r.fields["low"] == pytest.approx(9.9)
    assert get("premarket_high_low")(None).quality == "degraded"


def test_relative_volume_premarket_same_feed_same_checkpoint():
    pm = pm_frame([("05:00", 5.0, 1000), ("07:30", 5.5, 4000)])
    r = get("relative_volume_premarket")(pm, baseline_avg=1000.0, baseline_days_used=14, baseline_days=14,
                                         checkpoint="08:00", feed="sip", adv_value=50_000)
    assert r.value == 5.0 and r.fields["pct_of_adv"] == 10.0 and r.fields["checkpoint"] == "08:00" and r.quality == "exact"
    r = get("relative_volume_premarket")(pm, baseline_avg=None, baseline_days_used=0)
    assert r.value is None and r.quality == "degraded" and "baseline" in r.notes
    r = get("relative_volume_premarket")(pm, baseline_avg=1000.0, baseline_days_used=6, baseline_days=14)
    assert r.quality == "approximate"


def test_float_shares_sources():
    assert get("float_shares")(None, stats={"float_shares": 5_000_000, "float_source": "finviz"}).quality == "exact"
    r = get("float_shares")(None, stats={"shares_outstanding": 30_000_000})
    assert r.value == 30_000_000 and r.quality == "approximate" and r.fields["source"] == "fmp_shares_outstanding"
    assert get("float_shares")(None, stats={}).value is None


def test_distance_to_level_atr():
    r = get("distance_to_level_atr")(None, price=10.4, atr_value=1.0, levels={"prior_high": 10.0, "high_20d": 15.0},
                                     round_numbers=False, max_atr=1.5)
    assert r.value == 0.4 and r.fields["level_name"] == "prior_high" and r.fields["within"] is True
    r = get("distance_to_level_atr")(None, price=12.5, atr_value=1.0, levels={"prior_high": 10.0}, round_numbers=True)
    assert r.fields["level_name"] in ("round_12", "round_13") and r.value == 0.5
    assert get("distance_to_level_atr")(None, price=10.0, atr_value=0).quality == "degraded"
