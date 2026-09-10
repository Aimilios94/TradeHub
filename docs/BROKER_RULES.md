# Broker rules this system depends on

The Guardian's knowledge file. Every rule here is one the engine *relies on being true* — if the broker
changes it and we do not notice, something breaks quietly. The monthly freshness job watches the source
pages for changes and raises a `compliance_events` row when one moves.

`version` and `last_checked` are tracked in `app_settings.rules_file`.

---

## R1 — Extended-hours order types
Pre-market (04:00–09:30 ET) and after-hours (16:00–20:00 ET) accept **only** `type=limit`,
`time_in_force=day`, `extended_hours=true`.
**Why it matters:** market orders are rejected, and — far worse — stop and stop-limit orders are
*accepted but never trigger* outside regular hours. Enforced in `app/broker/orders.validate_shape`.
**Source:** https://docs.alpaca.markets/docs/orders-at-alpaca

## R2 — Bracket and OCO orders are regular-hours only
No bracket or OCO order is accepted for extended hours.
**Why it matters:** it is the entire reason the synthetic stop monitor exists, and why real OCO
attachment waits for 09:31. Enforced in the same validator and in `app/execution/oco.py`.
**Source:** https://docs.alpaca.markets/docs/orders-at-alpaca

## R3 — PDT rules were retired
`pattern_day_trader`, `daytrade_count` and `daytrading_buying_power` no longer gate the account, so only
`buying_power` is used. **This is the change the freshness job exists to catch**: a rule that quietly
disappears is as dangerous as one that quietly appears.
**Why it matters:** `app/guardian/gate.check_buying_power` reads `buying_power` alone.
**Source:** https://docs.alpaca.markets/docs/user-protection

## R4 — Free-plan market data
SIP data is delayed ~15 minutes; the last 15 minutes are refused outright. IEX is real-time but its
pre-market book is near-empty for small caps.
**Why it matters:** the 08:00 scan reads SIP pre-market volume through ≈07:45, and `get_bars` clamps to
now−16min. Recorded as decision #10.
**Source:** https://docs.alpaca.markets/docs/real-time-stock-pricing-data

## R5 — Regulatory fees are charged on sells only
SEC fee on sale proceeds (rate reset annually), TAF per share sold with a per-trade cap.
**Why it matters:** Guardian Layer 2 estimates these when the broker reports none — which is always, on
a paper account (decision #46). Rates live in `app_settings.fee_model`.
**Source:** https://alpaca.markets/disclosures

## R6 — Shorting requires a margin account and locate
Shorts need ≥ $2,000 equity, the symbol `shortable`, and `easy_to_borrow` for the Guardian to allow it.
**Why it matters:** `app/guardian/gate.check_shortable` blocks a short otherwise.
**Source:** https://docs.alpaca.markets/docs/margin-and-short-selling

---

**If a source page changes:** read the diff, decide whether the rule above is still accurate, update this
file and bump `app_settings.rules_file.version`, then re-run the freshness job to re-baseline the hash.
The job flags; a human decides.
