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
    "sentiment": {
        "value": {
            "sources": ["finnhub", "yahoo"],
            "candidates_per_strategy": 5,
            "lookback_days": 2,
            "max_items_per_source": 20,
            "reddit_subs": ["stocks", "wallstreetbets", "pennystocks", "StockMarket"],
            "catalyst_weight": 2.0,
        },
        "description": (
            "Plan Phase 3 / §10 template 1. Which news sources the catalyst pipeline condenses, and how many "
            "of each strategy's top-ranked passed candidates it enriches (cap 5, deduped across strategies). "
            "Add \"reddit\" to `sources` once REDDIT_CLIENT_ID/SECRET are in .env — the fetcher is already "
            "implemented and reports itself skipped until then. `catalyst_weight` scales the scan-score bonus, "
            "which uses catalyst MAGNITUDE not direction (docs/DECISIONS.md #23)."
        ),
    },
    "signals": {
        "value": {
            "candidates_per_strategy": 5,
            "run_in_play_gate": True,
            "briefing_strategy": "S2",
            "level_tolerance_pct": 3.0,
        },
        "description": (
            "Plan Phase 4 / §10 templates 2-4. How many of each strategy's top-ranked passed candidates get a "
            "Hermes trade plan, whether the cheap one-sentence in-play gate (template 04) runs first, which "
            "strategy produces the watchlist briefing (template 03), and how far a planned price may sit from a "
            "supplied level before code rejects the plan (template 02 Notes: ±3%). Signals are proposals; "
            "execution is Phase 5."
        ),
    },
    "fee_model": {
        "value": {
            "sec_fee_per_dollar": 0.0000278,
            "taf_per_share": 0.000166,
            "taf_max": 8.30,
        },
        "description": (
            "Regulatory fees on SELLS, used by Guardian Layer 2 (plan §11) when the broker reports none. "
            "A PAPER account is never charged SEC/TAF, so the ledger computes an estimate from these "
            "published rates and marks the row `estimated: true` — never presented as an actual charge "
            "(docs/DECISIONS.md #46). The SEC rate is reset annually; update it here, not in code."
        ),
    },
    "rules_file": {
        "value": {
            "version": 1,
            "last_checked": None,
            "hashes": {},
            "flagged_changes": [],
            "doc": "docs/BROKER_RULES.md",
        },
        "description": (
            "Guardian rules-knowledge state (plan §10, §11). `docs/BROKER_RULES.md` states the six broker "
            "rules the engine relies on; the monthly freshness job hashes each source page and raises a "
            "`compliance_events` row when one moves. A changed page means someone should READ it — the job "
            "never decides a rule changed and never edits the doc (docs/DECISIONS.md #55)."
        ),
    },
    "backtest": {
        "value": {
            "fill_model": {
                "spread_model": "band",
                "spread_band_pct": {"2": 1.20, "5": 0.60, "10": 0.30, "50": 0.10, "inf": 0.05},
                "slippage_ticks": 1,
                "tick_size": 0.01,
                "stop_slippage_pct": 0.15,
                "limit_requires_through": True,
                "queue_volume_multiple": 2.0,
                "max_participation_pct": 10.0,
                "commission_per_share": 0.0,
                "apply_regulatory_fees": True,
                "allow_partial_fills": True,
            },
            "default_equity": 25000.0,
            "default_universe_cap": 1500,
            "include_inactive_symbols": True,
            "walk_forward_splits": 2,
        },
        "description": (
            "Plan §13 countermeasure 3 — the fill model, stated rather than assumed. Historical Alpaca "
            "bars carry no quotes, so the spread is ESTIMATED from `spread_band_pct` (a $3 low-float "
            "name is not a $300 mega-cap) and every result reports the parameters it used. "
            "`limit_requires_through` + `queue_volume_multiple` are §31's queue rule: a passive limit "
            "fills only when volume traded THROUGH the level, never on a touch — without it a backtest "
            "fills every losing limit and every winning one, which is the asymmetry that turns a "
            "fake-profitable result negative. `include_inactive_symbols` keeps delisted names in the "
            "replay universe (countermeasure 1): a low-float universe with no delistings in it is not "
            "clean, it is missing the stocks that went to zero."
        ),
    },
    "failure_policy": {
        "value": {
            "broker_unreachable": "block entries, telegram alert, red banner, synthetic monitor fallback, escalate",
            "data_stale_seconds": 60,
            "stale_data_action": "mark stale, refuse to generate or deploy signals",
            "pipeline_incomplete": "notice instead of partial signals; log compliance_event",
            "llm_failure": "show real error and stop; never fabricate",
            "monitor_heartbeat_seconds": 180,
            "alert_cooldown_minutes": 30,
            "block_entries_when_broker_unreachable": True,
            "block_entries_when_data_stale": True,
        },
        "description": (
            "Fail safe, never fake (plan §33) — and since Phase 8 these are the numbers the code actually "
            "reads, not a description of intent. `app/ops/preflight.py` runs every clause as a named check "
            "in front of the Guardian gate: a stale quote or an unreachable broker blocks a NEW entry and "
            "never an exit. `data_stale_seconds` is measured on IEX only, because the free plan delays SIP "
            "~15 minutes by subscription and measuring freshness against it would flag a healthy feed daily. "
            "`monitor_heartbeat_seconds` is how long the synthetic stop watcher may stay silent before "
            "pre-market goes red — it is the only protection a pre-market position has. "
            "`alert_cooldown_minutes` deduplicates the Telegram ping only; the `compliance_events` row is "
            "written every single time, because the count is the evidence."
        ),
    },
    "rate_limits": {
        "value": {
            "alpaca_data": {"per_minute": 190, "burst": 20, "max_attempts": 4, "base_delay_s": 0.5},
            "alpaca_trading": {"per_minute": 190, "burst": 10, "max_attempts": 3, "base_delay_s": 0.5},
            "openrouter": {"per_minute": 30, "burst": 5, "max_attempts": 3, "base_delay_s": 2.0},
            "ollama": {"per_minute": 600, "burst": 30, "max_attempts": 2, "base_delay_s": 1.0},
            "finnhub": {"per_minute": 55, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
            "yahoo": {"per_minute": 60, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
            "reddit": {"per_minute": 55, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
            "fmp": {"per_minute": 55, "burst": 10, "max_attempts": 3, "base_delay_s": 1.0},
            "telegram": {"per_minute": 20, "burst": 5, "max_attempts": 3, "base_delay_s": 1.0},
            "supabase": {"per_minute": 600, "burst": 60, "max_attempts": 3, "base_delay_s": 0.3},
        },
        "description": (
            "Phase 8: how hard the engine is allowed to push each provider, and how often it retries "
            "(`app/ops/resilience.py`). A token bucket spaces our own calls so the free plans' limits are "
            "mostly never reached — Alpaca's is 200/min, Finnhub's 60/min. Retries use exponential backoff "
            "with FULL jitter because every job in this system fires from the same cron minute, and "
            "synchronised retries are how a small outage becomes a 429 storm. Only READS are retried: an "
            "order submission is never auto-retried, since a broker timeout cannot be distinguished from a "
            "silent success and a retry risks a duplicate position."
        ),
    },
    "paper_month": {
        "value": {
            "started_on": None,
            "min_weeks": 4,
            "min_trades": 20,
            "note": "Plan §17 criterion 2. POST /ops/paper-month/start stamps day zero.",
        },
        "description": (
            "Phase 8's calendar gate: four CONSECUTIVE weeks of the full pipeline on paper, at least 20 "
            "journaled trades, autopsies generated and zero unacknowledged execution failures. Restarting "
            "resets the clock on purpose — an outage that breaks the run has to be visible as a restart "
            "rather than averaged away. `/ops/acceptance` measures it; nothing here can grant permission "
            "to trade live, which stays refused in code."
        ),
    },
    "monitor_heartbeat": {
        "value": {"at": None, "watching": 0, "refresh_seconds": 30, "error": None},
        "description": (
            "Written by the synthetic stop container on every watch-list refresh. Before Phase 8 a monitor "
            "that had silently died looked exactly like a monitor with nothing to do — and pre-market it is "
            "the only thing protecting a position, because Alpaca accepts a pre-market stop and never "
            "triggers it (docs/BROKER_RULES.md R1)."
        ),
    },
    "go_live_checklist": {
        "value": {"funded": False, "w8ben": False, "first_strategy": False, "accountant": False},
        "description": (
            "Plan §17 criterion 5 — the steps only the owner can take: fund modestly, file the W-8BEN, "
            "choose ONE small first live config, confirm the tax treatment with an accountant. The engine "
            "never ticks these off, and the go-live scorecard reports them as the owner's permanently."
        ),
    },
    "backup": {
        "value": {
            "tables": ["app_settings", "strategy_configs", "frameworks", "prompt_templates", "plugin_registry",
                       "scans", "signals", "orders", "journal", "news_sentiment", "backtests",
                       "guardian_ledger", "compliance_events", "hypotheses", "economic_events",
                       "premarket_volume_baseline", "feed_calibration", "symbol_stats", "briefings"],
            "keep_daily": 14,
            "keep_weekly": 8,
            "path": "data/backups",
            "include_parquet_manifest": True,
        },
        "description": (
            "Phase 8 backup/restore. `scripts/backup.py` writes one JSONL file per table plus a manifest "
            "with row counts and a checksum; `scripts/restore.py` reads it back and refuses to run without "
            "--yes. The parquet bar cache is NOT copied — it is 18 MB of re-downloadable history — but its "
            "manifest is, so a restore can tell what was cached and re-fetch it. Keep the backup directory "
            "out of git: it contains real account data."
        ),
    },
}
