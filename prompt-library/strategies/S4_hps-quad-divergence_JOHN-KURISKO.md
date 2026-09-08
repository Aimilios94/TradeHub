# S4 — HPS Quad Divergence ("Super Signal")

| | |
|---|---|
| **Creator** | John Kurisko (DayTraderRockStar) |
| **Provenance** | **pdf_canonical** — source of truth: [`../_originals/pdfs/John_Kurisko_s_interview_on_Desire_To_Trade.pdf`](../_originals/pdfs/John_Kurisko_s_interview_on_Desire_To_Trade.pdf); secondary: [`../_originals/11_HPS-setups_JOHN-KURISKO_interview-supplement.md`](../_originals/11_HPS-setups_JOHN-KURISKO_interview-supplement.md) |
| **Style / timeframe** | day · **5-min** (ported up from 1-min futures scalping — we do not scalp 1-min on IEX data) |
| **Instruments** | SPY, QQQ + liquid stocks from the S3 focus list |
| **Automation** | **semi_auto** — engine detects; human visually confirms channel/trend context; taps Deploy |
| **Status** | backtesting |
| **Frameworks** | F-B (trend first; trend determines the exit) |
| **Config** | `engine/app/seed/strategies.py` → `S4` |

## Indicators
Stochastic quad **9-3, 14-3, 40-4, 60-10** (40-4 is canonical per the PDF; "44-4" in the interview text is a transcription error — never seeded), EMA 20/50/200, VWAP, prior-day levels. CVD Tier 1 shown on the card, display-only.

## Entry (long form; short mirrors)
- **Stage 1:** ALL four bands < 20 (`stoch_quad_extreme`).
- **Stage 2:** a bounce lifts the 9-3.
- **Stage 3:** price returns to an equal-or-lower low (or double bottom) while the 9-3 holds above its prior low — the divergence (`stoch_divergence`).
- **Trigger:** 9-3 turns up through 20.
- **Optional confluence (config flags):** 5-min/60-min stoch turning up off oversold; reversal candle capping the low; alignment with a lower channel / trendline.
- **Human confirm (not code):** trendline/channel alignment, reversal candle, prevailing trend.

## Exit logic
1. `pattern_low_stop` — 1–2 ticks under the pattern-low candle.
2. `stoch_rotation_partial` — partial at the first 9-3 rotation back above 80.
3. `scale_out_breakeven` on the remainder.
4. `trailing_pct` — **trend determines the style (F-B):** confirmed uptrend → wider trail, time exit via the 60-10 rotation; downtrend → take profits fast.
5. `time_exit` 15:55.

## Risk
risk_per_trade 0.5% · max_concurrent 2 · regular session · limit / stop-limit.

## Marketing-claims disclaimer
The PDF claims ~95% win rate when all conditions align. Recorded here, **never displayed as fact**; the backtest produces the real number and overrides (plan §19).
