# S2 — Pre-Market Watchlist Briefing

| | |
|---|---|
| **Creator** | own |
| **Provenance** | own — original prompt: [`../_originals/03_S2_premarket-game-plan_OWN.md`](../_originals/03_S2_premarket-game-plan_OWN.md) |
| **Style / timeframe** | day (decision support) · 1-min pre-market data + daily levels |
| **Automation** | full_auto briefing; trades are manual (Manual order ticket, Guardian-gated) |
| **Status** | paper — ready immediately, no order automation required |
| **Hermes template** | [`../hermes-core/03_watchlist-briefing.md`](../hermes-core/03_watchlist-briefing.md) |
| **Config** | `engine/app/seed/strategies.py` → `S2` |

## Universe (watchlist, user-editable)
`TSLA, NVDA, AMD, META, AAPL, GOOGL, MSFT, AMZN` (default also stored in `app_settings.watchlist_default`).

## Pipeline (what replaces the copy-paste)
For each symbol the engine auto-assembles: pre-market price / % / volume / rel-vol (Alpaca IEX), overnight catalysts (`news_sentiment`), key levels (prior-day H/L/C, pre-market H/L, round numbers, prior-day POC/VAH/VAL). Hermes returns per stock: catalyst driver, price-action assessment, levels to watch, likely setups (ORB, VWAP bounce, momentum continuation…), priority High/Med/Low — as a ranked table + 2–3 sentences of market context. Pushed to Telegram ~08:15 ET and shown on the Dashboard.

## Entry / exit / risk
No automated entries. Manual trades taken from the briefing pass the Guardian gate like any order and are journaled with `source = manual`.

## What changed vs the original
Everything the original asked the user to paste (headlines, pre-market table, levels) is auto-filled. The output format (table sorted by priority + market context) is preserved as the JSON schema of template 03.
