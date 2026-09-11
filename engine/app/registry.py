"""Engine plugin registry — the SINGLE SOURCE OF TRUTH for detector and exit-plugin names.

Plan §9. The database table `plugin_registry` is generated FROM this file
(`scripts/gen_seed.py`), and every strategy config is validated against it
(`app/models.py`). Adding a plugin = add an entry here + implement it under
`app/detectors/` or `app/exits/` + regenerate the seed. As of Phase 7 every entry is `implemented`;
a test asserts the registry and the code agree, so "planned" can never drift back in unnoticed.

`params` is a compact JSON-schema-like description: {name: {"type", "default", "desc"}}.
"""

from __future__ import annotations

from typing import Literal, TypedDict


class Plugin(TypedDict):
    number: int
    name: str
    kind: Literal["detector", "exit"]
    description: str
    params: dict
    status: Literal["planned", "implemented"]
    notes: str


def _p(type_: str, default, desc: str) -> dict:
    return {"type": type_, "default": default, "desc": desc}


DETECTORS: list[Plugin] = [
    {"number": 1, "name": "ema", "kind": "detector", "status": "implemented",
     "description": "Exponential moving average of close (20/50/200 or any period).",
     "params": {"period": _p("int", 20, "EMA period")}, "notes": ""},
    {"number": 2, "name": "rsi", "kind": "detector", "status": "implemented",
     "description": "Relative strength index.",
     "params": {"period": _p("int", 14, "RSI period")}, "notes": ""},
    {"number": 3, "name": "vwap", "kind": "detector", "status": "implemented",
     "description": "Session-anchored VWAP (resets at 04:00 ET pre-market open or 09:30, param).",
     "params": {"anchor": _p("str", "session", "session | premarket"),
                "feed": _p("str", "sip", "sip = exact; iex sees a slice of the volume, so VWAP is approximate")},
     "notes": "Live VWAP on IEX feed is approximate (IEX-only volume); exact on SIP history."},
    {"number": 4, "name": "stochastic", "kind": "detector", "status": "implemented",
     "description": "Stochastic oscillator %K/%D. The HPS quad uses four parameter sets: 9-3, 14-3, 40-4, 60-10.",
     "params": {"k": _p("int", 9, "%K lookback"), "d": _p("int", 3, "%D smoothing"),
                "smooth_k": _p("int", 1, "%K smoothing")},
     "notes": "40-4 is canonical (PDF). '44-4' from the interview is a transcription error — never seed it."},
    {"number": 5, "name": "stoch_quad_extreme", "kind": "detector", "status": "implemented",
     "description": "All four stochastic bands beyond a threshold (quad-under-20 / quad-over-80).",
     "params": {"bands": _p("list", [[9, 3], [14, 3], [40, 4], [60, 10]], "the four (k,d) sets"),
                "side": _p("str", "under", "under | over"),
                "threshold": _p("float", 20, "20 for under, 80 for over")}, "notes": ""},
    {"number": 6, "name": "stoch_divergence", "kind": "detector", "status": "implemented",
     "description": "Swing-point divergence: price lower-low vs stochastic higher-low (bullish) and inverse.",
     "params": {"k": _p("int", 9, ""), "d": _p("int", 3, ""), "swing_lookback": _p("int", 5, "bars each side of a swing point"),
                "trigger_level": _p("float", 20, "9-3 must turn up through this level to trigger"),
                "side": _p("str", "bullish", "bullish | bearish (the mirror form)")}, "notes": ""},
    {"number": 7, "name": "stoch_embedded", "kind": "detector", "status": "implemented",
     "description": "Stochastic value beyond threshold for >= N consecutive bars. Doubles as a trend-strength filter.",
     "params": {"k": _p("int", 60, ""), "d": _p("int", 10, ""), "side": _p("str", "over", "over | under"),
                "threshold": _p("float", 85, ""), "min_bars": _p("int", 3, "consecutive bars")}, "notes": ""},
    {"number": 8, "name": "atr", "kind": "detector", "status": "implemented",
     "description": "Average true range.", "params": {"period": _p("int", 14, "")}, "notes": ""},
    {"number": 9, "name": "adv", "kind": "detector", "status": "implemented",
     "description": "Average daily volume over a lookback of daily bars.",
     "params": {"lookback_days": _p("int", 20, "")}, "notes": ""},
    {"number": 10, "name": "relative_volume_premarket", "kind": "detector", "status": "implemented",
     "description": "Cumulative pre-market volume (sum of 1-min bars from 04:00 ET) divided by the N-day average of the same window. SAME FEED, same checkpoint baseline (SIP vs SIP since decision #10).",
     "params": {"baseline_days": _p("int", 14, ""), "window_start": _p("str", "04:00", "ET"),
                "feed": _p("str", "sip", "must equal the baseline feed (decision #10: SIP, ~15 min delayed)")},
     "notes": "Plan §3.2: baseline and live cumulative volume MUST come from the same feed; IEX↔SIP calibration logged nightly (Phase 1)."},
    {"number": 11, "name": "gap_pct", "kind": "detector", "status": "implemented",
     "description": "Gap % of current/pre-market price vs prior close.", "params": {}, "notes": ""},
    {"number": 12, "name": "float_shares", "kind": "detector", "status": "implemented",
     "description": "Float in shares. Finviz Elite primary; fallback = FMP shares outstanding (approximate, ≥ float).",
     "params": {"source_order": _p("list", ["finviz", "fmp_shares_outstanding"], "")}, "notes": ""},
    {"number": 13, "name": "opening_range", "kind": "detector", "status": "implemented",
     "description": "High/low of the first N minutes after 09:30 ET.",
     "params": {"minutes": _p("int", 30, "")}, "notes": ""},
    {"number": 14, "name": "nbar_high_low", "kind": "detector", "status": "implemented",
     "description": "Rolling N-bar high and low.", "params": {"n": _p("int", 2, "")}, "notes": ""},
    {"number": 15, "name": "prior_day_levels", "kind": "detector", "status": "implemented",
     "description": "Prior-day high / low / close.", "params": {}, "notes": ""},
    {"number": 16, "name": "premarket_high_low", "kind": "detector", "status": "implemented",
     "description": "Pre-market session high and low (04:00–09:30 ET).", "params": {}, "notes": ""},
    {"number": 17, "name": "breakout_52w_base", "kind": "detector", "status": "implemented",
     "description": "Proximity to 52-week high and multi-month base/consolidation breakout.",
     "params": {"proximity_pct": _p("float", 3.0, "within X% of 52w high"),
                "base_min_days": _p("int", 60, "consolidation length"),
                "base_range_pct": _p("float", 15.0, "max range of the base")}, "notes": ""},
    {"number": 18, "name": "distance_to_level_atr", "kind": "detector", "status": "implemented",
     "description": "Distance from price to nearest major level in ATR units. Large distance = 'no man's land' (reject).",
     "params": {"max_atr": _p("float", 1.5, "reject if nearest level farther than this"),
                "levels": _p("list", ["prior_day", "premarket", "52w", "volume_profile"], "level families used")},
     "notes": "SMB structure filter (plan §8 F-A)."},
    {"number": 19, "name": "double_top_bottom", "kind": "detector", "status": "implemented",
     "description": "Swing-point double top / double bottom.",
     "params": {"tolerance_pct": _p("float", 0.3, ""), "swing_lookback": _p("int", 5, "")}, "notes": ""},
    {"number": 20, "name": "sector_relative_strength", "kind": "detector", "status": "implemented",
     "description": "Sector ETF performance ranking; strongest-in-strong-sector longs, weakest-in-weak shorts.",
     "params": {"lookback_days": _p("int", 5, "")}, "notes": ""},
    {"number": 21, "name": "flagpole_steepness", "kind": "detector", "status": "implemented",
     "description": "Slope of the move off an EMA vs a rolling slope baseline (S5 bull flag).",
     "params": {"ema_period": _p("int", 20, ""), "baseline_bars": _p("int", 50, ""),
                "min_multiple": _p("float", 1.5, "steeper than X× baseline slope"),
                "side": _p("str", "long", "long = pole up off the EMA; short = mirror")}, "notes": ""},
    {"number": 22, "name": "cvd", "kind": "detector", "status": "implemented",
     "description": "Cumulative volume delta. Tier 1 bar-based approximation (default), Tier 2 IEX print classification, Tier 3 SIP. DISPLAY-ONLY in v1.",
     "params": {"tier": _p("int", 1, "1 | 2 | 3")},
     "notes": "Plan §27: never an entry rule until Tier 3 data exists AND backtests prove edge."},
    {"number": 23, "name": "volume_profile", "kind": "detector", "status": "implemented",
     "description": "POC / VAH / VAL from binned volume. Prior-day/multi-day accurate (SIP history); developing intraday approximate (IEX).",
     "params": {"bins": _p("int", 50, ""), "value_area_pct": _p("float", 70.0, ""),
                "scope": _p("str", "prior_day", "prior_day | multi_day | intraday"),
                "feed": _p("str", "sip", "developing intraday profiles on IEX are approximate")},
     "notes": "In-house ~30 lines; no pandas-ta dependency (plan §27)."},
]

