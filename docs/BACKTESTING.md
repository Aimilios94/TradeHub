# Backtesting method

How backtests are run and judged. These rules are fixed before the first run so results cannot shape them.

## Promotion thresholds

All figures are **after costs**. These are the owner's starting values (set 2026-11-11), not industry rules; they may be tightened after the first runs, and any change is recorded in `DECISIONS.md`.

| Measure | Threshold |
|---|---|
| Expectancy | Above 0, and the lower bound of the bootstrap 90% range also above 0 |
| Profit factor, tuning period | At least 1.3 |
| Profit factor, validation period | At least 1.2 |
| Trades | At least 30. Under 100 is labeled "low confidence" |
| Hold-out 2024–2026 | Still profitable after costs (expectancy above 0) |

- Win rate is shown and never used to decide.
- A result that only works with zero costs is discarded.
- Every run is kept, including failures.

## Data split

| Period | Years | Use |
|---|---|---|
| Tuning | 2010–2020 | Parameter tuning, walk-forward |
| Validation | 2021–2023 | Check the tuned parameters |
| Hold-out | 2024–2026 | Final check, untouched until the end |

## Fill model

- Market entries fill at the **next bar's open**.
- Limit entries (zone strategies) fill at the limit price when the bar touches it.
- Spread, a slippage buffer and commissions are always applied. The parameters are stored with every result.
- When stop and target fall in the same bar, the **stop counts first**.
- No mid-bar guessing.

## Reproducibility

- A backtest reads a named, frozen Parquet snapshot, never live data. Snapshots are never edited; new data is a new snapshot.
- A snapshot is a folder `data/<version>/` with one Parquet file per table (`bars_daily`, `bars_intraday`, `cot`) and `manifest.json`: sources, date range, row counts, symbols and a SHA-256 per file. The combined hash is also stored in the `datasets` table. `python -m scripts.snapshot verify <version>` re-hashes the files and compares.
- A snapshot holds only rows dated before the day it was frozen (UTC), so a bar still forming cannot be in it.
- Same config and same snapshot must give byte-identical metrics. CI enforces this on the committed sample.
- Each result stores the config hash, dataset version, fill parameters and code version.
- At least one strategy (gap fade) is also run in backtesting.py; results must match within a small tolerance stated in that strategy's RESULTS.md.
- On higher timeframes the engine only sees candles already closed at signal time. A test enforces this.

## Point-in-time rules

- News uses `ingested_at`, not `published_at`.
- Macro series use first-published values (ALFRED vintages).
- Raw OHLC and adjusted close are stored as separate columns; the adjustment method is written in the snapshot manifest. With Yahoo data, open/high/low/close are adjusted for splits but not for dividends; `adj_close` is adjusted for both.
- COT rows carry the report date (a Tuesday). The report is published the following Friday, so a strategy may use a row only from that Friday on.

## Instruments

- MVP backtests use ETFs only: SPY, QQQ, GLD. Every result carries `instrument_type`.
- Futures versions of a strategy are separate configs with their own costs and hours, and are never compared one-to-one with ETF results.
- **No USO.** It is not used as an oil proxy because of contango decay and the 2020 change in how it holds contracts. Oil backtests wait for back-adjusted CL futures in v1.2.
- Yahoo `=F` series are display-only and never used for backtests.

## Futures roll method (for later futures configs)

- Ratio back-adjusted continuous series with a fixed roll rule: roll 5 trading days before expiry by default.
- Or Databento's continuous-contract series when that data is used.
- The method and the roll dates are written in every snapshot manifest.

## Known biases, written next to every result

- **Survivorship:** the universe is today's symbols.
- **Short tag history:** news and driver tags only exist from the day recording started, so tag-based strategies cannot be tested on earlier years.
