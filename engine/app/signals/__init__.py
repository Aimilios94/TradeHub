"""Phase 4 — the Hermes decision node.

Turns the morning's scan rows into judgment:

  template 04  one-sentence in-play test  → the F-A "Reason" gate, written to scans.metrics.in_play
  template 02  morning trade plan         → one validated plan per surviving candidate → `signals`
  template 03  watchlist briefing         → the S2 ranked table → `briefings`

Hermes never computes. Every number it is shown was calculated by code, and every price it returns is
re-validated in code against the levels it was given (`plan.validate_plan`). A plan that fails validation
is stored with status 'rejected' and a reason — never silently corrected (template 02 Notes).

Nothing here places an order. Signals are proposals the owner approves by hand; execution is Phase 5.
"""
