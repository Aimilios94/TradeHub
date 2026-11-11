# Data sources

Each source: status, limits, fallback and the date it was last verified. The same facts are seeded into the `sources` table from `db/seeds/sources.json`; change both together. Re-check every free tier quarterly.

**Status menu:** working (one successful call on the date shown), pending (waiting on something outside the project), untested (not yet called), dropped.

## Summary

| Source | Role | Status | Key | Last verified |
|---|---|---|---|---|
| Yahoo Finance (yfinance) | Main: daily prices, futures/VIX/DXY strip | untested | none | – |
| Yahoo RSS | Backup: company news | untested | none | – |
| Finnhub | Main: quotes, company news. Backup: earnings | working | `FINNHUB_API_KEY` | 2026-11-11 |
| FMP | Main: earnings calendar. Fundamentals from v1.0.8 | working | `FMP_API_KEY` | 2026-11-11 |
| Tiingo | Backup: daily prices; backfill cross-check | working | `TIINGO_API_KEY` | 2026-11-11 |
| Twelve Data | Strip backup, limited (see below) | working | `TWELVEDATA_API_KEY` | 2026-11-11 |
| Stooq | Strip backup once keyed | pending | `STOOQ_API_KEY` | 2026-11-11 |
| FRED / ALFRED | Main: macro, rates, Fed panel | working | `FRED_API_KEY` | 2026-11-11 |
| BLS | Backup: CPI/NFP release dates | working | `BLS_API_KEY` | 2026-11-11 |
| StockTwits | Additive: bull/bear ratio | working | none | 2026-11-11 |
| SearXNG (self-hosted) | Deep search | untested as a source | none | – |
| CFTC COT (Socrata) | Positioning history | untested | none | – |

## Fallback order

| Need | Order |
|---|---|
| Daily prices (stocks, ETFs) | Yahoo, then Tiingo |
| Strip (futures, VIX, DXY, US10Y) | Yahoo, then Twelve Data (spot gold only on the free plan), then Stooq (pending key). FRED `VIXCLS` and `DGS10` can stand in for VIX and US10Y with a one-day delay |
| Quotes and company news | Finnhub, then Yahoo RSS |
| Earnings calendar | FMP, then Finnhub, then yfinance earnings dates |
| Social sentiment | News-only by default; StockTwits is added when it has enough labeled messages |

If every source for a need fails, the UI shows the last value with a stale label and the real error. Nothing is filled in.

## Notes per source

### Finnhub
- Free plan: 60 calls/min, 1 year of company news.
- Verified: `/quote`, `/company-news`, `/calendar/earnings` (the earnings calendar does return data on the free key).

### FMP
- Free plan: 250 calls/day, `/stable/` endpoints only (v3 is deprecated).
- Verified on the free key: `quote`, `earnings-calendar`, `profile`, `income-statement`, `balance-sheet-statement`, `cash-flow-statement`, `ratios`, `key-metrics`.

### Tiingo
- Free plan: 50 requests/hour, 1,000/day, about 500 symbols/month, personal use.
- Verified: `/tiingo/daily/SPY/prices`.

### Twelve Data
- Basic plan: 8 calls/min, 800/day.
- Probe of strip symbols on the free plan: `XAU/USD` works. `SPX` requires the Pro plan and `GC` the Grow plan. `VIX`, `DXY` and `NQ` were rejected as invalid symbols. `ES` and `CL` resolve to stocks (Eversource, Colgate-Palmolive), so they must never be requested as futures.
- Conclusion: it does not back up indices or futures on the free plan.

### Stooq
- The CSV endpoint `https://stooq.com/q/d/l/?s=SYMBOL&i=d` works in a browser session. A script receives an HTML page: programmatic access needs an API key (policy since April 2026).
- A key was requested by email on 2026-11-11. When it arrives it goes into `STOOQ_API_KEY`; the adapter is written and contract-tested then.
- Format when keyed: `Date,Open,High,Low,Close,Volume`, daily bars. Display-only, never backtested.

### FRED / ALFRED
- Free. Verified with `DTWEXBGS` (broad dollar index; ICE DXY is not on FRED).
- Backtests use first-published values from ALFRED vintages to avoid look-ahead.

### BLS
- Free v2 key. Verified with CPI series `CUUR0000SA0`.

### StockTwits
- Unofficial public endpoint `https://api.stocktwits.com/api/2/streams/symbol/{TICKER}.json`. No key, no account.
- Use: one cached fetch per watchlist ticker per day; count Bullish and Bearish among labeled messages.
- Each call returns only recent messages, so the sample is small. Below a minimum number of labeled messages, or when the endpoint fails, that ticker's score is news-only.

### Yahoo
- Unofficial endpoints that can change without notice; contract tests will catch shape changes.
- Futures (`=F`) series are for display only and are never used in backtests.

### SearXNG
- Runs in Docker Compose with JSON output enabled. The container answers queries; on 2026-11-11 the DuckDuckGo engine returned a CAPTCHA while other engines returned results.

## Not used

StockTitan (no public API), Brave Search (no free tier since Feb 2026), Alpha Vantage (25 requests/day), Massive (overlaps Tiingo), US Treasury API (FRED covers it), Finviz, X/Twitter, CryptoPanic, IEX Cloud. Polymarket is blocked in Portugal and is not used in v1.0. Reddit is optional and nothing depends on it.
