---
number: 9
slug: analyst_conclusion
name: Financial analyst conclusion (E2)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in extension phase E2
---

# 09 — Financial analyst conclusion (E2)

## Purpose
Plan §22 E2. Code computes DCF (explicit growth/WACC/terminal assumptions, bear/base/bull), technicals (50/200 MA, RSI, MACD, Bollinger, OBV), and the weighted composite (defaults: Fundamentals 40 / Technicals 20 / Institutional-Insider 10 / Sector-Macro 15 / Sentiment 15). Hermes writes classification + reasoning + risks → `analyst_reports`. Sanctioned read-point: template 02 may cite a row ≤ 7 days old.

## Corrections vs the original prompt (kept verbatim in `_originals/07`)
No LLM math. The "6/12-month price forecast" is reframed as DCF sensitivity **scenario ranges**, labeled as scenarios, never predictions. Institutional data is patchy on free tiers → `data_quality` flag.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["classification", "composite_score", "reasoning", "scenarios", "risks", "data_quality"],
  "properties": {
    "classification": {"type": "string", "enum": ["Undervalued", "Fairly priced", "Overpriced"]},
    "composite_score": {"type": "number"},
    "reasoning": {"type": "string"},
    "scenarios": {"type": "object", "properties": {"bear": {"type": "string"}, "base": {"type": "string"}, "bull": {"type": "string"}}},
    "risks": {"type": "array", "items": {"type": "string"}},
    "data_quality": {"type": "string", "enum": ["full", "degraded"]}
  }
}
```
Classification thresholds are applied in code (≥70 Undervalued / 40–69 Fair / <40 Overpriced); Hermes explains, it does not decide the bucket.
