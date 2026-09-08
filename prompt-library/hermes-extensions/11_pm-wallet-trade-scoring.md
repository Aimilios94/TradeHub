---
number: 11
slug: pm_wallet_trade_scoring_rationale
name: Polymarket wallet / trade scoring rationale (E3)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in extension phase E3 — PAPER ONLY
---

# 11 — Polymarket wallet / trade scoring rationale (E3)

## Purpose
Plan §22 E3. Scores are computed in code (wallet: ROI, consistency, copyability, category edge, liquidity quality, entry timing, resolved-trade count, **one-hit-wonder penalty**; trade: wallet score, entry-vs-current, movement since entry, spread, liquidity, time to resolution). Hermes supplies the **thesis-clarity** judgment and a plain-language rationale for the decision label `paper_copy | watchlist | skip` → `pm_decisions`.

## Hard safety (non-negotiable, inherited from the source PDFs)
Paper only · no private keys · no signing · no live execution · real errors shown, never fake data · demo data labeled.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["thesis_clarity", "rationale", "flags"],
  "properties": {
    "thesis_clarity": {"type": "string", "enum": ["clear", "plausible", "unclear"]},
    "rationale": {"type": "string"},
    "flags": {"type": "array", "items": {"type": "string", "enum": ["late_entry", "illiquid", "wide_spread", "one_hit_wonder", "uncopyable_timing", "resolution_ambiguity", "none"]}}
  }
}
```
Sources: `../_originals/pdfs/hermes-polymarket-full.pdf`, `../_originals/pdfs/polymarket-ai-trading-cheat-sheet.pdf`; distilled notes in [`../extension-modules/E3_polymarket_source-notes.md`](../extension-modules/E3_polymarket_source-notes.md).
