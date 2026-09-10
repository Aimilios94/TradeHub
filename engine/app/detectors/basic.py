"""Phase 2 detectors — the price/volume/level plugins the 08:00 scanner needs (plan §9 items 8–12, 14–16, 18).

Contract (see app/detectors/__init__.py): pure functions, no I/O.
  bars   : pd.DataFrame OHLCV indexed by ET timestamp — the *intraday* frame (pre-market 1-min bars) or None
  daily  : pd.DataFrame OHLCV indexed by trade date (ascending) — passed as a keyword where needed
  stats  : dict — the symbol_stats row (nightly universe job), used for floats / fallbacks
Every result carries `quality`: exact | approximate | degraded, and `fields` addressable via Condition.field.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.detectors import DetectorResult, register


def _last_close(bars: pd.DataFrame | None) -> float | None:
    if bars is None or bars.empty:
        return None
    return float(bars["close"].iloc[-1])


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


# ---------- 11. gap_pct ----------

@register("gap_pct")
def gap_pct(bars: pd.DataFrame | None = None, *, prev_close: float | None = None, price: float | None = None,
            **_: Any) -> DetectorResult:
    """Gap % of the latest pre-market price (or `price`) vs prior close. fields: abs, price, prev_close."""
    px = price if price is not None else _last_close(bars)
    if _nan(px) or _nan(prev_close) or prev_close <= 0:
        return DetectorResult("gap_pct", None, {"abs": None, "price": px, "prev_close": prev_close}, quality="degraded",
                              notes="no pre-market print or no prior close")
    g = (px / prev_close - 1.0) * 100.0
    return DetectorResult("gap_pct", round(g, 3), {"abs": round(abs(g), 3), "price": px, "prev_close": prev_close,
                                                   "direction": "up" if g >= 0 else "down"})


# ---------- 9. adv ----------

@register("adv")
def adv(bars: pd.DataFrame | None = None, *, daily: pd.DataFrame | None = None, lookback_days: int = 20,
        **_: Any) -> DetectorResult:
    """Average daily volume over the last `lookback_days` sessions (daily frame excludes today)."""
    if daily is None or daily.empty:
        return DetectorResult("adv", None, {"sessions": 0}, quality="degraded", notes="no daily bars")
    tail = daily["volume"].tail(lookback_days)
    q = "exact" if len(tail) >= lookback_days else "approximate"
    return DetectorResult("adv", int(round(tail.mean())), {"sessions": len(tail), "lookback_days": lookback_days},
                          quality=q)


# ---------- 8. atr ----------

def wilder_atr(daily: pd.DataFrame, period: int = 14) -> float | None:
    if daily is None or len(daily) < 2:
        return None
    h, low, c = daily["high"], daily["low"], daily["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - low, (h - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1).iloc[1:]
    if tr.empty:
        return None
    if len(tr) < period:
        return float(tr.mean())
    atr_val = tr.iloc[:period].mean()
    for v in tr.iloc[period:]:
        atr_val = (atr_val * (period - 1) + v) / period
    return float(atr_val)


@register("atr")
def atr(bars: pd.DataFrame | None = None, *, daily: pd.DataFrame | None = None, period: int = 14,
        **_: Any) -> DetectorResult:
    v = wilder_atr(daily, period) if daily is not None else None
    if v is None:
        return DetectorResult("atr", None, {}, quality="degraded", notes="no daily bars")
    q = "exact" if len(daily) > period else "approximate"
    px = float(daily["close"].iloc[-1])
    return DetectorResult("atr", round(v, 4), {"period": period, "atr_pct": round(v / px * 100, 3) if px else None},
                          quality=q)


# ---------- 15. prior_day_levels ----------

@register("prior_day_levels")
def prior_day_levels(bars: pd.DataFrame | None = None, *, daily: pd.DataFrame | None = None, **_: Any) -> DetectorResult:
    if daily is None or daily.empty:
        return DetectorResult("prior_day_levels", None, {}, quality="degraded", notes="no daily bars")
    r = daily.iloc[-1]
    f = {"high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"]), "open": float(r["open"]),
         "date": str(daily.index[-1].date() if hasattr(daily.index[-1], "date") else daily.index[-1])}
    return DetectorResult("prior_day_levels", f["close"], f)


# ---------- 16. premarket_high_low ----------

@register("premarket_high_low")
def premarket_high_low(bars: pd.DataFrame | None = None, **_: Any) -> DetectorResult:
    if bars is None or bars.empty:
        return DetectorResult("premarket_high_low", None, {"high": None, "low": None, "volume": 0, "print_count": 0},
                              quality="degraded", notes="no pre-market bars")
    f = {"high": float(bars["high"].max()), "low": float(bars["low"].min()), "last": float(bars["close"].iloc[-1]),
         "volume": int(bars["volume"].sum()), "print_count": len(bars),
         "first_ts": bars.index[0].isoformat(), "last_ts": bars.index[-1].isoformat()}
    return DetectorResult("premarket_high_low", f["high"], f)


# ---------- 14. nbar_high_low ----------

@register("nbar_high_low")
def nbar_high_low(bars: pd.DataFrame | None = None, *, daily: pd.DataFrame | None = None, n: int = 20,
                  **_: Any) -> DetectorResult:
    if daily is None or daily.empty:
        return DetectorResult("nbar_high_low", None, {}, quality="degraded", notes="no daily bars")
    t = daily.tail(n)
    return DetectorResult("nbar_high_low", float(t["high"].max()), {"high": float(t["high"].max()),
                          "low": float(t["low"].min()), "n": n, "sessions": len(t)},
                          quality="exact" if len(t) >= n else "approximate")


# ---------- 10. relative_volume_premarket ----------

@register("relative_volume_premarket")
def relative_volume_premarket(bars: pd.DataFrame | None = None, *, baseline_avg: float | None = None,
                              baseline_days_used: int = 0, baseline_days: int = 14, checkpoint: str | None = None,
                              feed: str = "sip", adv_value: float | None = None, **_: Any) -> DetectorResult:
    """Cumulative pre-market volume ÷ same-feed, same-checkpoint N-day average (Phase 1 baseline table).
    fields: volume, pct_of_adv, baseline_avg, days_used, checkpoint, feed, print_count."""
    vol = int(bars["volume"].sum()) if bars is not None and not bars.empty else 0
    prints = len(bars) if bars is not None else 0
    ratio = round(vol / baseline_avg, 3) if baseline_avg and baseline_avg > 0 else None
    pct_adv = round(vol / adv_value * 100, 3) if adv_value and adv_value > 0 else None
    q = "exact" if baseline_days_used >= baseline_days else ("approximate" if baseline_days_used else "degraded")
    return DetectorResult("relative_volume_premarket", ratio,
                          {"volume": vol, "pct_of_adv": pct_adv, "baseline_avg": baseline_avg,
                           "days_used": baseline_days_used, "checkpoint": checkpoint, "feed": feed,
                           "print_count": prints}, quality=q,
                          notes="" if ratio is not None else "no baseline yet — run the nightly job")


# ---------- 12. float_shares ----------

@register("float_shares")
def float_shares(bars: pd.DataFrame | None = None, *, stats: dict | None = None, **_: Any) -> DetectorResult:
    """Float from symbol_stats: Finviz Elite when present, else FMP shares outstanding (approximate), else unknown."""
    s = stats or {}
    if s.get("float_shares"):
        return DetectorResult("float_shares", int(s["float_shares"]), {"source": s.get("float_source") or "finviz"})
    if s.get("shares_outstanding"):
        return DetectorResult("float_shares", int(s["shares_outstanding"]), {"source": "fmp_shares_outstanding"},
                              quality="approximate", notes="shares outstanding ≥ float; true float needs Finviz Elite")
    return DetectorResult("float_shares", None, {"source": None}, quality="degraded", notes="float unknown")


# ---------- 18. distance_to_level_atr ----------

@register("distance_to_level_atr")
def distance_to_level_atr(bars: pd.DataFrame | None = None, *, price: float | None = None, atr_value: float | None = None,
                          levels: dict[str, float] | None = None, round_numbers: bool = True, max_atr: float = 1.5,
                          **_: Any) -> DetectorResult:
    """Distance from `price` to the nearest major level in ATR units (plan §8 F-A structure filter).
    `levels` = {name: price}; round-number levels ($1/$5/$10 steps by price band) are added when enabled."""
    px = price if price is not None else _last_close(bars)
    if _nan(px) or not atr_value or atr_value <= 0:
        return DetectorResult("distance_to_level_atr", None, {}, quality="degraded", notes="no price or ATR")
    lv = dict(levels or {})
    if round_numbers:
        step = 1.0 if px < 20 else (5.0 if px < 100 else 10.0)
        lo = math.floor(px / step) * step
        lv[f"round_{lo:g}"] = lo
        lv[f"round_{lo + step:g}"] = lo + step
    lv = {k: float(v) for k, v in lv.items() if v is not None and not _nan(v)}
    if not lv:
        return DetectorResult("distance_to_level_atr", None, {}, quality="degraded", notes="no levels")
    name, level = min(lv.items(), key=lambda kv: abs(px - kv[1]))
    d = abs(px - level) / atr_value
    return DetectorResult("distance_to_level_atr", round(d, 3), {"nearest_level": level, "level_name": name,
                          "price": px, "atr": atr_value, "within": d <= max_atr, "max_atr": max_atr})
