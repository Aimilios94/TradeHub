"""The 8 seed strategies (plan §7) as validated StrategyConfig objects.

Strategies are DATA. Nothing here is logic; every detector/exit name must exist in
app/registry.py. Provenance rules: pdf_canonical beats interview_supplement (S4–S8);
40-4 is canonical, never 44-4; the bull-flag embed threshold is 85 (preferably 90).
"""

from __future__ import annotations

from app.models import (
    AutomationMode,
    Condition,
    Direction,
    EntryConditions,
    ExitStep,
    Provenance,
    Reentry,
    Risk,
    ScreenerUniverse,
    StrategyConfig,
    StrategyStatus,
    Style,
    WatchlistUniverse,
)

QUAD_BANDS = [[9, 3], [14, 3], [40, 4], [60, 10]]
LIQUID_INSTRUMENTS = ["SPY", "QQQ", "from:S3_focus_list"]

# ---------------------------------------------------------------- S1
S1 = StrategyConfig(
    code="S1",
    name="Low-Float Gapper",
    creator="own",
    provenance=Provenance.own,
    style=Style.day,
    timeframe="1min",
    automation_mode=AutomationMode.full_auto,
    status=StrategyStatus.backtesting,
    frameworks=["FA"],
    universe=ScreenerUniverse(
        type="screener",
        source="finviz_elite",
        params={"price_min": 2.0, "price_max": 20.0, "gap_pct_min": 10.0, "float_max_shares": 20_000_000},
    ),
    entry_conditions=EntryConditions(
        direction=Direction.long,
        all=[
            Condition(detector="relative_volume_premarket", params={"baseline_days": 14, "feed": "iex"},
                      op=">=", value=5.0, label="cumulative pre-market volume ≥ 5× the 14-day IEX average"),
            Condition(detector="gap_pct", op=">=", value=10.0),
            Condition(detector="float_shares", op="<", value=20_000_000),
        ],
        soft=[
            Condition(detector="premarket_high_low", label="breakout level for the buy-stop entry"),
        ],
        hermes_gate="one_sentence_in_play_test",
        notes="News catalyst preferred (soft score from news_sentiment). Hermes ranks the top 5 and writes plans: "
        "buy-stop entry above the pre-market high or breakout level, per plan.",
    ),
    exit_logic=[
        ExitStep(plugin="synthetic_premarket_stop", when="premarket", params={"limit_offset_pct": 1.0}),
        ExitStep(plugin="bracket_oco", when="regular", params={"stop": "plan", "target": "plan"},
                 label="attached at 09:30"),
        ExitStep(plugin="trailing_pct", when="regular", params={"trail_pct": 4.5}, label="optional intraday swap"),
        ExitStep(plugin="time_exit", when="regular", params={"flat_by": "15:55"}),
    ],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=2, daily_loss_limit_r=2.0, session="premarket+regular",
              order_types=["limit"], spread_ceiling_pct=2.0,
              notes="Limit orders ONLY — spreads are the killer. Guardian spread ceiling applies hard."),
    reentry=Reentry(allowed=False),
    prompt_template_slug="morning_trade_plan",
    library_file="prompt-library/strategies/S1_low-float-gapper_OWN.md",
    notes="IEX understates true pre-market volume on small caps — the ratio is valid only because the baseline is IEX too. "
    "Backtest must model wide spreads/partial fills; survivorship-bias check mandatory (delisted low-floats).",
)

# ---------------------------------------------------------------- S2
S2 = StrategyConfig(
    code="S2",
    name="Pre-Market Watchlist Briefing",
    creator="own",
    provenance=Provenance.own,
    style=Style.day,
    timeframe="1min premarket + daily levels",
    automation_mode=AutomationMode.full_auto,
    status=StrategyStatus.paper,
    universe=WatchlistUniverse(type="watchlist", symbols=["TSLA", "NVDA", "AMD", "META", "AAPL", "GOOGL", "MSFT", "AMZN"]),
    entry_conditions=EntryConditions(
        direction=Direction.both,
        soft=[
            Condition(detector="gap_pct", label="pre-market % change"),
            Condition(detector="relative_volume_premarket", params={"baseline_days": 14, "feed": "iex"}),
            Condition(detector="prior_day_levels"),
            Condition(detector="premarket_high_low"),
            Condition(detector="volume_profile", params={"scope": "prior_day"}),
        ],
        notes="Decision support only: no order automation. Pipeline auto-assembles price/%/volume, overnight catalysts "
        "and key levels; Hermes returns catalyst driver, price-action assessment, levels, likely setups "
        "(ORB, VWAP bounce, momentum continuation), priority High/Med/Low, ranked table + market context.",
    ),
    exit_logic=[ExitStep(plugin="time_exit", params={"flat_by": "15:55"}, label="n/a — trades are manual")],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=3, session="regular", notes="Manual trades pass the Guardian gate like any order."),
    prompt_template_slug="watchlist_briefing",
    library_file="prompt-library/strategies/S2_premarket-watchlist-briefing_OWN.md",
    notes="Replaces the owner's manual copy-paste prompt (original 03); everything it asked the user to paste is auto-filled.",
)

