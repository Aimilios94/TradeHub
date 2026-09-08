# E3 — Polymarket Agent (source notes + adapted build spec)

**Sources (verbatim PDFs):** [`../_originals/pdfs/hermes-polymarket-full.pdf`](../_originals/pdfs/hermes-polymarket-full.pdf) (copy-trading bot build prompt), [`../_originals/pdfs/polymarket-ai-trading-cheat-sheet.pdf`](../_originals/pdfs/polymarket-ai-trading-cheat-sheet.pdf) (5 agent strategies + safety checklist) · **Plan:** §22 E3 · **Build order:** after Fundamentals Lab · **Templates:** [11](../hermes-extensions/11_pm-wallet-trade-scoring.md), [12](../hermes-extensions/12_pm-eod-report.md)

## Ported onto OUR rails — not the PDFs' Next.js/SQLite/Vercel app
n8n workflow(s) + engine module + 7 `pm_*` tables (`pm_wallets`, `pm_observed_trades`, `pm_market_snapshots`, `pm_decisions`, `pm_paper_trades`, `pm_outcome_reviews`, `pm_rules`) + two Hermes templates.

## Hard safety — non-negotiable
Paper trading only · no private keys stored or requested · no transaction signing · no live execution (live Polymarket = USDC on Polygon, a rail we do not run) · if an API fails show the real error, never fake data · demo data labeled.

## Pipeline (from the PDFs, mapped)
leaderboard scan (top wallets, 30-day lookback) → wallet scoring (ROI, consistency, copyability, category edge, liquidity quality, entry timing, resolved-trade count, **one-hit-wonder penalty**) → trade monitor (new trades from tracked wallets) → trade scoring (wallet score, entry-vs-current, movement since entry, spread, liquidity, time to resolution, thesis clarity) → decision `paper_copy | watchlist | skip` → simulated positions $5–20 sized by confidence → hourly PnL snapshots → outcome reviews (1h/6h/24h + resolution) → **benchmark: bot-filtered vs blind leaderboard copy vs watchlist vs skipped** → EOD Telegram report.

## Rule self-updates
Allowed autonomously INSIDE the paper sandbox only; every change versioned with reason/evidence/before/after as `hypotheses`-pattern rows in `pm_rules`. Any future live mode inherits the full human-approval gate + Guardian.

## Five sub-strategies (cheat-sheet PDF) → five `pm_*` strategy configs
smart-money copy · crypto short-window (needs an external BTC/ETH feed) · weather (official sources only: NOAA/NWS/ECMWF-class; social posts are never evidence) · arbitrage/correlated (skeptical by default; edge must survive spread+fees+slippage+resolution-criteria match) · event-driven news (fresh-vs-priced-in).

## Honest edge note
Leaderboard copy-trading is crowded; late entry, spread and illiquidity are the killers — the benchmark decides whether anything survives.
