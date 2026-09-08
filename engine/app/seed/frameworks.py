"""Selection frameworks — they GATE strategies, they are not strategies (plan §8)."""

from __future__ import annotations

from app.models import Framework

FA = Framework(
    code="FA",
    name="SMB 'Stocks in Play' Triple Filter",
    creator="SMB Capital",
    description="Reason / Participation / Structure. Applies to all stock day-trade strategies. "
    "One-sentence in-play test enforced by Hermes as a hard gate.",
    rules={
        "reason": {
            "definition": "fresh information forcing revaluation: earnings, guidance, FDA, M&A, lawsuit, multi-month level break",
            "auto_reject": ["moved yesterday", "looks interesting", "no identifiable catalyst"],
            "hermes_gate": "one_sentence_in_play_test",
            "gate_template": "Stock X is in play today because ___",
        },
        "participation": {
            "gap_pct_min": 3.0,
            "adv_min_shares": 2_000_000,
            "atr_min_usd": 1.0,
            "premarket_volume_pct_adv_tiers": {"pass": 10, "good": 20, "strong": 50, "exceptional": 100},
            "min_premarket_prints": 200,
            "reject": "polka-dot charts (thin, gappy prints)",
        },
        "structure": {
            "validate_order": ["daily", "hourly", "15min"],
            "require_clean_level": True,
            "level_families": ["52w_high", "multi_month_base", "major_sr", "prior_day", "volume_profile"],
            "reject_no_mans_land": {"detector": "distance_to_level_atr", "max_atr": 1.5},
        },
        "focus_list_cap": 3,
        "sector_alignment": "strongest-in-strong-sector longs / weakest-in-weak-sector shorts",
        "midday_rescan_et": "11:30",
        "midday_rescan_flags": ["exceeds full-day ADV", "exceeds full-day ATR"],
        "alerts_not_watching": True,
    },
    library_file="prompt-library/frameworks/FA_stocks-in-play-triple-filter_SMB-CAPITAL.md",
)

FB = Framework(
    code="FB",
    name="Kurisko Trend-First Rule",
    creator="John Kurisko (DayTraderRockStar)",
    description="Determine the prevailing trend FIRST; the trend determines the exit strategy. "
    "Only trade the confirmed 'Super Signal' (quad rotation + playbook setup together).",
    rules={
        "step_one": "determine prevailing trend before any HPS trade",
        "uptrend": {
            "definition": "rising channel; price above 200 EMA and VWAP",
            "exit_style": "give room; wider trailing stop; time exits with the 60-10 rotation",
        },
        "downtrend": {
            "definition": "price below 200 EMA; 60-10 weak / under 30",
            "exit_style": "take profits fast as the 9-3 reaches its extreme; move to breakeven immediately",
        },
        "only_confirmed_signal": True,
        "skip_partial_setups": True,
        "active_windows_hint_et": ["09:30-11:00", "03:00-04:30 (European open)"],
        "human_confirm": ["channels", "trendlines", "wedges", "coils"],
    },
    library_file="prompt-library/frameworks/FB_trend-first-rule_JOHN-KURISKO.md",
)


def all_frameworks() -> list[Framework]:
    return [FA, FB]