# ---------------------------------------------------------------- S3
S3 = StrategyConfig(
    code="S3",
    name="SMB Stocks-in-Play",
    creator="SMB Capital",
    provenance=Provenance.derived,
    style=Style.day,
    timeframe="daily context + 5min execution",
    automation_mode=AutomationMode.full_auto,
    status=StrategyStatus.backtesting,
    frameworks=["FA"],
    universe=ScreenerUniverse(
        type="screener",
        source="finviz_elite",
        params={"gap_abs_pct_min": 3.0, "adv_min_shares": 2_000_000, "atr_min_usd": 1.0},
    ),
    entry_conditions=EntryConditions(
        direction=Direction.both,
        all=[
            Condition(detector="gap_pct", field="abs", op=">=", value=3.0),
            Condition(detector="adv", params={"lookback_days": 20}, op=">", value=2_000_000),
            Condition(detector="atr", params={"period": 14}, op=">", value=1.0),
            Condition(detector="relative_volume_premarket", field="pct_of_adv", op=">=", value=10.0,
                      label="participation: pre-market volume ≥ 10% of ADV (tiers 20/50/100 escalate score)"),
            Condition(detector="distance_to_level_atr", params={"max_atr": 1.5}, op="<=", value=1.5,
                      label="structure: reject mid-range 'no man's land'"),
        ],
        soft=[
            Condition(detector="breakout_52w_base", label="52-week high / multi-month base breakout"),
            Condition(detector="sector_relative_strength", label="strongest-in-strong / weakest-in-weak"),
            Condition(detector="volume_profile", params={"scope": "multi_day"}, label="sharper battlefield lines"),
        ],
        hermes_gate="one_sentence_in_play_test",
        notes="Reason: fresh catalyst from news_sentiment, earnings prioritized; hard gate = Hermes one-sentence test. "
        "Rank and cap the focus list at 3. Midday re-scan 11:30 ET flags names exceeding full-day ADV/ATR.",
    ),
    exit_logic=[
        ExitStep(plugin="bracket_oco", when="regular", params={"stop": "plan", "target": "plan"},
                 label="per-setup ORB / level-based bracket, OCO after the open"),
        ExitStep(plugin="scale_out_breakeven", when="after_partial"),
        ExitStep(plugin="time_exit", when="regular", params={"flat_by": "15:55"}),
    ],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=3, daily_loss_limit_r=2.0, session="regular", order_types=["limit"]),
    prompt_template_slug="morning_trade_plan",
    library_file="prompt-library/strategies/S3_stocks-in-play_SMB-CAPITAL.md",
    notes="Liquid names — best-quality data on the free IEX feed. Recommended first live candidate with S8.",
)

