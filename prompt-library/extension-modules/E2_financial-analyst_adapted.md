# E2 — Financial Analyst (adapted build spec)

**Original (verbatim, preserved as the coverage specification):** [`../_originals/07_E2_financial-analyst_OWN.md`](../_originals/07_E2_financial-analyst_OWN.md) · **Plan:** §22 E2 · **Build order:** second · **Template:** [09](../hermes-extensions/09_analyst-conclusion.md) · **Table:** `analyst_reports`

## Split
| Code | Hermes |
|---|---|
| FMP free tier: fundamentals, ratios, growth, margins, insider transactions; Alpaca: price history. DCF with explicit assumptions (growth, WACC, terminal) → bear/base/bull ranges. Technicals: 50/200 MA, RSI, MACD, Bollinger, OBV. Weighted composite (config; defaults Fundamentals 40 / Technicals 20 / Institutional-Insider 10 / Sector-Macro 15 / Sentiment 15). Classification thresholds ≥70 / 40–69 / <40. | classification wording, reasoning, risks |

## Corrections vs the original
- No LLM math anywhere.
- "6/12-month price forecast" → DCF sensitivity **scenario ranges**, labeled as scenarios, never predictions.
- Institutional-holdings data is patchy on free tiers → score runs degraded with a `data_quality` flag (future option: FMP paid ~$15–20/mo).

## Sanctioned read-point
Template 02 (morning plan) may cite an `analyst_reports` row for a candidate if ≤ 7 days old; otherwise ignored.
