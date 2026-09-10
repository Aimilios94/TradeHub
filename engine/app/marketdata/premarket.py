"""Pre-market volume math — PURE functions, no I/O (plan §3.2 item 2, §6 S1, §9 detector 10).

Definitions
- Pre-market window: 04:00 ≤ bar_start < 09:30 ET. Regular window: 09:30 ≤ bar_start < 16:00 ET.
- `cum_profile`: cumulative pre-market volume at fixed checkpoints (bar_start < checkpoint), so a scan
  at 08:00 compares today's volume-by-08:00 against the 14-day average volume-by-08:00 — same window,
  same feed, apples to apples. Full-window total is `premarket_volume`.
- Baseline: mean over the N most recent prior trading days available for (symbol, feed). Days with no
  bars at all are still counted as 0 when they were stored as such (a quiet day IS information).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import fmean

from app.marketdata.alpaca import ET, Bar

PREMARKET_START = dt.time(4, 0)
PREMARKET_END = dt.time(9, 30)
REGULAR_END = dt.time(16, 0)
DEFAULT_CHECKPOINTS: tuple[str, ...] = ("05:00", "06:00", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30")
DEFAULT_BASELINE_DAYS = 14


def parse_hhmm(s: str) -> dt.time:
    hh, mm = s.split(":")
    return dt.time(int(hh), int(mm))


def _in_window(bar: Bar, day: dt.date, start: dt.time, end: dt.time) -> bool:
    t = bar.ts_et()
    return t.date() == day and start <= t.time() < end


def premarket_bars(bars: Iterable[Bar], day: dt.date) -> list[Bar]:
    return [b for b in bars if _in_window(b, day, PREMARKET_START, PREMARKET_END)]


def regular_bars(bars: Iterable[Bar], day: dt.date) -> list[Bar]:
    return [b for b in bars if _in_window(b, day, PREMARKET_END, REGULAR_END)]


def cumulative_to(bars: Iterable[Bar], day: dt.date, as_of: dt.time) -> int:
    """Pre-market volume from 04:00 up to (not including) bars starting at `as_of`."""
    return sum(b.volume for b in bars if _in_window(b, day, PREMARKET_START, min(as_of, PREMARKET_END)))


def cum_profile(bars: Iterable[Bar], day: dt.date, checkpoints: Iterable[str] = DEFAULT_CHECKPOINTS) -> dict[str, int]:
    pm = premarket_bars(bars, day)
    return {cp: cumulative_to(pm, day, parse_hhmm(cp)) for cp in checkpoints}


@dataclass(slots=True)
class DayVolume:
    symbol: str
    trade_date: dt.date
    feed: str
    premarket_volume: int
    print_count: int
    cum_profile: dict[str, int] = field(default_factory=dict)
    regular_volume: int = 0

    def as_row(self) -> dict:
        return {"symbol": self.symbol, "trade_date": self.trade_date.isoformat(), "feed": self.feed,
                "premarket_volume": self.premarket_volume, "print_count": self.print_count,
                "cum_profile": self.cum_profile}


def summarize_day(symbol: str, bars: Iterable[Bar], day: dt.date, feed: str,
                  checkpoints: Iterable[str] = DEFAULT_CHECKPOINTS) -> DayVolume:
    bars = [b for b in bars if b.symbol == symbol]
    pm = premarket_bars(bars, day)
    reg = regular_bars(bars, day)
    return DayVolume(symbol=symbol, trade_date=day, feed=feed, premarket_volume=sum(b.volume for b in pm),
                     print_count=len(pm), cum_profile=cum_profile(pm, day, checkpoints),
                     regular_volume=sum(b.volume for b in reg))


def checkpoint_for(as_of: dt.time, checkpoints: Iterable[str] = DEFAULT_CHECKPOINTS) -> str | None:
    """Latest checkpoint ≤ as_of (so the live cumulative window never exceeds the baseline window)."""
    best = None
    for cp in checkpoints:
        if parse_hhmm(cp) <= as_of and (best is None or parse_hhmm(cp) > parse_hhmm(best)):
            best = cp
    return best


@dataclass(slots=True)
class Baseline:
    feed: str
    checkpoint: str | None  # None = full pre-market window
    days_used: int
    average: float | None
    values: list[int]
    dates: list[str]

    def as_dict(self) -> dict:
        return {"feed": self.feed, "checkpoint": self.checkpoint, "days_used": self.days_used,
                "average": self.average, "dates": self.dates}


def baseline_from_rows(rows: Iterable[dict], feed: str, checkpoint: str | None = None,
                       n: int = DEFAULT_BASELINE_DAYS, before: dt.date | None = None) -> Baseline:
    """`rows` are premarket_volume_baseline records (any order). Uses the n most recent dates < before."""
    picked: list[tuple[str, int]] = []
    for r in rows:
        if r.get("feed") != feed:
            continue
        d = str(r["trade_date"])
        if before is not None and dt.date.fromisoformat(d) >= before:
            continue
        if checkpoint is None:
            v = int(r["premarket_volume"])
        else:
            prof = r.get("cum_profile") or {}
            if checkpoint not in prof:
                continue  # row predates profiles or checkpoint config changed — skip, don't guess
            v = int(prof[checkpoint])
        picked.append((d, v))
    picked.sort(reverse=True)
    picked = picked[:n]
    vals = [v for _, v in picked]
    return Baseline(feed=feed, checkpoint=checkpoint, days_used=len(vals), average=fmean(vals) if vals else None,
                    values=vals, dates=[d for d, _ in picked])


def relvol(current: int, baseline_avg: float | None) -> float | None:
    if baseline_avg is None or baseline_avg <= 0:
        return None
    return round(current / baseline_avg, 3)


def weekdays_ending(end: dt.date, n: int) -> list[dt.date]:
    """The n most recent Mon–Fri dates ending at `end` (holidays are filtered later by 'no bars')."""
    out: list[dt.date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return sorted(out)


def session_date_for(now_et: dt.datetime) -> dt.date:
    """Which session the nightly job should summarize: today if the regular session has ended, else yesterday."""
    day = now_et.date()
    if now_et.time() < REGULAR_END or day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


__all__ = [
    "DEFAULT_BASELINE_DAYS",
    "DEFAULT_CHECKPOINTS",
    "ET",
    "Bar",
    "Baseline",
    "DayVolume",
    "baseline_from_rows",
    "checkpoint_for",
    "cum_profile",
    "cumulative_to",
    "parse_hhmm",
    "premarket_bars",
    "regular_bars",
    "relvol",
    "session_date_for",
    "summarize_day",
    "weekdays_ending",
]
