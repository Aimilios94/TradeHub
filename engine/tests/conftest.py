"""Shared bar builders for the offline tests.

Every Phase 7 test runs on hand-built frames: no network, no Supabase, no clock. A backtester whose
tests need the market open is a backtester nobody re-runs.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ET = ZoneInfo("America/New_York")


def bar_index(n: int, *, start: tuple[int, int] = (9, 30), minutes: int = 5,
              day: dt.date = dt.date(2026, 3, 10)) -> pd.DatetimeIndex:
    first = dt.datetime.combine(day, dt.time(*start), ET)
    return pd.DatetimeIndex([first + dt.timedelta(minutes=minutes * i) for i in range(n)])


def frame(closes, *, highs=None, lows=None, opens=None, volume=10_000, **kw) -> pd.DataFrame:
    """OHLCV from a list of closes; highs/lows default to a thin wrapper around the close."""
    closes = [float(c) for c in closes]
    highs = list(highs) if highs is not None else [c * 1.001 for c in closes]
    lows = list(lows) if lows is not None else [c * 0.999 for c in closes]
    opens = list(opens) if opens is not None else closes
    vols = volume if isinstance(volume, list) else [volume] * len(closes)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
                        index=bar_index(len(closes), **kw))


@pytest.fixture
def flat_bars() -> pd.DataFrame:
    return frame([100.0] * 80)


@pytest.fixture
def uptrend_bars() -> pd.DataFrame:
    return frame([100 + i * 0.1 for i in range(120)])


@pytest.fixture
def downtrend_bars() -> pd.DataFrame:
    return frame([120 - i * 0.1 for i in range(120)])
