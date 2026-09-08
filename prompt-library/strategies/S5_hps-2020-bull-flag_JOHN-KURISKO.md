# S5 — HPS 20/20 Bull Flag

| | |
|---|---|
| **Creator** | John Kurisko (DayTraderRockStar) |
| **Provenance** | **pdf_canonical** (PDF in `../_originals/pdfs/`); interview supplement in `../_originals/11_…` |
| **Style / timeframe** | day · 5-min |
| **Instruments** | SPY / QQQ / liquid stocks |
| **Automation** | semi_auto |
| **Status** | backtesting |
| **Frameworks** | F-B |
| **Config** | `engine/app/seed/strategies.py` → `S5` |

## Entry
- Market uptrend: price above the 50 EMA.
- Aggressive **flagpole** off the 20 EMA — steeper than the rolling slope baseline (`flagpole_steepness`, min 1.5× baseline).
- Slow **60-10 embedded above 85, preferably 90** (`stoch_embedded`); medium 14-3 holding above 80.
- Pullback to the 20 EMA / 50 EMA zone.
- 9-3 rotates down to 20 → **buy into weakness** at the 20 EMA touch or when the 9-3 = 20.

## Exit
- Partial profits as the 9-3 crosses back above 80 (`stoch_rotation_partial`); begin profit-taking on the first candle out of the flag.
- Stop to breakeven on the remainder (`scale_out_breakeven`).
- Stop under the pullback low (`pattern_low_stop`).
- **Optional, non-canonical target** (interview): ATR-relative "next push" 0.5–1.0× ATR — a backtester parameter, flagged `provenance = interview_supplement` inside the exit step; calibrate in Phase 7.

## Risk
risk_per_trade 0.5% · max_concurrent 2 · regular session · limit orders.

## Provenance conflicts (PDF wins)
- Interview text says the 60-10 must be "above 80 (preferably 90)"; the PDF says **85, preferably 90**. Seeded value: 85 hard, 90 preferred (soft).
- Interview mentions 3–5 point pushes on futures; irrelevant on ETFs — replaced by the optional ATR target.
