# App overview

What each section of TradeHub does. Seven sections; the MVP (v1.0) builds five, for Equities only.

| # | Section | What it shows | Version |
|---|---|---|---|
| 1 | **Dashboard** | Futures and macro strip (ES, NQ, YM, RTY, VIX, DXY, US10Y, GC, CL, SI), Fed and macro events with countdowns, ranked hot news with a "why it matters" line for the top 5, event calendar including earnings, market mood, watchlist | v1.0 |
| 2 | **Market Pulse** | Per-source sentiment and a sector heatmap computed from the 11 sector ETFs. Tabs: Equities, then Crypto and Commodities | v1.0 Equities |
| 3 | **Ticker Page** | For any symbol: news, sentiment, driver tags, levels, upcoming earnings, backtest history | v1.0 |
| 4 | **Strategy Lab** | Strategy configs, backtest results and equity curves per strategy | v1.0 backtests |
| 5 | **Deep Analysis** | Fundamentals per ticker: scores computed in Python, report written by Hermes | v1.0.8 |
| 6 | **Smart Money** | SEC Form 4 and 13F insider filings | v1.3 |
| 7 | **Settings / Sources** | Source toggles, scrape depth, LLM budget, source health (last success, last error) | v1.0 |

## What exists today (Phases 0, 0.5 and 1)

- A status page that reads the API: service health (database, SearXNG, Ollama) and the source registry with each source's status, last success and last error.
- A recorder, triggered by n8n: news headlines into PostgreSQL, daily and intraday bars and gold COT into Parquet. One frozen snapshot.
- The news pipeline, triggered by n8n after each news run: duplicates folded together, driver tags, an importance score, a condenser line per headline from a cheap model on OpenRouter (validated, or logged as a failure), and a composite sentiment score per ticker per day. It can be read through `/api/news/top` and `/api/scores`; there is no page for it yet.
- The condenser, the tagger and both scores are unmeasured: the evals come in Phase 4, after the 100 hand labels exist. Treat their output as provisional.
- Nothing is backtested yet, and no "why it matters" text exists.

## How the pieces fit

- **News pipeline:** ingest, dedupe, importance score, driver tags, then the Hermes condenser turns each item into validated JSON.
- **Context layer (no LLM):** quotes and strip, sector heatmap, Fed panel numbers, and a composite sentiment score per ticker per day (-100 to +100).
- **Hermes narrator:** explains numbers that code already computed. Text that fails the number check is never shown.
- **Backtester:** runs JSON strategy configs on frozen daily snapshots; the same config and snapshot always give byte-identical results.

## Labels the UI always shows

- **Delayed** on any quote that is not real time.
- **Stale** on anything whose source failed its last run.
- **Hypothesis** on every sensitivity-matrix entry not yet supported by a signal check.
- **Low confidence** on any backtest with fewer than 100 trades.
- **Demo** on any demo data.