EXITS: list[Plugin] = [
    {"number": 1, "name": "bracket_oco", "kind": "exit", "status": "implemented",
     "description": "Fixed OCO: stop-loss + take-profit. Attached at/after 09:30 ET only (brackets are rejected in extended hours).",
     "params": {"stop": _p("str", "plan", "plan | atr_multiple"),
                "target": _p("str", "plan", "plan | r_multiple | atr_next_push"),
                "r_multiple": _p("float", 2.0, ""),
                "atr_multiple": _p("float", 1.0, "used by stop=atr_multiple and target=atr_next_push (S5, 0.5–1.0)")},
     "notes": "Plan §3.4 / §12."},
    {"number": 2, "name": "trailing_pct", "kind": "exit", "status": "implemented",
     "description": "Trailing percentage stop (single order, never a bracket leg). Regular hours only.",
     "params": {"trail_pct": _p("float", 4.5, "")}, "notes": ""},
    {"number": 3, "name": "nbar_trailing_stop", "kind": "exit", "status": "implemented",
     "description": "Initial stop = lowest low of past N bars; ratchets to the new N-bar low on every new N-bar high; never moves down. Owner's T1 spec.",
     "params": {"n": _p("int", 2, "")}, "notes": "prompt-library/tests/T1_2bar-trailing-stop_spec.md"},
    {"number": 4, "name": "pattern_low_stop", "kind": "exit", "status": "implemented",
     "description": "Stop 1–2 ticks under (over, for shorts) the divergence/pattern candle.",
     "params": {"ticks": _p("int", 2, ""), "tick_size": _p("float", 0.01, ""),
                "lookback_bars": _p("int", 3, "fallback window when the entry recorded no pattern candle")}, "notes": ""},
    {"number": 5, "name": "stoch_rotation_partial", "kind": "exit", "status": "implemented",
     "description": "Partial exit when the 9-3 stochastic crosses back above 80 (long) / below 20 (short).",
     "params": {"k": _p("int", 9, ""), "d": _p("int", 3, ""), "level": _p("float", 80, ""),
                "partial_pct": _p("float", 50.0, "")}, "notes": ""},
    {"number": 6, "name": "scale_out_breakeven", "kind": "exit", "status": "implemented",
     "description": "After the first partial, move the stop on the remainder to entry (breakeven).",
     "params": {"offset_ticks": _p("int", 0, ""), "tick_size": _p("float", 0.01, "")}, "notes": ""},
    {"number": 7, "name": "time_exit", "kind": "exit", "status": "implemented",
     "description": "Flat by HH:MM ET; also enforces intraday-only for leveraged ETFs.",
     "params": {"flat_by": _p("str", "15:55", "ET"), "no_entries_after": _p("str", None, "ET, optional")},
     "notes": ""},
    {"number": 8, "name": "synthetic_premarket_stop", "kind": "exit", "status": "implemented",
     "description": "Websocket listener (alpaca-py stream) in the engine; on stop breach fires a closing LIMIT order in < 1s. Required pre-market because real stops do not trigger outside regular hours.",
     "params": {"stop": _p("str", "plan", ""), "limit_offset_pct": _p("float", 0.5, "how far through the stop the closing limit is placed"),
                "active_window": _p("str", "04:00-09:30", "ET")},
     "notes": "Plan §12.3. Best-effort; surfaced in-app as such (plan §19)."},
    {"number": 9, "name": "rotation_complete_exit", "kind": "exit", "status": "implemented",
     "description": "Full exit as soon as the fast stochastic completes its rotation back (momentum-cycle exit, not price-based). S7 'must take it off' rule.",
     "params": {"k": _p("int", 9, ""), "d": _p("int", 3, ""), "complete_level": _p("float", 20, "")}, "notes": ""},
]

ALL_PLUGINS: list[Plugin] = DETECTORS + EXITS
DETECTOR_NAMES: frozenset[str] = frozenset(p["name"] for p in DETECTORS)
EXIT_NAMES: frozenset[str] = frozenset(p["name"] for p in EXITS)


def get(name: str) -> Plugin:
    for p in ALL_PLUGINS:
        if p["name"] == name:
            return p
    raise KeyError(name)


assert len(DETECTORS) == 23, "plan §9 + §27: 23 detectors"
assert len(EXITS) == 9, "plan §9: 9 exit plugins"
assert len(DETECTOR_NAMES) + len(EXIT_NAMES) == len(ALL_PLUGINS), "duplicate plugin names"
