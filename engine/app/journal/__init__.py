"""Phase 6 — the journal and the Guardian Layer-2 ledger (plan §5.5, §11, §14).

`build.py`  fills → completed round trips → `journal` rows (FIFO, one row per closed position)
`ledger.py` every fill → `guardian_ledger`: fees, realized spread cost, realized P&L, tax provision

This is where the system starts learning from itself. Until a trade closes here, the daily-loss-limit
check has nothing to read, template 02 has no lessons to cite, and the weekly review has nothing to
review — which is why the journal is the thing Phase 6 exists to fill.
"""
