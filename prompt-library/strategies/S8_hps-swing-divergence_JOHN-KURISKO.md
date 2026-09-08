# S8 — HPS Swing Divergence

| | |
|---|---|
| **Creator** | John Kurisko (DayTraderRockStar) |
| **Provenance** | **pdf_canonical** — the PDF shows the same divergence on 60-min SP-500 and daily Verizon charts |
| **Style / timeframe** | **swing** · 60-min and daily |
| **Instruments** | SPY / QQQ / large caps (seed watchlist: SPY, QQQ, AAPL, MSFT, NVDA, AMZN, GOOGL, META) |
| **Automation** | semi_auto |
| **Status** | backtesting |
| **Frameworks** | F-B |
| **Config** | `engine/app/seed/strategies.py` → `S8` |

## Entry
Same Lane/quad divergence logic as S4 (`stoch_quad_extreme` under 20 → bounce → equal/lower low with 9-3 higher low → 9-3 up through 20), on **60-min and daily bars**. Strongest when aligned with a falling-wedge / lower-channel context (human confirm) and the daily is oversold (soft: 60-10 on daily).

## Exit
- `pattern_low_stop` under the pattern low.
- Scale out on the stochastic rotation (`stoch_rotation_partial` at 80).
- Breakeven on the remainder; then a **wider** `trailing_pct` (4%) — holds overnight / days.

## Risk
risk_per_trade 0.75% · max_concurrent 3 · session swing · limit / stop-limit.

## Why it matters
The easiest strategy for our stack: daily bars are free and perfect quality, latency is irrelevant, no pre-market complexity. Good early live candidate (plan §7 S8, §17.5).
