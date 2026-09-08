---
number: 15
slug: fundamentals_criteria_to_filters
name: Fundamentals Lab — criteria → screener filters (Mode A)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in extension phase (Fundamentals Lab, after E5)
---

# 15 — Fundamentals Lab Mode A: plain-language criteria → FMP screener filters

## Purpose
Plan §37 Mode A. The user types criteria with no ticker ("revenue growth >15%, D/E <0.5, profitable, market cap >$1B"). Hermes translates the sentence into structured filter parameters (small, cheap call); the **FMP stock-screener endpoint** does the filtering; the ranked list with key numbers comes back in seconds → `fundamentals_screens`. Coffin's Step 1: screen wide before going deep.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["filters", "unmapped", "sort"],
  "properties": {
    "filters": {"type": "object", "additionalProperties": {"type": ["number", "string", "boolean"]}},
    "unmapped": {"type": "array", "items": {"type": "string"}},
    "sort": {"type": "object", "properties": {"field": {"type": "string"}, "direction": {"type": "string", "enum": ["asc", "desc"]}}}
  }
}
```
`filters` keys are restricted in code to the FMP screener's accepted parameters; anything else lands in `unmapped` and is shown to the user rather than silently dropped.
