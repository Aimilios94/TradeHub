"""Phase 6 — the feedback loop (plan §20). "Hermes proposes, the backtester disposes, the database
remembers."

`autopsy.py`  template 05, batched: one review per closed trade → `journal.autopsy`
`weekly.py`   template 06: the week's trades + analytics → lessons + `hypotheses` rows

Nothing here changes a strategy. A hypothesis is a proposal with a config diff and cited evidence; it
has to survive a Phase 7 backtest and then the owner's approval before it touches anything. Rejected
hypotheses are kept forever so the same idea cannot be re-proposed without new evidence.
"""
