"""Phase 7 structure / pattern / participation detectors (plan §9 items 13, 17, 19, 20, 22-23, §27).

Same contract as the rest: pure functions over an OHLCV frame, no I/O. Where a detector needs data
the caller must supply (sector ETF frames, a daily frame), it is a keyword argument — a missing input
returns `degraded` with the reason, never a fabricated value (plan §33).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

from app.detectors import DetectorResult, register
from app.detectors.indicators import swing_points

REGULAR_OPEN = dt.time(9, 30)


def _empty(name: str, note: str, fields: dict | None = None) -> DetectorResult:
    return DetectorResult(name, None, fields or {}, quality="degraded", notes=note)


# ---------- 13. opening_range ----------

@register("opening_range")
def opening_range(bars: pd.DataFrame | None = None, *, minutes: int = 30, **_: Any) -> DetectorResult:
    """High/low of the first `minutes` after 09:30 ET. value = the range high.
    fields: high, low, width, width_pct, complete, broke_up / broke_down (of the bars after the window)."""
    if bars is None or bars.empty:
        return _empty("opening_range", "no bars", {"high": None, "low": None})
    idx = bars.index
    t = pd.Series([x.time() for x in idx], index=idx)
    end = (dt.datetime.combine(dt.date(2000, 1, 1), REGULAR_OPEN) + dt.timedelta(minutes=minutes)).time()
    win = bars[(t >= REGULAR_OPEN) & (t < end)]
    if win.empty:
        return _empty("opening_range", "no regular-session bars in the opening window",
                      {"high": None, "low": None, "minutes": minutes})
    hi, lo = float(win["high"].max()), float(win["low"].min())
    after = bars[t >= end]
    last = float(bars["close"].iloc[-1])
    # "complete" means the whole window is in the frame; a partial window is a real reading, flagged
    complete = len(win) >= minutes or (after is not None and not after.empty)
    return DetectorResult("opening_range", round(hi, 4), {
        "high": round(hi, 4), "low": round(lo, 4), "width": round(hi - lo, 4),
        "width_pct": round((hi - lo) / lo * 100, 3) if lo else None,
        "minutes": minutes, "bars_in_window": len(win), "complete": bool(complete), "price": last,
        "broke_up": bool(not after.empty and float(after["high"].max()) > hi),
        "broke_down": bool(not after.empty and float(after["low"].min()) < lo),
        "inside": bool(lo <= last <= hi),
    }, quality="exact" if complete else "approximate",
        notes="" if complete else f"opening range still forming ({len(win)} of {minutes} minutes)")


# ---------- 17. breakout_52w_base ----------

@register("breakout_52w_base")
def breakout_52w_base(bars: pd.DataFrame | None = None, *, daily: pd.DataFrame | None = None,
                      price: float | None = None, proximity_pct: float = 3.0, base_min_days: int = 60,
                      base_range_pct: float = 15.0, **_: Any) -> DetectorResult:
    """Proximity to the 52-week high plus a multi-month base/consolidation breakout.

    Base = the last `base_min_days` sessions BEFORE today whose high/low range is at most
    `base_range_pct` of the base low. value = True when price is near the 52-week high or is breaking
    out of such a base. A frame shorter than a year is `approximate` and says how much history it had.
    """
    if daily is None or daily.empty:
        return _empty("breakout_52w_base", "no daily bars", {"near_52w_high": False, "base_breakout": False})
    px = price if price is not None else float(daily["close"].iloc[-1])
    yr = daily.tail(252)
    hi52, lo52 = float(yr["high"].max()), float(yr["low"].min())
    near = hi52 > 0 and (hi52 - px) / hi52 * 100 <= proximity_pct
    base = daily.tail(base_min_days)
    base_hi, base_lo = float(base["high"].max()), float(base["low"].min())
    rng_pct = (base_hi - base_lo) / base_lo * 100 if base_lo else None
    tight = rng_pct is not None and len(base) >= base_min_days and rng_pct <= base_range_pct
    breakout = bool(tight and px > base_hi)
    q = "exact" if len(daily) >= 252 else "approximate"
    return DetectorResult("breakout_52w_base", bool(near or breakout), {
        "price": px, "high_52w": round(hi52, 4), "low_52w": round(lo52, 4),
        "pct_from_52w_high": round((hi52 - px) / hi52 * 100, 3) if hi52 else None,
        "pct_off_52w_low": round((px - lo52) / lo52 * 100, 3) if lo52 else None,
        "near_52w_high": bool(near), "proximity_pct": proximity_pct,
        "base_days": len(base), "base_high": round(base_hi, 4), "base_low": round(base_lo, 4),
        "base_range_pct": round(rng_pct, 3) if rng_pct is not None else None,
        "base_is_tight": bool(tight), "base_breakout": breakout,
    }, quality=q,
        notes="" if q == "exact" else f"only {len(daily)} sessions of history - 52-week levels are partial")


# ---------- 19. double_top_bottom ----------

@register("double_top_bottom")
def double_top_bottom(bars: pd.DataFrame | None = None, *, tolerance_pct: float = 0.3, swing_lookback: int = 5,
                      **_: Any) -> DetectorResult:
    """Two confirmed swing highs (or lows) within `tolerance_pct` of each other.
    value = True when either pattern is present. fields keep top and bottom separate."""
    need = swing_lookback * 2 + 3
    if bars is None or len(bars) < need:
        return _empty("double_top_bottom", f"need at least {need} bars to confirm two swing points",
                      {"double_top": False, "double_bottom": False})
    low_idx, _ = swing_points(bars["low"], swing_lookback)
    _, high_idx = swing_points(bars["high"], swing_lookback)

    def _pair(series: pd.Series, idx: list[int]) -> dict | None:
        if len(idx) < 2:
            return None
        a, b = idx[-2], idx[-1]
        pa, pb = float(series.iloc[a]), float(series.iloc[b])
        diff = abs(pb - pa) / pa * 100 if pa else None
        return {"first": round(pa, 4), "second": round(pb, 4), "diff_pct": round(diff, 3) if diff is not None else None,
                "bars_apart": b - a, "bars_since": len(series) - 1 - b,
                "match": diff is not None and diff <= tolerance_pct}

    top = _pair(bars["high"], high_idx)
    bot = _pair(bars["low"], low_idx)
    is_top = bool(top and top["match"])
    is_bot = bool(bot and bot["match"])
    return DetectorResult("double_top_bottom", is_top or is_bot, {
        "double_top": is_top, "double_bottom": is_bot, "top": top, "bottom": bot,
        "tolerance_pct": tolerance_pct, "swing_lookback": swing_lookback,
    })


# ---------- 20. sector_relative_strength ----------

SECTOR_ETFS = {"XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Health Care",
               "XLY": "Consumer Discretionary", "XLP": "Consumer Staples", "XLI": "Industrials",
               "XLB": "Materials", "XLU": "Utilities", "XLRE": "Real Estate", "XLC": "Communication Services"}


@register("sector_relative_strength")
def sector_relative_strength(bars: pd.DataFrame | None = None, *, sector_daily: dict[str, pd.DataFrame] | None = None,
                             sector_etf: str | None = None, daily: pd.DataFrame | None = None,
                             lookback_days: int = 5, **_: Any) -> DetectorResult:
    """Rank the sector ETFs over `lookback_days` and place this symbol's sector in that ranking.

    `sector_daily` = {etf: daily OHLCV frame}; `sector_etf` = the symbol's sector ETF. Without the
    sector mapping the ranking is still returned (it is market context) but the symbol-level fields
    report unknown rather than a guess.
    """
    if not sector_daily:
        return _empty("sector_relative_strength", "no sector ETF frames supplied",
                      {"rank": None, "sector_etf": sector_etf})
    perf: dict[str, float] = {}
    for etf, f in sector_daily.items():
        if f is None or len(f) < 2:
            continue
        tail = f["close"].tail(lookback_days + 1)
        if len(tail) < 2 or not float(tail.iloc[0]):
            continue
        perf[etf] = round((float(tail.iloc[-1]) / float(tail.iloc[0]) - 1) * 100, 3)
    if not perf:
        return _empty("sector_relative_strength", "sector frames had no usable history",
                      {"rank": None, "sector_etf": sector_etf})
    ranked = sorted(perf.items(), key=lambda kv: kv[1], reverse=True)
    order = [e for e, _ in ranked]
    fields: dict[str, Any] = {
        "lookback_days": lookback_days, "ranking": [{"etf": e, "name": SECTOR_ETFS.get(e, e), "pct": p}
                                                    for e, p in ranked],
        "strongest": order[0], "weakest": order[-1], "sector_etf": sector_etf,
        "rank": None, "percentile": None, "sector_pct": None, "symbol_pct": None, "rs_vs_sector": None,
    }
    if sector_etf and sector_etf in perf:
        r = order.index(sector_etf) + 1
        fields.update({"rank": r, "of": len(order), "sector_pct": perf[sector_etf],
                       "percentile": round((len(order) - r) / (len(order) - 1) * 100, 1) if len(order) > 1 else None,
                       "in_strong_sector": r <= max(1, len(order) // 3),
                       "in_weak_sector": r > len(order) - max(1, len(order) // 3)})
        if daily is not None and len(daily) >= 2:
            t = daily["close"].tail(lookback_days + 1)
            if len(t) >= 2 and float(t.iloc[0]):
                sp = round((float(t.iloc[-1]) / float(t.iloc[0]) - 1) * 100, 3)
                fields.update({"symbol_pct": sp, "rs_vs_sector": round(sp - perf[sector_etf], 3)})
        value: Any = fields["percentile"]
        q = "exact"
        note = ""
    else:
        value = None
        q = "degraded"
        note = "sector for this symbol is unknown - ranking is market context only"
    return DetectorResult("sector_relative_strength", value, fields, quality=q, notes=note)


# ---------- 22. cvd ----------

@register("cvd")
def cvd(bars: pd.DataFrame | None = None, *, tier: int = 1, **_: Any) -> DetectorResult:
    """Cumulative volume delta. DISPLAY ONLY in v1 (plan §27) — never an entry rule.

    Tier 1 (the only tier the free plan supports) estimates each bar's delta from where the close sits
    inside the bar's range: delta = volume * (2 * (close - low) / (high - low) - 1). That is an
    approximation of the tape, not the tape, so the result is always `approximate` and says so.
    """
    if bars is None or bars.empty:
        return _empty("cvd", "no bars", {"tier": tier})
    if tier != 1:
        return _empty("cvd", f"tier {tier} needs full consolidated SIP trade data (plan §27) - not on the free plan",
                      {"tier": tier})
    hi, lo, c, v = bars["high"], bars["low"], bars["close"], bars["volume"]
    rng = (hi - lo).replace(0, np.nan)
    pos = ((c - lo) / rng).fillna(0.5)
    delta = (v * (2 * pos - 1)).round()
    series = delta.cumsum()
    tot = float(series.iloc[-1])
    return DetectorResult("cvd", round(tot, 1), {
        "tier": tier, "bar_delta": round(float(delta.iloc[-1]), 1),
        "direction": "buy" if tot > 0 else ("sell" if tot < 0 else "flat"),
        "pct_of_volume": round(tot / float(v.sum()) * 100, 2) if float(v.sum()) else None,
        "bars": len(bars), "display_only": True,
    }, series=series, quality="approximate",
        notes="bar-based approximation of the tape (plan §27 tier 1) - display only, never an entry rule")


# ---------- 23. volume_profile ----------

@register("volume_profile")
def volume_profile(bars: pd.DataFrame | None = None, *, bins: int = 50, value_area_pct: float = 70.0,
                   scope: str = "prior_day", feed: str = "sip", **_: Any) -> DetectorResult:
    """POC / VAH / VAL from volume binned by price. value = POC.

    Each bar's volume is spread evenly across the price bins its high-low range covers — the honest
    approximation available from bars (a true profile needs the tape). Developing intraday profiles on
    IEX are additionally `approximate` because IEX sees only a slice of the volume.
    """
    if bars is None or bars.empty or float(bars["volume"].sum()) <= 0:
        return _empty("volume_profile", "no bars or no volume", {"poc": None, "vah": None, "val": None})
    lo, hi = float(bars["low"].min()), float(bars["high"].max())
    if hi <= lo:
        return _empty("volume_profile", "flat price range", {"poc": lo, "vah": lo, "val": lo})
    edges = np.linspace(lo, hi, bins + 1)
    centres = (edges[:-1] + edges[1:]) / 2
    hist = np.zeros(bins)
    step = (hi - lo) / bins
    for b_lo, b_hi, vol in zip(bars["low"].to_numpy(), bars["high"].to_numpy(), bars["volume"].to_numpy()):
        i0 = min(int((b_lo - lo) / step), bins - 1)
        i1 = min(int((b_hi - lo) / step), bins - 1)
        hist[i0:i1 + 1] += vol / (i1 - i0 + 1)
    poc_i = int(hist.argmax())
    total = hist.sum()
    target = total * value_area_pct / 100.0
    lo_i = hi_i = poc_i
    acc = hist[poc_i]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        down = hist[lo_i - 1] if lo_i > 0 else -1.0
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        if up >= down:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    q = "exact" if (feed == "sip" and scope != "intraday") else "approximate"
    return DetectorResult("volume_profile", round(float(centres[poc_i]), 4), {
        "poc": round(float(centres[poc_i]), 4),
        "vah": round(float(edges[hi_i + 1]), 4), "val": round(float(edges[lo_i]), 4),
        "high": round(hi, 4), "low": round(lo, 4), "bins": bins, "value_area_pct": value_area_pct,
        "value_area_volume_pct": round(float(acc / total * 100), 2), "scope": scope, "feed": feed,
        "price": round(float(bars["close"].iloc[-1]), 4),
        "in_value_area": bool(edges[lo_i] <= float(bars["close"].iloc[-1]) <= edges[hi_i + 1]),
        "bars": len(bars),
    }, quality=q,
        notes="volume spread evenly across each bar's range - a true profile needs the tape"
              + ("" if feed == "sip" else "; IEX sees only part of the volume"))
