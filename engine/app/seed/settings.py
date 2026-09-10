"""Global app settings rows (`app_settings` table). Plan §11, §12, §20, §29, §33."""

from __future__ import annotations

from app.models import PromotionCriteria

APP_SETTINGS: dict[str, dict] = {
    "kill_switch": {
        "value": False,
        "description": "Global halt. Every execution workflow checks this first (plan §11/§12). True = no orders of any kind.",
    },
    "auto_approve": {
        "value": False,
        "description": "If true, full_auto signals would execute without the Deploy tap. Ships OFF and stays OFF in v1 (plan §12.1, §18).",
    },
    "paper_mode": {
        "value": True,
        "description": "Paper (default) vs live. Live requires the explicit onboarding switch and separate credentials (plan §16, §33).",
    },
    "promotion_criteria": {
        "value": PromotionCriteria().model_dump(),
        "description": "Gate for draft→backtesting→paper→live and for hypothesis promotion (plan §20). Win rate is NEVER a gate.",
    },
    "guardian_defaults": {
        "value": {
            "spread_ceiling_pct": 2.0,
            "buying_power_buffer_pct": 10.0,
            "daily_loss_limit_r": 2.0,
            "max_concurrent_positions": 3,
            "risk_per_trade_pct_default": 0.5,
            "require_etb_for_shorts": True,
            "wash_trade_guard": True,
            "economic_event_blackout_minutes": 30,
            "tax_provision_rate": 0.28,
            "tax_note": "PT flat CGT estimate, display only — confirm with an accountant",
        },
        "description": "Guardian Layer-1 gate defaults and Layer-2 ledger parameters (plan §11, §29). Per-strategy risk blocks may tighten, never loosen.",
    },
    "scanner_schedule": {
        "value": {"tz": "America/New_York", "morning_scan": "08:00", "midday_rescan": "11:30", "eod_journal": "16:30",
                  "premarket_baseline_nightly": "23:00", "weekly_autopsy": "SUN 09:00", "rules_freshness_monthly": "1st 10:00"},
        "description": "Cron reference (ET). n8n crons must match these values (plan §4 timeline).",
    },
    "data_feed": {
        "value": {
            "live": "iex",
            "historical": "sip",
            "premarket_volume": "sip",
            "premarket_checkpoints": ["05:00", "06:00", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30"],
            "baseline_days": 14,
            "calibration_logging": True,
        },
        "description": (
            "Plan §3.2 / §13. Quotes/charts = IEX (real-time). Pre-market relative volume = SIP (~15 min delayed on the "
            "free plan) because IEX pre-market bars are near-empty for small caps (verified 2026-09-10, docs/DECISIONS.md #10). "
            "Baseline and live cumulative volume ALWAYS use the same feed and the same checkpoint. "
            "IEX↔SIP calibration is logged nightly for both windows."
        ),
    },
    "baseline_universe": {
        "value": ["SPY", "QQQ", "IWM", "PLUG", "SOUN", "SNDL", "BNGO", "GNS", "MARA", "RIOT", "NIO", "LCID", "RIVN",
                  "PLTR", "SOFI", "AMC", "GME", "BBAI", "IONQ", "RGTI", "QUBT", "SMCI", "INTC", "F"],
        "description": (
            "Extra symbols the nightly pre-market baseline job maintains besides watchlist_default and every symbol seen in "
            "scans over the last 30 days (Phase 2 grows this automatically). User-editable."
        ),
    },
    "watchlist_default": {
        "value": ["TSLA", "NVDA", "AMD", "META", "AAPL", "GOOGL", "MSFT", "AMZN"],
        "description": "Default S2 watchlist and dashboard watchlist (user-editable).",
    },
    "failure_policy": {
        "value": {
            "broker_unreachable": "block entries, telegram alert, red banner, synthetic monitor fallback, escalate",
            "data_stale_seconds": 60,
            "stale_data_action": "mark stale, refuse to generate or deploy signals",
            "pipeline_incomplete": "notice instead of partial signals; log compliance_event",
            "llm_failure": "show real error and stop; never fabricate",
        },
        "description": "Fail safe, never fake (plan §33).",
    },
}