# ---------------------------------------------------------------- S4
S4 = StrategyConfig(
    code="S4",
    name="HPS Quad Divergence ('Super Signal')",
    creator="John Kurisko (DayTraderRockStar)",
    provenance=Provenance.pdf_canonical,
    style=Style.day,
    timeframe="5min",
    automation_mode=AutomationMode.semi_auto,
    status=StrategyStatus.backtesting,
    instruments=LIQUID_INSTRUMENTS,
    frameworks=["FB"],
    universe=WatchlistUniverse(type="watchlist", symbols=["SPY", "QQQ"]),
    entry_conditions=EntryConditions(
        direction=Direction.both,
        stages=[
            {"stage": 1, "rule": "ALL four bands < 20", "detector": "stoch_quad_extreme",
             "params": {"bands": QUAD_BANDS, "side": "under", "threshold": 20}},
            {"stage": 2, "rule": "bounce lifts the 9-3", "detector": "stochastic", "params": {"k": 9, "d": 3}},
            {"stage": 3, "rule": "price returns to an equal-or-lower low (or double bottom) while 9-3 holds above its prior low",
             "detector": "stoch_divergence", "params": {"k": 9, "d": 3, "swing_lookback": 5}},
            {"stage": "trigger", "rule": "9-3 turns up through 20", "detector": "stoch_divergence",
             "params": {"trigger_level": 20}},
        ],
        all=[
            Condition(detector="stoch_quad_extreme", params={"bands": QUAD_BANDS, "side": "under", "threshold": 20}),
            Condition(detector="stoch_divergence", params={"k": 9, "d": 3, "swing_lookback": 5, "trigger_level": 20}),
        ],
        soft=[
            Condition(detector="stochastic", params={"k": 9, "d": 3}, label="5-min/60-min stoch turning up off oversold (confluence flag)"),
            Condition(detector="double_top_bottom", label="double bottom capping the low (confluence flag)"),
            Condition(detector="ema", params={"period": 200}, label="trend context for Framework B"),
            Condition(detector="vwap"),
            Condition(detector="prior_day_levels"),
            Condition(detector="cvd", params={"tier": 1}, label="display only"),
        ],
        human_confirm=["lower channel / trendline alignment", "reversal candle capping the low", "prevailing trend (Framework B)"],
        notes="Short mirrors the long form (quad > 80, lower-high divergence, 9-3 turns down through 80). "
        "PDF claims ~95% win rate — marketing; the backtest produces the real number.",
    ),
    exit_logic=[
        ExitStep(plugin="pattern_low_stop", params={"ticks": 2}),
        ExitStep(plugin="stoch_rotation_partial", params={"k": 9, "d": 3, "level": 80, "partial_pct": 50}),
        ExitStep(plugin="scale_out_breakeven", when="after_partial"),
        ExitStep(plugin="trailing_pct", when="after_partial", params={"trail_pct": 1.5},
                 label="Framework B: uptrend → wider trail; time exit via 60-10 rotation"),
        ExitStep(plugin="time_exit", params={"flat_by": "15:55"}),
    ],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=2, session="regular", order_types=["limit", "stop_limit"]),
    library_file="prompt-library/strategies/S4_hps-quad-divergence_JOHN-KURISKO.md",
    notes="Ported up from 1-min futures scalping to 5-min on ETFs/liquid stocks — our stack does not scalp 1-min on IEX data.",
)

# ---------------------------------------------------------------- S5
S5 = StrategyConfig(
    code="S5",
    name="HPS 20/20 Bull Flag",
    creator="John Kurisko (DayTraderRockStar)",
    provenance=Provenance.pdf_canonical,
    style=Style.day,
    timeframe="5min",
    automation_mode=AutomationMode.semi_auto,
    status=StrategyStatus.backtesting,
    instruments=LIQUID_INSTRUMENTS,
    frameworks=["FB"],
    universe=WatchlistUniverse(type="watchlist", symbols=["SPY", "QQQ"]),
    entry_conditions=EntryConditions(
        direction=Direction.long,
        all=[
            Condition(detector="ema", params={"period": 50}, field="price_above", op="true", label="market uptrend: price above 50 EMA"),
            Condition(detector="flagpole_steepness", params={"ema_period": 20, "baseline_bars": 50, "min_multiple": 1.5},
                      op="true", label="aggressive flagpole off the 20 EMA"),
            Condition(detector="stoch_embedded", params={"k": 60, "d": 10, "side": "over", "threshold": 85, "min_bars": 3},
                      op="true", label="slow 60-10 embedded above 85 (preferably 90) — PDF canonical"),
            Condition(detector="stoch_embedded", params={"k": 14, "d": 3, "side": "over", "threshold": 80, "min_bars": 2},
                      op="true", label="medium holding above 80"),
            Condition(detector="stochastic", params={"k": 9, "d": 3}, field="k", op="<=", value=20,
                      label="9-3 rotates down to 20 — buy into weakness"),
        ],
        soft=[
            Condition(detector="ema", params={"period": 20}, label="pullback to the 20 EMA / 50 EMA zone"),
            Condition(detector="stoch_embedded", params={"k": 60, "d": 10, "side": "over", "threshold": 90, "min_bars": 3},
                      label="preferred: embedded above 90"),
        ],
        human_confirm=["flag shape / pullback quality", "prevailing trend (Framework B)"],
        notes="Buy into weakness at the 20 EMA touch or when the 9-3 = 20. Interview's 'above 80' threshold is superseded by the PDF's 85/90.",
    ),
    exit_logic=[
        ExitStep(plugin="pattern_low_stop", params={"ticks": 2}, label="under the pullback low"),
        ExitStep(plugin="stoch_rotation_partial", params={"k": 9, "d": 3, "level": 80, "partial_pct": 50},
                 label="partial as the 9-3 crosses back above 80; begin on the first candle out of the flag"),
        ExitStep(plugin="scale_out_breakeven", when="after_partial"),
        ExitStep(plugin="bracket_oco", when="after_partial", params={"target": "atr_next_push", "atr_multiple": 0.75},
                 label="OPTIONAL non-canonical target 0.5–1.0× ATR (interview_supplement) — backtester parameter"),
        ExitStep(plugin="time_exit", params={"flat_by": "15:55"}),
    ],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=2, session="regular", order_types=["limit"]),
    library_file="prompt-library/strategies/S5_hps-2020-bull-flag_JOHN-KURISKO.md",
    notes="The ATR 'next push' target is flagged provenance=interview_supplement inside the exit step; calibrate in Phase 7.",
)

