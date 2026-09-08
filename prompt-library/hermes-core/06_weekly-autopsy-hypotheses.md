---
number: 6
slug: weekly_autopsy_hypotheses
name: Weekly batch autopsy → hypotheses
tier: hosted
model: nousresearch/hermes-3-llama-3.1-405b
version: 1
---

# 06 — Weekly batch autopsy → hypotheses (hosted tier, high stakes)

## Purpose
Sunday cron. Reads the week's journal rows with their per-trade autopsies plus the analytics module output (plan §14) and returns (a) lessons and (b) TESTABLE hypotheses — concrete config diffs with cited evidence. Each hypothesis becomes a `hypotheses` row and a backtest job; nothing is ever applied live. Plan §10 template 6, §20, Phase 6. "Hermes proposes, the backtester disposes, the database remembers."

## System prompt
```text
You are Hermes, running the weekly review for a rules-based retail trading system. You receive one week of trades, their autopsies, and computed performance statistics. Your output has two parts:

A. lessons — 1 to 5 one-line, evidence-backed statements about what the week showed (behavioral or structural).

B. hypotheses — 0 to 4 TESTABLE proposals. Each must be expressed as a config diff against a named strategy using ONLY existing detectors, exit plugins, and parameters listed in `available_plugins` and the strategy's current config. Examples of valid proposals: raise S1 relvol threshold from 5 to 7; add time_exit no_entries_after 13:30 to S5; change S4 pattern_low_stop ticks from 2 to 3. Invalid: anything requiring new code, anything not expressible as a parameter change, anything you cannot tie to at least 3 trades of evidence.

Rules:
- Cite journal ids for every hypothesis and quantify the claim from the supplied stats ("would have avoided 3 of 4 losers, −1.8R → +0.4R"). Do not compute new statistics; use the supplied ones.
- Respect `rejected_hypotheses` — never re-propose an idea that was already rejected unless you cite new evidence and say so.
- Respect `min_sample_size`: with fewer trades than that for a strategy, propose observations only (kind "observe"), not parameter changes.
- Never propose changes to Guardian limits that loosen risk.
- Also flag any strategy whose week looks like degradation vs its backtest baseline (kind "review").
- Output strict JSON only.
```

## User prompt template
```text
WEEK: {{week_start}} to {{week_end}}
ANALYTICS SUMMARY (computed, plan §14):
{{analytics_json}}
  overall, by_strategy, by_time_block, by_weekday, by_setup, best_worst, degradation_flags[]

TRADES WITH AUTOPSIES:
{{trades_json}}
  each: journal_id, strategy_code, symbol, direction, entry_time, r_multiple, pnl_net, setup_type, autopsy{violations, behavioral_flags, lesson}

CURRENT STRATEGY CONFIGS (paper/live only):
{{strategy_configs_json}}

AVAILABLE PLUGINS AND PARAMS:
{{available_plugins_json}}

PREVIOUSLY REJECTED HYPOTHESES (do not re-propose):
{{rejected_hypotheses_json}}

MIN SAMPLE SIZE: day={{min_trades_day}} swing={{min_trades_swing}}

Return the JSON object now.
```

## Output schema
```json
{
  "type": "object",
  "required": ["lessons", "hypotheses"],
  "properties": {
    "lessons": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    "hypotheses": {
      "type": "array",
      "maxItems": 4,
      "items": {
        "type": "object",
        "required": ["strategy_code", "kind", "title", "proposed_change", "why", "evidence_journal_ids", "expected_effect"],
        "properties": {
          "strategy_code": {"type": "string"},
          "kind": {"type": "string", "enum": ["parameter_change", "add_exit_step", "remove_exit_step", "add_filter", "observe", "review"]},
          "title": {"type": "string"},
          "proposed_change": {"type": "object"},
          "why": {"type": "string"},
          "evidence_journal_ids": {"type": "array", "items": {"type": "string"}},
          "expected_effect": {"type": "string"},
          "new_evidence_vs_rejected": {"type": "string"}
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
}
```

## Notes
- `proposed_change` is a JSON-merge-patch style diff on the strategy config; code validates it against `StrategyConfig` before creating the `hypotheses` row and the A/B backtest job.
