---
number: 14
slug: long_term_quality
name: Long-term investment analysis (E5)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in extension phase E5 (after E2; shares its pipeline)
---

# 14 — Long-term investment analysis (E5)

## Purpose
Plan §36. Same FMP + Alpaca pipeline as E2, different lens: "is this a business worth owning for 5–10 years." Python computes the five fixed checks; Hermes writes the judgment → `longterm_reports`.

## The five checks (computed, every ticker)
1. Growth consistency — revenue/EPS CAGR over 5/10/15y and lumpiness.
2. Margin & ROIC/ROCE/ROE trend — widening or eroding moat.
3. Debt trajectory — leverage, interest coverage over years.
4. Shareholder returns — dividend history, payout ratio, buybacks vs dilution.
5. Valuation vs its own 10-year history — P/E, EV/EBITDA, FCF yield percentile.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["classification", "quality_score", "check_verdicts", "thesis_breakers", "reasoning"],
  "properties": {
    "classification": {"type": "string", "enum": ["Wonderful", "Fair", "Avoid"]},
    "quality_score": {"type": "number"},
    "check_verdicts": {"type": "array", "items": {"type": "object", "properties": {"check": {"type": "string"}, "verdict": {"type": "string"}, "evidence": {"type": "string"}}}},
    "thesis_breakers": {"type": "array", "items": {"type": "string"}},
    "reasoning": {"type": "string"}
  }
}
```

## Provenance note
The five-check list was authored during design (not owner-supplied). The owner's Richard Coffin process ([`../_originals/12_fundamental-research-process_RICHARD-COFFIN.md`](../_originals/12_fundamental-research-process_RICHARD-COFFIN.md)) may replace this methodology in a later revision — decision deferred by the owner.
