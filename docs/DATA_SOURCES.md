# Data sources

Each source: status, limits, fallback and the date it was last verified. The same facts are seeded into the `sources` table from `db/seeds/sources.json`; change both together. Re-check every free tier quarterly.

**Status menu:** working (one successful call on the date shown), pending (waiting on something outside the project), untested (not yet called), dropped.

## Summary

| Source | Role | Status | Key | Last verified |
|---|---|---|---|---|
| Yahoo Finance (yfinance) | Main: daily prices, futures/VIX/DXY strip, recent intraday | working | none | 2026-11-11 |
| Yahoo RSS | Backup: company news (recorded alongside Finnhub) | working | none | 2026-11-11 |
| Finnhub | Main: quotes, company news. Backup: earnings | working | `FINNHUB_API_KEY` | 2026-11-11 |
| FMP | Main: earnings calendar. Fundamentals from v1.0.8 | working | `FMP_API_KEY` | 2026-11-11 |
| Tiingo | Backup: daily prices; backfill cross-check | working | `TIINGO_API_KEY` | 2026-11-11 |
| Twelve Data | Strip backup, limited (see below) | working | `TWELVEDATA_API_KEY` | 2026-11-11 |
| Stooq | Strip backup once keyed | pending | `STOOQ_API_KEY` | 2026-11-11 |
| FRED / ALFRED | Main: macro, rates, Fed panel | working | `FRED_API_KEY` | 2026-11-11 |
| BLS | Backup: CPI/NFP release dates | working | `BLS_API_KEY` | 2026-11-11 |
| StockTwits | Additive: bull/bear ratio | working | none | 2026-11-11 |
| SearXNG (self-hosted) | Deep search | working | none | 2026-11-11 |
| CFTC COT (Socrata) | Positioning history | working | none | 2026-11-11 |

## Fallback order

| Need | Order |
|---|---|
| Daily prices (stocks, ETFs) | Yahoo, then Tiingo |
| Strip (futures, VIX, DXY, US10Y) | Yahoo, then Twelve Data (spot gold only on the free plan), then Stooq (pending key). FRED `VIXCLS` and `DGS10` can stand in for VIX and US10Y with a one-day delay |
| Quotes and company news | Finnhub, then Yahoo RSS |
| Earnings calendar | FMP, then Finnhub, then yfinance earnings dates |
| Social sentiment | News-only by default; StockTwits is added when it has enough labeled messages |

If every source for a need fails, the UI shows the last value with a stale label and the real error. Nothing is filled in.

## Fallback decisions (Phase 0.5)

| Gap found | Decision |
|---|---|
| Stooq needs a key that has not arrived | Stays `pending`. Nothing depends on it; the adapter is written when the key arrives |
| Twelve Data free plan has no indices or futures | Not a strip backup for those. It covers spot gold only |
| Futures on the strip (ES, NQ, YM, RTY, GC, CL, SI) have no working backup | If Yahoo fails they show the last value with a stale label and the error. They are display-only, so no backtest is affected |
| VIX and US10Y backup | FRED `VIXCLS` and `DGS10`, one day late, labeled as delayed |
| DXY backup | None free. FRED `DTWEXBGS` (broad dollar index) is a different index and is shown under its own name, never as DXY |
| Yahoo has no spot gold symbol (`XAUUSD=X` is not found) | Spot gold comes from Twelve Data `XAU/USD` only |
| SearXNG engines that refuse (Google News, Reuters, DuckDuckGo general) | Accepted. Other engines answer; the check reports which ones did not |
| Yahoo RSS holds only the latest 20 items per symbol | Finnhub is the main news source (date-range queries); RSS is recorded as well to widen coverage |

## What the recorder stores

| Data | From | Where | Schedule (UTC) |
|---|---|---|---|
| News headlines, link, short excerpt, the ticker it was fetched for | Finnhub and Yahoo RSS, watchlist tickers | PostgreSQL `news_items` | 07:00, 13:00, 21:30 daily |
| Daily bars, full history | Yahoo, every seeded ticker with a Yahoo symbol (27) | `data/live/bars_daily/` | 22:30 daily |
| Intraday bars: 1m, 5m, 1h, regular session | Yahoo, watchlist tickers (16) | `data/live/bars_intraday/` | 22:30 daily |
| COT, legacy futures-only | CFTC, tickers with a `cftc` market code (gold) | `data/live/cot/` | Friday 22:00 |

Yahoo serves 1-minute bars for the last 7 days only, 5-minute for 60 days and hourly for 730 days. Each run asks for the last 7 days, so a few missed days fill in on the next run; after a longer gap it asks for the longest window allowed. Bars still forming when a run happens are not stored.

`data/live/` is the only place that changes. A backtest never reads it: it reads a frozen snapshot (`data/<version>/`), see [BACKTESTING.md](BACKTESTING.md).

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
- Re-probed 2026-11-11 with 25 calls (symbol search, the commodities list, and quotes): symbol search returns no futures for `ES`, `GC` or `CL`, only stocks and funds. `YM`, `RTY`, `ES1!`, `GC1!`, `CL1!`, `DJI`, `RUT` are invalid symbols. `SI` resolves to a stock (Shoulder Innovations). Spot silver `XAG/USD`, spot oil `WTI/USD` and `XBR/USD`, `NDX` and `TNX` need a paid plan.
- Conclusion: it carries no futures at all, and on the free plan no indices. Its only use for the strip is spot gold `XAU/USD`.

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
- Unofficial endpoints, through the `yfinance` library; they can change without notice. Contract tests catch shape changes.
- Verified: daily history for all 27 seeded symbols. Strip symbols: `ES=F`, `NQ=F`, `YM=F`, `RTY=F`, `GC=F`, `CL=F`, `SI=F`, `^GSPC`, `^VIX`, `DX-Y.NYB`, `^TNX`. Intraday: 1m, 5m, 1h.
- Prices: open, high, low and close are adjusted for splits but not for dividends. `Adj Close` is adjusted for both and is stored as its own column, with dividends and split ratios beside it.
- Futures (`=F`) series are front-month with no roll adjustment. They are for display only and are never used in backtests. Yahoo's futures history holds a few odd rows (crude's negative close on 2020-04-20 is real; one gold row has its high below its low). They are stored as received.
- No spot gold: `XAUUSD=X` is not found.

### Yahoo RSS
- `https://feeds.finance.yahoo.com/rss/2.0/headline?s=SYMBOL&region=US&lang=en-US`. No key.
- Returns the latest 20 items for the symbol: title, link, publication time, short description.

### SearXNG
- Runs in Docker Compose with JSON output enabled.
- Verified 2026-11-11 with a news query: results from Bing News, Brave News, DuckDuckGo News and Wikinews. Google News answered "access denied", Reuters an HTTP error, and the general DuckDuckGo engine a CAPTCHA. No attempt is made to get around these.

### CFTC COT
- Socrata dataset `6dca-aqww` (legacy report, futures only) at `publicreporting.cftc.gov`. No key.
- Gold is contract market code `088691`: 1,941 weekly reports from 1986-01-15.
- A report shows positions as of a Tuesday and is published the following Friday. Backtests must not use a row before its release.
- A ticker gets COT history when its `source_symbols` in `db/seeds/tickers.json` has a `cftc` code.

## Not used

StockTitan (no public API), Brave Search (no free tier since Feb 2026), Alpha Vantage (25 requests/day), Massive (overlaps Tiingo), US Treasury API (FRED covers it), Finviz, X/Twitter, CryptoPanic, IEX Cloud. Polymarket is blocked in Portugal and is not used in v1.0. Reddit is optional and nothing depends on it.
