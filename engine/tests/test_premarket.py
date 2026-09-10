"""Pure pre-market math (app/marketdata/premarket.py) — no network, no DB."""

from __future__ import annotations

import datetime as dt

from app.marketdata import premarket as pm
from app.marketdata.alpaca import ET, Bar

DAY = dt.date(2026, 9, 9)  # Wednesday


def bar(sym: str, hh: int, mm: int, vol: int, day: dt.date = DAY, feed: str = "sip") -> Bar:
    ts = dt.datetime.combine(day, dt.time(hh, mm), ET)
    return Bar(symbol=sym, ts=ts, open=1, high=1, low=1, close=1, volume=vol, vwap=None, trade_count=None, feed=feed)


def test_windows_and_cumulative():
    bars = [bar("X", 3, 59, 999), bar("X", 4, 0, 10), bar("X", 7, 59, 20), bar("X", 8, 0, 30), bar("X", 9, 29, 40),
            bar("X", 9, 30, 500), bar("X", 15, 59, 600), bar("X", 16, 0, 700)]
    assert [b.volume for b in pm.premarket_bars(bars, DAY)] == [10, 20, 30, 40]
    assert [b.volume for b in pm.regular_bars(bars, DAY)] == [500, 600]
    assert pm.cumulative_to(bars, DAY, dt.time(8, 0)) == 30          # bar starting 08:00 excluded
    assert pm.cumulative_to(bars, DAY, dt.time(9, 30)) == 100
    assert pm.cumulative_to(bars, DAY, dt.time(12, 0)) == 100        # clamped to pre-market end
    assert pm.cumulative_to(bars, DAY + dt.timedelta(days=1), dt.time(9, 30)) == 0


def test_summarize_day_and_profile():
    bars = [bar("X", 5, 30, 5), bar("X", 7, 45, 15), bar("X", 9, 0, 25), bar("X", 10, 0, 1000), bar("Y", 8, 0, 77)]
    d = pm.summarize_day("X", bars, DAY, "sip")
    assert (d.premarket_volume, d.print_count, d.regular_volume) == (45, 3, 1000)
    assert d.cum_profile == {"05:00": 0, "06:00": 5, "07:00": 5, "07:30": 5, "08:00": 20, "08:30": 20, "09:00": 20,
                             "09:30": 45}
    row = d.as_row()
    assert row["trade_date"] == "2026-09-09" and row["feed"] == "sip" and row["cum_profile"]["09:30"] == 45
    assert pm.summarize_day("Z", bars, DAY, "sip").premarket_volume == 0


def test_checkpoint_for():
    assert pm.checkpoint_for(dt.time(7, 59)) == "07:30"
    assert pm.checkpoint_for(dt.time(8, 0)) == "08:00"
    assert pm.checkpoint_for(dt.time(4, 30)) is None
    assert pm.checkpoint_for(dt.time(11, 0)) == "09:30"


def _rows():
    out = []
    for i in range(20):
        d = DAY - dt.timedelta(days=i)
        out.append({"symbol": "X", "trade_date": d.isoformat(), "feed": "sip", "premarket_volume": 1000 + i,
                    "cum_profile": {"08:00": 100 + i}})
        out.append({"symbol": "X", "trade_date": d.isoformat(), "feed": "iex", "premarket_volume": 1})
    return out


def test_baseline_from_rows_same_feed_same_checkpoint():
    b = pm.baseline_from_rows(_rows(), "sip", checkpoint=None, n=14, before=DAY)
    assert b.days_used == 14 and b.dates[0] == "2026-09-08"           # today excluded, most recent first
    assert b.average == sum(1000 + i for i in range(1, 15)) / 14
    b8 = pm.baseline_from_rows(_rows(), "sip", checkpoint="08:00", n=14, before=DAY)
    assert b8.average == sum(100 + i for i in range(1, 15)) / 14
    # iex rows have no profile → checkpoint baseline is empty rather than guessed
    assert pm.baseline_from_rows(_rows(), "iex", checkpoint="08:00", n=14).days_used == 0
    assert pm.baseline_from_rows(_rows(), "iex", checkpoint=None, n=5).days_used == 5
    assert pm.baseline_from_rows([], "sip").average is None


def test_relvol():
    assert pm.relvol(5000, 1000.0) == 5.0
    assert pm.relvol(5000, 0) is None and pm.relvol(5000, None) is None


def test_weekdays_and_session_date():
    days = pm.weekdays_ending(dt.date(2026, 9, 14), 3)  # Mon
    assert days == [dt.date(2026, 9, 10), dt.date(2026, 9, 11), dt.date(2026, 9, 14)]
    assert pm.session_date_for(dt.datetime(2026, 9, 10, 23, 0, tzinfo=ET)) == dt.date(2026, 9, 10)
    assert pm.session_date_for(dt.datetime(2026, 9, 10, 8, 0, tzinfo=ET)) == dt.date(2026, 9, 9)
    assert pm.session_date_for(dt.datetime(2026, 9, 13, 12, 0, tzinfo=ET)) == dt.date(2026, 9, 11)  # Sunday → Friday
