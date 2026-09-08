# S6 — HPS Bear Flag (Canonical)

| | |
|---|---|
| **Creator** | John Kurisko (DayTraderRockStar) |
| **Provenance** | **pdf_canonical** (PDF in `../_originals/pdfs/`) |
| **Style / timeframe** | day · 5-min · **direction SHORT** |
| **Instruments** | SPY / QQQ (ETB, trivial to short with margin ≥ $2,000); alternatively SQQQ intraday-only; liquid stocks from S3 (Guardian ETB check) |
| **Automation** | semi_auto |
| **Status** | backtesting |
| **Frameworks** | F-B |
| **Config** | `engine/app/seed/strategies.py` → `S6` |

## Entry
- Slow (60-10) AND medium (14-3) stochastics holding **under 20** (`stoch_embedded`).
- Price at/below the 20 EMA.
- **Short when the 9-3 rotates UP to the 80 line.**
- Single entry per setup (re-entry not allowed).

> The PDF slide's "go long" at the 80 touch is an obvious typo inside a short setup — treated as short.

## Exit
- Partial profits as the 9-3 crosses back **below 20** (`stoch_rotation_partial`, level 20).
- Stop to breakeven on the remainder (`scale_out_breakeven`).
- Protective stop above the flag high (`pattern_low_stop`, mirrored).
- `time_exit` 15:55 (also enforces intraday-only if SQQQ is used).

## Risk
risk_per_trade 0.5% · max_concurrent 2 · regular · limit orders · shorting requires margin account + easy-to-borrow (Guardian).

## A/B
Backtested head-to-head against **S7 (Variant B)** in Phase 7; promote whichever the backtest prefers (plan §13).
