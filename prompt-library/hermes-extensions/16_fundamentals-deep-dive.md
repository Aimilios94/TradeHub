---
number: 16
slug: fundamentals_deep_dive
name: Fundamentals Lab — deep-dive with devil's-advocate pass (Mode B)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-405b
version: 0
status: built in extension phase (Fundamentals Lab) — the costliest single action in the app; on-demand only
---

# 16 — Fundamentals Lab Mode B: deep-dive against the user's own thesis

## Purpose
Plan §37 Mode B. Input: one ticker plus, optionally, the user's own thesis/checklist. Pulls SEC EDGAR 10-K/10-Q and recent earnings-call transcripts. Hermes produces: business summary (segments, revenue nature, geography, key players), **SWOT**, sector-specific KPIs vs peers, management execution track record (multi-year ROIC/ROCE vs past promises), and a deliberate **devil's-advocate pass** — arguments against the thesis, red flags on "adjusted" earnings language, related-party/conflict checks, and "what would have to be true for this to fail". → `fundamentals_deepdives`.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["business_summary", "swot", "kpis_vs_peers", "management_track_record", "devils_advocate", "thesis_verdict", "sources"],
  "properties": {
    "business_summary": {"type": "string"},
    "swot": {"type": "object", "properties": {"strengths": {"type": "array", "items": {"type": "string"}}, "weaknesses": {"type": "array", "items": {"type": "string"}}, "opportunities": {"type": "array", "items": {"type": "string"}}, "threats": {"type": "array", "items": {"type": "string"}}}},
    "kpis_vs_peers": {"type": "array", "items": {"type": "object", "properties": {"kpi": {"type": "string"}, "value": {"type": "string"}, "peer_context": {"type": "string"}}}},
    "management_track_record": {"type": "string"},
    "devils_advocate": {"type": "object", "properties": {"against_thesis": {"type": "array", "items": {"type": "string"}}, "adjusted_earnings_flags": {"type": "array", "items": {"type": "string"}}, "related_party_flags": {"type": "array", "items": {"type": "string"}}, "what_must_be_true_to_fail": {"type": "array", "items": {"type": "string"}}}},
    "thesis_verdict": {"type": "string", "enum": ["holds", "partially_holds", "does_not_hold", "no_thesis_supplied"]},
    "sources": {"type": "array", "items": {"type": "string"}}
  }
}
```
Qualitative steps (screening, understanding the business, skepticism) come from the owner's Coffin document (`../_originals/12_…`). Long documents → materially higher token use than any other module (still likely < €1 per report).
