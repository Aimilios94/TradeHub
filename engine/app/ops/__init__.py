"""Phase 8 — hardening. The parts that keep the engine honest when something outside it breaks.

Four modules, one rule each:

- `resilience` — a transient failure is retried; a *write* is not. A duplicate order is worse than a
  failed one, so `submit_order` is never wrapped in a retry and this module refuses to do it.
- `failures`  — every failure lands in `compliance_events` and (once) in Telegram. Nothing is swallowed.
- `preflight` — plan §33 as code: broker unreachable, feed stale or pipeline incomplete each have a
  defined, testable consequence. Unknown is never green.
- `acceptance`/`drill` — the §17 gate to real money, measured from the database rather than asserted.
"""
