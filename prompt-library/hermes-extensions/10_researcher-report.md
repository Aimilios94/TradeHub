---
number: 10
slug: researcher_report
name: Researcher report (E4)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-405b
version: 0
status: built in extension phase E4 (last)
---

# 10 — Researcher report (E4)

## Purpose
Plan §22 E4. Theme/geo/type/horizon → SearXNG (self-hosted) or Brave free tier discovery of 10–20 candidates with sub-segments → FMP + Alpaca quantitative comparison → Python normalizes 0–100 (defaults: Valuation 30 / Growth 25 / Momentum 15 / Sentiment 15 / Macro 15) → Hermes writes the ranked top-5 with entry zones, horizons, rationale, risks, diversification mix → `research_reports`.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["theme", "top5", "diversification_mix", "caveats"],
  "properties": {
    "theme": {"type": "string"},
    "top5": {"type": "array", "maxItems": 5, "items": {"type": "object", "properties": {"symbol": {"type": "string"}, "segment": {"type": "string"}, "score": {"type": "number"}, "entry_zone": {"type": "string"}, "horizon": {"type": "string"}, "rationale": {"type": "array", "maxItems": 3, "items": {"type": "string"}}, "risks": {"type": "array", "items": {"type": "string"}}}}},
    "diversification_mix": {"type": "array", "items": {"type": "object", "properties": {"symbol": {"type": "string"}, "weight_pct": {"type": "number"}}}},
    "caveats": {"type": "array", "items": {"type": "string"}}
  }
}
```
Positioning: a research assistant that saves hours, not an oracle. Original: [`../_originals/08_E4_researcher_OWN.md`](../_originals/08_E4_researcher_OWN.md).