# ---------------------------------------------------------------- S6
S6 = StrategyConfig(
    code="S6",
    name="HPS Bear Flag (Canonical)",
    creator="John Kurisko (DayTraderRockStar)",
    provenance=Provenance.pdf_canonical,
    style=Style.day,
    timeframe="5min",
    automation_mode=AutomationMode.semi_auto,
    status=StrategyStatus.backtesting,
    instruments=["SPY", "QQQ", "SQQQ (inverse alt, intraday-only)", "from:S3_focus_list"],
    frameworks=["FB"],
    universe=WatchlistUniverse(type="watchlist", symbols=["SPY", "QQQ"]),
    entry_conditions=EntryConditions(
        direction=Direction.short,
        all=[
            Condition(detector="stoch_embedded", params={"k": 60, "d": 10, "side": "under", "threshold": 20, "min_bars": 3},
                      op="true", label="slow holding under 20"),
            Condition(detector="stoch_embedded", params={"k": 14, "d": 3, "side": "under", "threshold": 20, "min_bars": 2},
                      op="true", label="medium holding under 20"),
            Condition(detector="ema", params={"period": 20}, field="price_below_or_at", op="true", label="price at/below the 20 EMA"),
            Condition(detector="stochastic", params={"k": 9, "d": 3}, field="k", op=">=", value=80,
                      label="short when the 9-3 rotates UP to the 80 line"),
        ],
        human_confirm=["flag shape", "prevailing downtrend (Framework B)"],
        notes="PDF slide's 'go long' at the 80 touch is an obvious typo in a short setup — treated as short. Single entry per setup.",
    ),
    exit_logic=[
        ExitStep(plugin="pattern_low_stop", params={"ticks": 2}, label="above the flag high (mirror)"),
        ExitStep(plugin="stoch_rotation_partial", params={"k": 9, "d": 3, "level": 20, "partial_pct": 50},
                 label="partial as the 9-3 crosses back below 20"),
        ExitStep(plugin="scale_out_breakeven", when="after_partial"),
        ExitStep(plugin="time_exit", params={"flat_by": "15:55"}, label="also enforces intraday-only if SQQQ is used"),
    ],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=2, session="regular", order_types=["limit"],
              notes="Shorting requires margin account ≥ $2,000 + ETB; Guardian ETB check for individual stocks."),
    reentry=Reentry(allowed=False),
    library_file="prompt-library/strategies/S6_hps-bear-flag-canonical_JOHN-KURISKO.md",
    notes="A/B against S7 in Phase 7; promote whichever the backtest prefers.",
)

