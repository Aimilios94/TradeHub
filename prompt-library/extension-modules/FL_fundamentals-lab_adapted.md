# Fundamentals Lab (build spec) — the owner's OWN methodology, not Hermes'

**Plan:** §37 · **Build order:** after E5, before E3 · **Templates:** [15](../hermes-extensions/15_fundamentals-criteria-to-filters.md) (Mode A), [16](../hermes-extensions/16_fundamentals-deep-dive.md) (Mode B) · **Tables:** `fundamentals_screens`, `fundamentals_deepdives`
**Owner-supplied methodology source:** [`../_originals/12_fundamental-research-process_RICHARD-COFFIN.md`](../_originals/12_fundamental-research-process_RICHARD-COFFIN.md) (qualitative steps: screening, understanding the business, skepticism / devil's advocate)

## Why a separate section
E2 and E5 apply fixed methodologies to a ticker you name. Fundamentals Lab runs **your** criteria — with or without a ticker. It is the only section where the analysis logic itself is user-supplied ("the deepest input in the app", APP_OVERVIEW.md).

## Mode A — Screener (build first; cheap; use liberally)
Plain-language criteria, no ticker → Hermes → structured filters → **FMP stock-screener endpoint** (one free call) → ranked candidates with key numbers. Pennies per run.

## Mode B — Deep-dive (build second; costliest action in the app; use selectively)
One ticker (+ optional thesis/checklist) → SEC EDGAR 10-K/10-Q + recent transcripts → business summary, SWOT, sector KPIs vs peers, management track record, devil's-advocate pass ("what would have to be true for this to fail"). On-demand only, never scheduled.

## UI note
Screen liberally, deep-dive selectively — surfaced as guidance in the section header.
