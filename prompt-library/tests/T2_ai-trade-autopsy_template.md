# T2 — AI trade autopsy — original + adapted

**Original (verbatim):** [`../_originals/05_T2_ai-trade-autopsy_OWN.md`](../_originals/05_T2_ai-trade-autopsy_OWN.md) · **Adapted prompt:** [`../hermes-core/05_per-trade-autopsy.md`](../hermes-core/05_per-trade-autopsy.md) · **Plan:** §10 template 5 · **Output:** `journal.autopsy` (JSON)

## Mapping: what the original asked you to paste → where it now comes from
| Original block | Auto-filled from |
|---|---|
| Setup details (type, stock, date, entry, planned exit/stop, actual exit) | `signals` + `orders` + `journal` |
| Entry rules / exit rules (playbook) | strategy config `entry_conditions` / `exit_logic`, rendered as text |
| Context (why you entered, what you planned, why you exited, trade # of day) | `journal.planned`, `journal.actual`, `journal.trade_number_of_day`, running P&L; **your subjective note** stays manual |
| Chart screenshot | replaced by a small text-serialized OHLCV window + computed indicator values at entry and exit (text models read numbers better than pictures) |

## The five questions (kept)
1. Did the entry meet the rules — was it clean?
2. Was the exit plan followed — what was violated?
3. What behavioral pattern is showing? (tilt detection: revenge trading, early exits after losses, oversizing, moving stops…)
4. What to focus on improving?
5. Coach-style feedback.

Plus one addition: a one-line reusable **lesson** that feeds future morning plans and the weekly review.

## Journal habit (from APP_OVERVIEW.md)
Add the subjective note the same day, while you remember how it felt — it is the tilt-detection input.
