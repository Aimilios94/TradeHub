---
number: 5
slug: per_trade_autopsy
name: Per-trade autopsy
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 1
---

# 05 — Per-trade autopsy (hosted tier)

## Purpose
Adapts the owner's original autopsy template (`../_originals/05_T2_ai-trade-autopsy_OWN.md`). Every input the original asked the user to paste (setup details, playbook rules, context, the chart screenshot) is auto-filled from `signals` / `orders` / `journal` / strategy config / bars. The chart screenshot is replaced by a small text-serialized OHLCV window plus computed indicator values at entry and exit — strictly more reliable for a text model. Answers the owner's five questions and adds tilt detection. Output → `journal.autopsy`. Plan §10 template 5, Phase 6.

## System prompt
```text
You are a trading coach reviewing ONE completed trade for a retail day trader who runs a rules-based system. You receive the plan, the playbook rules, what actually happened, the bars around the trade, indicator values, the day's context, and the trader's own subjective note. Everything numeric was computed by code; you interpret and coach.

Answer these questions, in this order:
1. Did the entry meet the playbook rules? Was it clean? Check each rule individually against the supplied values.
2. Was the exit plan followed? If not, exactly what was violated (early exit, moved stop, oversized, held past time exit, ignored partial rule)?
3. What behavioral pattern is showing? Tilt detection: revenge trading after losses, early exits after consecutive losers, oversizing, moving stops, trading outside the plan's session, hesitation on valid signals. Use trade_number_of_day, running_pnl_r, and the subjective note as evidence. Say "no pattern detected" when the evidence does not support one.
4. What ONE thing should the trader focus on improving?
5. Coach note: 3–6 sentences, direct and specific, as if speaking to the trader.

Rules: cite the supplied numbers when judging; never invent price action beyond the bars given; if a rule cannot be evaluated from the inputs, mark it "unknown" rather than guessing. Output strict JSON only.
```

## User prompt template
```text
TRADE: {{symbol}} {{direction}} on {{trade_date}} — strategy {{strategy_code}} {{strategy_name}} (source: {{source}})
SETUP TYPE: {{setup_type}}   TRADE NUMBER OF DAY: {{trade_number_of_day}}   RUNNING P&L BEFORE THIS TRADE: {{running_pnl_r}} R

PLAN (from the signal / manual ticket):
{{planned_json}}
  entry, stop, target, trail_pct, size, exit_rule, session

PLAYBOOK RULES (from strategy config):
ENTRY: {{entry_rules_text}}
EXIT: {{exit_rules_text}}

ACTUAL (from orders / journal):
{{actual_json}}
  entry_time, entry_price, exit_time, exit_price, qty, pnl_net, r_multiple, fees, spread_cost, stop_changes[], partials[]

INDICATORS AT ENTRY: {{indicators_at_entry_json}}
INDICATORS AT EXIT: {{indicators_at_exit_json}}

BARS AROUND THE TRADE ({{bar_timeframe}}, ET, o/h/l/c/v; window {{bars_window}}):
{{bars_text}}

TRADER'S NOTE (verbatim): "{{subjective_note}}"

Return the JSON object now.
```

## Output schema
```json
{
  "type": "object",
  "required": ["rule_compliance", "entry_clean", "exit_followed", "violations", "behavioral_flags", "focus_area", "coach_note", "lesson"],
  "properties": {
    "rule_compliance": {
      "type": "array",
      "items": {"type": "object", "required": ["rule", "met"], "properties": {"rule": {"type": "string"}, "met": {"type": "string", "enum": ["yes", "no", "unknown"]}, "evidence": {"type": "string"}}}
    },
    "entry_clean": {"type": "boolean"},
    "exit_followed": {"type": "boolean"},
    "violations": {"type": "array", "items": {"type": "string"}},
    "behavioral_flags": {"type": "array", "items": {"type": "string", "enum": ["revenge_trading", "early_exit_after_losses", "oversizing", "moved_stop", "outside_session", "hesitation", "overtrading", "none"]}},
    "focus_area": {"type": "string"},
    "coach_note": {"type": "string"},
    "lesson": {"type": "string"}
  },
  "additionalProperties": false
}
```

## Notes
- `lesson` is a one-line, reusable statement ("On S1, exits before the stop after two losers cost 1.4R this week") — these are what template 02 receives as `journal_lessons` and what template 06 aggregates.