# ---------------------------------------------------------------- S7
S7 = StrategyConfig(
    code="S7",
    name="HPS Bear Flag (Variant B — interview supplement)",
    creator="John Kurisko (DayTraderRockStar)",
    provenance=Provenance.interview_supplement,
    style=Style.day,
    timeframe="5min",
    automation_mode=AutomationMode.semi_auto,
    status=StrategyStatus.backtesting,
    instruments=["SPY", "QQQ", "SQQQ (inverse alt, intraday-only)", "from:S3_focus_list"],
    frameworks=["FB"],
    universe=WatchlistUniverse(type="watchlist", symbols=["SPY", "QQQ"]),
    entry_conditions=EntryConditions(
        direction=Direction.short,
        all=[
            Condition(detector="ema", params={"period": 200}, field="price_below", op="true", label="trending DOWN under the 200 EMA"),
            Condition(detector="stoch_embedded", params={"k": 60, "d": 10, "side": "under", "threshold": 30, "min_bars": 3},
                      op="true", label="60-10 embedded below 20–30 ('banging around the bottom')"),
            Condition(detector="stochastic", params={"k": 9, "d": 3}, field="k", op=">=", value=80,
                      label="short EVERY 9-3 rotation up above 80"),
        ],
        human_confirm=["prevailing downtrend (Framework B)"],
        notes="Re-entry enabled: every rotation is a fresh short while the regime holds.",
    ),
    exit_logic=[
        ExitStep(plugin="rotation_complete_exit", params={"k": 9, "d": 3, "complete_level": 20},
                 label="cover as soon as the 9-3 completes its rotation back down — 'must take it off'"),
        ExitStep(plugin="pattern_low_stop", params={"ticks": 2}, label="protective stop above the rotation high"),
        ExitStep(plugin="time_exit", params={"flat_by": "15:55"}),
    ],
    risk=Risk(risk_per_trade_pct=0.5, max_concurrent=2, session="regular", order_types=["limit"],
              notes="Same shorting requirements as S6."),
    reentry=Reentry(allowed=True, max_entries=3, cooldown_bars=2),
    library_file="prompt-library/strategies/S7_hps-bear-flag-variantB_JOHN-KURISKO-interview.md",
    notes="Kept as Variant B for A/B testing against S6; PDF wins on every other conflict.",
)

# ---------------------------------------------------------------- S8
S8 = StrategyConfig(
    code="S8",
    name="HPS Swing Divergence",
    creator="John Kurisko (DayTraderRockStar)",
    provenance=Provenance.pdf_canonical,
    style=Style.swing,
    timeframe="60min + daily",
    automation_mode=AutomationMode.semi_auto,
    status=StrategyStatus.backtesting,
    instruments=["SPY", "QQQ", "large caps"],
    frameworks=["FB"],
    universe=WatchlistUniverse(type="watchlist", symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]),
    entry_conditions=EntryConditions(
        direction=Direction.both,
        all=[
            Condition(detector="stoch_quad_extreme", params={"bands": QUAD_BANDS, "side": "under", "threshold": 20}),
            Condition(detector="stoch_divergence", params={"k": 9, "d": 3, "swing_lookback": 5, "trigger_level": 20}),
        ],
        soft=[
            Condition(detector="stochastic", params={"k": 60, "d": 10}, label="daily oversold alignment"),
            Condition(detector="double_top_bottom"),
            Condition(detector="ema", params={"period": 200}),
        ],
        human_confirm=["falling-wedge / lower-channel context", "prevailing trend (Framework B)"],
        notes="Same Lane/quad divergence logic as S4 on 60-min and daily bars. PDF shows it on 60-min SP-500 and daily Verizon charts.",
    ),
    exit_logic=[
        ExitStep(plugin="pattern_low_stop", params={"ticks": 2}),
        ExitStep(plugin="stoch_rotation_partial", params={"k": 9, "d": 3, "level": 80, "partial_pct": 50}, label="scale out on rotation"),
        ExitStep(plugin="scale_out_breakeven", when="after_partial"),
        ExitStep(plugin="trailing_pct", when="after_partial", params={"trail_pct": 4.0}, label="wider trail; holds overnight/days"),
    ],
    risk=Risk(risk_per_trade_pct=0.75, max_concurrent=3, daily_loss_limit_r=2.0, session="swing", order_types=["limit", "stop_limit"]),
    library_file="prompt-library/strategies/S8_hps-swing-divergence_JOHN-KURISKO.md",
    notes="Easiest strategy for our stack: daily bars are free and perfect quality, latency irrelevant, no pre-market complexity. Good early live candidate.",
)


def all_strategies() -> list[StrategyConfig]:
    return [S1, S2, S3, S4, S5, S6, S7, S8]
