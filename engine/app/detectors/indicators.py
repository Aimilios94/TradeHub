"""Phase 7 oscillator / moving-average detectors (plan §9 items 1-7, 21).

The HPS (Kurisko) family lives here: the four stochastic bands 9-3, 14-3, 40-4, 60-10, the quad
extreme, embedded readings and swing divergence. Contract as in app/detectors/__init__.py —
pure functions over an OHLCV frame indexed by ET timestamp, no I/O.

Convention for the stochastic family: the *band value* is %K after `smooth_k` smoothing (the line
Kurisko's charts read); %D is always exposed alongside it in `fields` so a config can reference it.
Every result carries its series so the backtester can evaluate a bar without recomputing.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.detectors import DetectorResult, register

QUAD_BANDS = ((9, 3), (14, 3), (40, 4), (60, 10))


def _empty(name: str, note: str, fields: dict | None = None) -> DetectorResult:
    return DetectorResult(name, None, fields or {}, quality="degraded", notes=note)


def _ok(bars: pd.DataFrame | None, need: int) -> bool:
    return bars is not None and not bars.empty and len(bars) >= need


# ---------- 1. ema ----------

def ema_series(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


@register("ema")
def ema(bars: pd.DataFrame | None = None, *, period: int = 20, **_: Any) -> DetectorResult:
    """EMA of close. fields: price_above / price_below / price_above_or_at / price_below_or_at, distance_pct."""
    if not _ok(bars, 2):
        return _empty("ema", "no bars")
    s = ema_series(bars["close"], period)
    v, px = float(s.iloc[-1]), float(bars["close"].iloc[-1])
    q = "exact" if len(bars) >= period * 3 else "approximate"
    return DetectorResult("ema", round(v, 4), {
        "period": period, "price": px, "ema": round(v, 4),
        "price_above": px > v, "price_below": px < v,
        "price_above_or_at": px >= v, "price_below_or_at": px <= v,
        "distance_pct": round((px / v - 1) * 100, 3) if v else None,
        "bars": len(bars),
    }, series=s, quality=q,
        notes="" if q == "exact" else f"only {len(bars)} bars for a {period}-period EMA - still warming up")


# ---------- 2. rsi ----------

def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0.0)
    loss = (-d).clip(lower=0.0)
    ag = gain.ewm(alpha=1 / period, adjust=False).mean()
    al = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = ag / al.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(al != 0, 100.0).fillna(50.0)


@register("rsi")
def rsi(bars: pd.DataFrame | None = None, *, period: int = 14, **_: Any) -> DetectorResult:
    if not _ok(bars, period + 1):
        return _empty("rsi", f"need more than {period} bars")
    s = rsi_series(bars["close"], period)
    v = float(s.iloc[-1])
    return DetectorResult("rsi", round(v, 3), {"period": period, "overbought": v >= 70, "oversold": v <= 30},
                          series=s)


# ---------- 3. vwap ----------

ANCHORS = {"session": (9, 30), "premarket": (4, 0)}


def vwap_series(bars: pd.DataFrame, anchor: str = "session") -> pd.Series:
    """Cumulative typical-price VWAP, reset at the anchor time of each ET session in the frame."""
    hh, mm = ANCHORS.get(anchor, (9, 30))
    idx = bars.index
    after = (idx.hour > hh) | ((idx.hour == hh) & (idx.minute >= mm))
    # group by (date, past-the-anchor) so a pre-market block and the regular session never mix
    groups = pd.Series(list(zip(idx.date, after)), index=idx)
    tp = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    pv = (tp * bars["volume"]).groupby(groups).cumsum()
    vv = bars["volume"].groupby(groups).cumsum()
    return (pv / vv.replace(0, np.nan)).ffill()


@register("vwap")
def vwap(bars: pd.DataFrame | None = None, *, anchor: str = "session", feed: str = "sip",
         **_: Any) -> DetectorResult:
    """Session-anchored VWAP. Exact on SIP (full tape); approximate on IEX, which sees a slice of the volume."""
    if not _ok(bars, 1):
        return _empty("vwap", "no bars")
    s = vwap_series(bars, anchor)
    v = s.iloc[-1]
    if pd.isna(v):
        return _empty("vwap", "no volume in the anchored window")
    px = float(bars["close"].iloc[-1])
    q = "exact" if feed == "sip" else "approximate"
    return DetectorResult("vwap", round(float(v), 4), {
        "anchor": anchor, "price": px, "price_above": px > float(v), "price_below": px < float(v),
        "distance_pct": round((px / float(v) - 1) * 100, 3), "feed": feed,
    }, series=s, quality=q,
        notes="" if q == "exact" else "IEX volume is a slice of the tape - VWAP is approximate")


# ---------- 4. stochastic ----------

def stoch_frame(bars: pd.DataFrame, k: int = 9, d: int = 3, smooth_k: int = 1,
                cache: dict | None = None) -> pd.DataFrame:
    """%K (after `smooth_k` smoothing) and %D. A flat window (high == low) holds the previous value.

    `cache` is an optional {(k, d, smooth_k): full-session frame} the backtester fills once per
    symbol-day. Every window here is ROLLING, so a value at bar i depends only on bars at or before i:
    slicing a frame computed over the whole session gives byte-identical numbers to recomputing on
    `bars[:i+1]`, with no lookahead, and turns an O(n²) walk into an O(n) one. The cache is used only
    when its index actually lines up with the slice, so a mismatched frame falls through to a real
    computation rather than returning someone else's numbers.
    """
    if cache is not None:
        full = cache.get((k, d, smooth_k))
        n = len(bars)
        if full is not None and n and len(full) >= n and full.index[n - 1] == bars.index[-1]:
            return full.iloc[:n]
    hh = bars["high"].rolling(k, min_periods=1).max()
    ll = bars["low"].rolling(k, min_periods=1).min()
    rng = hh - ll
    raw = (bars["close"] - ll) / rng.where(rng > 0, np.nan) * 100.0
    raw = raw.ffill().fillna(50.0)
    kk = raw.rolling(smooth_k, min_periods=1).mean() if smooth_k > 1 else raw
    dd = kk.rolling(d, min_periods=1).mean()
    return pd.DataFrame({"k": kk, "d": dd}, index=bars.index)


@register("stochastic")
def stochastic(bars: pd.DataFrame | None = None, *, k: int = 9, d: int = 3, smooth_k: int = 1,
               stoch_cache: dict | None = None, **_: Any) -> DetectorResult:
    """Stochastic oscillator. value = %K. fields: k, d, band ("9-3"), overbought / oversold, rising."""
    if not _ok(bars, 2):
        return _empty("stochastic", "no bars", {"k": None, "d": None})
    f = stoch_frame(bars, k, d, smooth_k, stoch_cache)
    kv, dv = float(f["k"].iloc[-1]), float(f["d"].iloc[-1])
    q = "exact" if len(bars) >= k + d else "approximate"
    return DetectorResult("stochastic", round(kv, 3), {
        "k": round(kv, 3), "d": round(dv, 3), "band": f"{k}-{d}",
        "overbought": kv >= 80, "oversold": kv <= 20,
        "rising": bool(f["k"].iloc[-1] > f["k"].iloc[-2]) if len(f) > 1 else None,
        "bars": len(bars),
    }, series=f, quality=q,
        notes="" if q == "exact" else f"only {len(bars)} bars for a {k}-{d} stochastic")


# ---------- 5. stoch_quad_extreme ----------

@register("stoch_quad_extreme")
def stoch_quad_extreme(bars: pd.DataFrame | None = None, *, bands: list | tuple = QUAD_BANDS, side: str = "under",
                       threshold: float = 20, stoch_cache: dict | None = None, **_: Any) -> DetectorResult:
    """All four bands beyond the threshold at the latest bar (quad-under-20 / quad-over-80).
    value = bool. fields: the value of every band, how_many are beyond, the extreme reading."""
    bl = [tuple(b) for b in bands]
    need = max(k for k, _ in bl) + 1
    if not _ok(bars, 2):
        return _empty("stoch_quad_extreme", "no bars", {"values": {}, "how_many": 0})
    vals: dict[str, float] = {}
    for k, d in bl:
        f = stoch_frame(bars, k, d, 1, stoch_cache)
        vals[f"{k}-{d}"] = round(float(f["k"].iloc[-1]), 3)
    beyond = [n for n, v in vals.items() if (v < threshold if side == "under" else v > threshold)]
    q = "exact" if len(bars) >= need else "approximate"
    return DetectorResult("stoch_quad_extreme", len(beyond) == len(bl), {
        "values": vals, "how_many": len(beyond), "of": len(bl), "beyond": beyond,
        "side": side, "threshold": threshold,
        "extreme": round(max(vals.values()) if side == "over" else min(vals.values()), 3),
    }, quality=q,
        notes="" if q == "exact" else f"the {need - 1}-period band is still warming up ({len(bars)} bars)")


# ---------- 7. stoch_embedded ----------

@register("stoch_embedded")
def stoch_embedded(bars: pd.DataFrame | None = None, *, k: int = 60, d: int = 10, side: str = "over",
                   threshold: float = 85, min_bars: int = 3, stoch_cache: dict | None = None,
                   **_: Any) -> DetectorResult:
    """Beyond `threshold` for at least `min_bars` CONSECUTIVE bars ending at the latest bar.
    Doubles as the trend-strength filter. value = bool. fields: run (bars so far), value, band."""
    if not _ok(bars, 2):
        return _empty("stoch_embedded", "no bars", {"run": 0})
    f = stoch_frame(bars, k, d, 1, stoch_cache)
    kk = f["k"]
    cond = kk > threshold if side == "over" else kk < threshold
    run = 0
    for v in reversed(cond.tolist()):
        if not v:
            break
        run += 1
    q = "exact" if len(bars) >= k + d else "approximate"
    return DetectorResult("stoch_embedded", run >= min_bars, {
        "run": run, "min_bars": min_bars, "value": round(float(kk.iloc[-1]), 3), "band": f"{k}-{d}",
        "side": side, "threshold": threshold,
    }, quality=q, notes="" if q == "exact" else f"only {len(bars)} bars for a {k}-{d} stochastic")


# ---------- 6. stoch_divergence ----------

def swing_points(s: pd.Series, lookback: int = 5) -> tuple[list[int], list[int]]:
    """Indices of CONFIRMED swing lows and swing highs — a bar at the extreme of its `lookback`
    neighbourhood on both sides. Confirmation costs `lookback` bars of lag; that lag is real and
    is not removed, so a backtest never sees a swing before the market did."""
    lows: list[int] = []
    highs: list[int] = []
    v = s.to_numpy(dtype=float)
    for i in range(lookback, len(v) - lookback):
        w = v[i - lookback:i + lookback + 1]
        c = v[i]
        if c == w.min() and (w > c).sum() >= 2 * lookback - 1:
            lows.append(i)
        if c == w.max() and (w < c).sum() >= 2 * lookback - 1:
            highs.append(i)
    return lows, highs


@register("stoch_divergence")
def stoch_divergence(bars: pd.DataFrame | None = None, *, k: int = 9, d: int = 3, swing_lookback: int = 5,
                     trigger_level: float = 20, side: str = "bullish", stoch_cache: dict | None = None,
                     **_: Any) -> DetectorResult:
    """Swing-point divergence between price and the k-d stochastic, plus the trigger.

    Bullish: price puts in an equal-or-lower low while the stochastic puts in a HIGHER low, and %K then
    turns up through `trigger_level`. Bearish is the mirror. value = divergence AND trigger; `fields`
    keeps the two apart so an entry stage can test either on its own.

    The second leg is deliberately NOT required to be a confirmed swing point. Confirmation costs
    `swing_lookback` bars on the right-hand side, and the 9-3 crosses 20 within a bar or two of the
    low — so demanding both would make the trigger unreachable by construction, and the detector would
    return False forever while looking like it worked. Two shapes are therefore checked:

      * the last two CONFIRMED swing points (the textbook double bottom, lows far apart), and
      * the last confirmed swing versus the RUNNING low since it — which is what a trader is looking at
        while the second low is still forming.

    Both are causal: the running low up to this bar is knowable at this bar. The first divergence found
    is reported, with `shape` naming which one it was.
    """
    if not _ok(bars, swing_lookback * 2 + 3):
        return _empty("stoch_divergence", "not enough bars to confirm a swing point",
                      {"divergence": False, "trigger": False})
    f = stoch_frame(bars, k, d, 1, stoch_cache)
    kk = f["k"]
    price = bars["low"] if side == "bullish" else bars["high"]
    bullish = side == "bullish"
    lows, highs = swing_points(price, swing_lookback)
    pts = lows if bullish else highs
    prev, now = float(kk.iloc[-2]), float(kk.iloc[-1])
    trig = (prev <= trigger_level < now) if bullish else (prev >= trigger_level > now)
    fields: dict[str, Any] = {"side": side, "band": f"{k}-{d}", "swing_points": len(pts),
                              "divergence": False, "trigger": bool(trig),
                              "k": round(float(kk.iloc[-1]), 3), "trigger_level": trigger_level,
                              "shape": None}
    if not pts:
        return DetectorResult("stoch_divergence", False, fields, quality="approximate",
                              notes="no confirmed swing point in this window")

    def _diverges(a: int, b: int) -> bool:
        pa, pb = float(price.iloc[a]), float(price.iloc[b])
        sa, sb = float(kk.iloc[a]), float(kk.iloc[b])
        return (pb <= pa and sb > sa) if bullish else (pb >= pa and sb < sa)

    pairs: list[tuple[str, int, int]] = []
    if len(pts) >= 2:
        pairs.append(("two confirmed swings", pts[-2], pts[-1]))
    seg = price.iloc[pts[-1] + 1:]
    if len(seg) >= 1:
        rel = int(seg.to_numpy().argmin() if bullish else seg.to_numpy().argmax())
        pairs.append(("swing vs the running low" if bullish else "swing vs the running high",
                      pts[-1], pts[-1] + 1 + rel))

    for shape, a, b in pairs:
        if not _diverges(a, b):
            continue
        fields.update({
            "divergence": True, "shape": shape,
            "prior_swing": {"price": round(float(price.iloc[a]), 4), "stoch": round(float(kk.iloc[a]), 3),
                            "ts": str(price.index[a])},
            "recent_swing": {"price": round(float(price.iloc[b]), 4), "stoch": round(float(kk.iloc[b]), 3),
                             "ts": str(price.index[b])},
            "bars_since_swing": len(price) - 1 - b})
        return DetectorResult("stoch_divergence", bool(trig), fields)

    a = pts[-1]
    fields["prior_swing"] = {"price": round(float(price.iloc[a]), 4), "stoch": round(float(kk.iloc[a]), 3),
                             "ts": str(price.index[a])}
    return DetectorResult("stoch_divergence", False, fields)


# ---------- 21. flagpole_steepness ----------

@register("flagpole_steepness")
def flagpole_steepness(bars: pd.DataFrame | None = None, *, ema_period: int = 20, baseline_bars: int = 50,
                       min_multiple: float = 1.5, side: str = "long", **_: Any) -> DetectorResult:
    """Steepness of the move off the EMA versus this symbol's own recent slope baseline (S5 bull flag).

    Pole = from the last bar that touched the EMA to the extreme reached since. Slope is dollars per bar
    divided by the mean absolute per-bar move over `baseline_bars`, so it is comparable across symbols
    and price levels. value = bool (at least `min_multiple`); the multiple itself is in `fields`.
    """
    need = max(ema_period, baseline_bars) + 2
    if not _ok(bars, 5):
        return _empty("flagpole_steepness", "no bars", {"multiple": None})
    e = ema_series(bars["close"], ema_period)
    close, high, low = bars["close"], bars["high"], bars["low"]
    touched = (low <= e) if side == "long" else (high >= e)
    idx = np.flatnonzero(touched.to_numpy())
    if idx.size == 0:
        return _empty("flagpole_steepness", f"price never touched the {ema_period} EMA in this window",
                      {"multiple": None})
    origin = int(idx[-1])
    bars_since = len(close) - 1 - origin
    if bars_since < 1:
        return DetectorResult("flagpole_steepness", False,
                              {"multiple": 0.0, "bars_since_ema_touch": 0, "ema_period": ema_period,
                               "min_multiple": min_multiple},
                              notes="price is at the EMA now - there is no pole yet")
    extreme = float(high.iloc[origin:].max()) if side == "long" else float(low.iloc[origin:].min())
    base_px = float(close.iloc[origin])
    move = (extreme - base_px) if side == "long" else (base_px - extreme)
    pole_slope = move / bars_since
    baseline = float(close.diff().abs().tail(baseline_bars).mean())
    if not baseline or np.isnan(baseline) or baseline <= 0:
        return _empty("flagpole_steepness", "flat baseline - cannot compare slopes", {"multiple": None})
    mult = pole_slope / baseline
    q = "exact" if len(bars) >= need else "approximate"
    return DetectorResult("flagpole_steepness", bool(mult >= min_multiple), {
        "multiple": round(float(mult), 3), "min_multiple": min_multiple, "pole_slope": round(pole_slope, 5),
        "baseline_slope": round(baseline, 5), "bars_since_ema_touch": bars_since,
        "pole_pct": round(move / base_px * 100, 3) if base_px else None,
        "ema_period": ema_period, "side": side,
    }, quality=q, notes="" if q == "exact" else f"baseline uses {min(len(bars), baseline_bars)} bars")
