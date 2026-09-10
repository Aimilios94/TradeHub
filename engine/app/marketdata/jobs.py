"""Nightly pre-market baseline + IEX↔SIP calibration job (plan Phase 1; n8n cron 23:00 ET calls this).

For each trading date in the range and each feed:
  fetch 1-min bars 04:00–16:00 ET for the universe (batched) → per symbol: pre-market total, print count,
  cumulative profile at checkpoints → upsert premarket_volume_baseline (one row per symbol/date/feed).
Then, for dates where both feeds returned data, write feed_calibration rows for the 'premarket' and
'regular' windows (iex_volume vs sip_volume; the ratio column is generated in Postgres).
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from statistics import median

from app.marketdata import alpaca, repo
from app.marketdata.premarket import (
    DEFAULT_CHECKPOINTS,
    PREMARKET_START,
    REGULAR_END,
    DayVolume,
    session_date_for,
    summarize_day,
    weekdays_ending,
)
from app.notify import send_telegram

log = logging.getLogger(__name__)
BATCH = 25  # symbols per Alpaca request (~18k 1-min rows/day on SIP → 2 pages)


@dataclass(slots=True)
class JobResult:
    dates: list[str]
    symbols: int
    baseline_rows: int = 0
    calibration_rows: int = 0
    skipped_dates: list[str] = field(default_factory=list)
    zero_iex_premarket: int = 0  # symbol-days where IEX had no pre-market prints but SIP did
    median_premarket_ratio: float | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"dates": self.dates, "symbols": self.symbols, "baseline_rows": self.baseline_rows,
                "calibration_rows": self.calibration_rows, "skipped_dates": self.skipped_dates,
                "zero_iex_premarket": self.zero_iex_premarket, "median_premarket_ratio_iex_over_sip": self.median_premarket_ratio,
                "errors": self.errors}

    def summary(self) -> str:
        r = f"{self.median_premarket_ratio:.4f}" if self.median_premarket_ratio is not None else "n/a"
        return (f"Pre-market baseline: {self.baseline_rows} rows, {self.calibration_rows} calibration rows over "
                f"{len(self.dates) - len(self.skipped_dates)} session(s) × {self.symbols} symbols. "
                f"IEX/SIP pre-market median ratio {r}; {self.zero_iex_premarket} symbol-days had zero IEX pre-market prints."
                + (f" Errors: {len(self.errors)}" if self.errors else ""))


def _bars_for(symbols: list[str], day: dt.date, feed: str) -> list[alpaca.Bar]:
    start = alpaca.et_datetime(day, PREMARKET_START.hour, PREMARKET_START.minute)
    end = alpaca.et_datetime(day, REGULAR_END.hour, REGULAR_END.minute)
    out: list[alpaca.Bar] = []
    for i in range(0, len(symbols), BATCH):
        out.extend(alpaca.get_bars(symbols[i:i + BATCH], start, end, feed=feed, timeframe="1Min"))
    return out


def run_premarket_baseline(*, end_date: dt.date | None = None, days: int = 1, symbols: list[str] | None = None,
                           checkpoints: tuple[str, ...] = DEFAULT_CHECKPOINTS, feeds: tuple[str, ...] = ("iex", "sip"),
                           notify: bool = False, dry_run: bool = False) -> JobResult:
    end_date = end_date or session_date_for(alpaca.now_et())
    dates = weekdays_ending(end_date, max(1, days))
    universe = sorted({s.upper() for s in symbols}) if symbols else repo.baseline_universe()
    result = JobResult(dates=[d.isoformat() for d in dates], symbols=len(universe))
    if not universe:
        result.errors.append("empty universe — set app_settings.baseline_universe or watchlist_default")
        return result

    baseline_rows: list[dict] = []
    calibration_rows: list[dict] = []
    ratios: list[float] = []
    for day in dates:
        per_feed: dict[str, dict[str, DayVolume]] = {}
        for feed in feeds:
            try:
                bars = _bars_for(universe, day, feed)
            except Exception as e:
                result.errors.append(f"{day} {feed}: {type(e).__name__}: {str(e)[:120]}")
                log.exception("bars fetch failed %s %s", day, feed)
                continue
            if not bars:
                continue
            per_feed[feed] = {sym: summarize_day(sym, bars, day, feed, checkpoints) for sym in universe}
        if not per_feed:
            result.skipped_dates.append(day.isoformat())  # holiday or no data yet
            continue
        for feed, by_sym in per_feed.items():
            baseline_rows.extend(v.as_row() for v in by_sym.values())
        if "iex" in per_feed and "sip" in per_feed:
            for sym in universe:
                i, s = per_feed["iex"][sym], per_feed["sip"][sym]
                if s.premarket_volume == 0 and s.regular_volume == 0:
                    continue  # symbol did not trade at all (delisted / halted) — not a calibration point
                calibration_rows.append({"symbol": sym, "trade_date": day.isoformat(), "session_window": "premarket",
                                         "iex_volume": i.premarket_volume, "sip_volume": s.premarket_volume})
                calibration_rows.append({"symbol": sym, "trade_date": day.isoformat(), "session_window": "regular",
                                         "iex_volume": i.regular_volume, "sip_volume": s.regular_volume})
                if s.premarket_volume > 0:
                    ratios.append(i.premarket_volume / s.premarket_volume)
                    if i.premarket_volume == 0:
                        result.zero_iex_premarket += 1

    result.median_premarket_ratio = round(median(ratios), 6) if ratios else None
    if not dry_run:
        try:
            result.baseline_rows = repo.upsert_baselines(baseline_rows)
            result.calibration_rows = repo.upsert_calibrations(calibration_rows)
        except Exception as e:
            result.errors.append(f"db write: {type(e).__name__}: {str(e)[:160]}")
            log.exception("baseline upsert failed")
    else:
        result.baseline_rows, result.calibration_rows = len(baseline_rows), len(calibration_rows)
    if notify:
        send_telegram(("⚠️ " if result.errors else "✅ ") + result.summary())
    return result


def calibration_summary(days: int = 30) -> dict:
    """Median IEX/SIP ratio per window over the last N days — the number the backtester (§13 trap 2) needs."""
    rows = repo.calibration_rows(alpaca.now_et().date() - dt.timedelta(days=days))
    out: dict[str, dict] = {}
    for window in ("premarket", "regular"):
        rs = [float(r["ratio"]) for r in rows if r["session_window"] == window and r.get("ratio") is not None]
        zero = sum(1 for r in rows if r["session_window"] == window and int(r["iex_volume"]) == 0 and int(r["sip_volume"]) > 0)
        out[window] = {"samples": len(rs), "median_iex_over_sip": round(median(rs), 6) if rs else None,
                       "zero_iex_share": round(zero / len(rs), 4) if rs else None}
    return {"days": days, "windows": out}
