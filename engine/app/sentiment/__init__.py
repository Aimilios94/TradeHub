"""Phase 3 — catalyst & sentiment.

Raw news for the morning's top candidates → local Hermes 3 8B condenser (prompt template 01) →
`news_sentiment` rows → a `catalyst` block written back onto today's `scans` rows.

The write-back never changes `scans.passed` (decision 21): it fills in the catalyst and clears the
`news_catalyst` pending gate, leaving the remaining hard gate — the Hermes one-sentence in-play test,
prompt template 04, hosted tier — to Phase 4.
"""
