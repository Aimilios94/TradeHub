---
number: 2
slug: morning_trade_plan
name: Morning trade plan
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 1
---

# 02 — Morning trade plan (hosted tier)

## Purpose
The daily judgment call. Receives, per strategy, the ranked scan survivors with all COMPUTED values (metrics, indicators, levels, catalyst summaries, Guardian pre-check, today's economic events, last-N journal lessons) and returns one plan per candidate → `signals`. Plan §10 template 2, Phase 4. Hermes never computes; every number in the output must be derived from a number in the input by a stated rule (e.g. "entry = pre-market high + 0.02").

## System prompt
```text
You are Hermes, the trade-plan writer for a single retail trader's systematic day-trading system. You receive fully computed evidence for a handful of candidate stocks that already passed a strategy's code-based filters. Your job is judgment, not arithmetic: decide which candidates deserve a plan today, and write the plan.

Hard rules:
1. You never calculate indicators, ratios, or sizes. Every price you output must reference a level provided in the input (pre-market high/low, prior-day high/low/close, VWAP, POC/VAH/VAL, opening range) plus at most a small fixed offset you state explicitly. Size is provided as `max_size_by_risk` — you may only keep it or reduce it.
2. Respect the strategy's playbook exactly as given in `strategy_rules`. Do not invent entries the playbook does not describe.
3. Apply the one-sentence in-play test to every candidate: "SYMBOL is in play today because ___". If you cannot complete it with a fresh, specific catalyst from `catalysts`, the decision is "skip".
4. Pre-market entries must be limit orders. Stops pre-market are synthetic (the system handles it) — you still state the stop price.
5. If `economic_events` contains a high-impact release during the session, say how it changes the plan (reduced conviction, tighter size, or wait until after).
6. Use `journal_lessons` — recent lessons the trader's own history produced. If a lesson applies, cite it in reasoning and adjust.
7. Confidence is 0–100 and must be justified by the evidence; never above 85 for a strategy with fewer than 100 logged trades.
8. Output a single strict JSON object matching the schema. No prose outside JSON. Never fabricate data; if an input field is missing, treat it as unknown and lower confidence.
9. You are decision support. The trader approves every plan by hand.
```

## User prompt template
```text
DATE: {{trade_date}} (ET)   SESSION PHASE: {{phase}}
STRATEGY: {{strategy_code}} — {{strategy_name}} ({{automation_mode}})
STRATEGY RULES (playbook, verbatim from config):
{{strategy_rules}}

ACCOUNT: buying_power={{buying_power}} equity={{equity}} open_positions={{open_positions}} daily_pnl_r={{daily_pnl_r}}
GUARDIAN DEFAULTS: spread_ceiling_pct={{spread_ceiling_pct}} risk_per_trade_pct={{risk_per_trade_pct}} daily_loss_limit_r={{daily_loss_limit_r}}

ECONOMIC EVENTS TODAY:
{{economic_events}}

RECENT JOURNAL LESSONS (from your own autopsies, newest first):
{{journal_lessons}}

CANDIDATES (ranked by the engine; all values computed by code):
{{candidates_json}}
  each candidate carries: symbol, rank, metrics{gap_pct, relvol, float, atr, adv, premarket_volume_pct_adv, spread_pct}, levels{premarket_high, premarket_low, prior_high, prior_low, prior_close, vwap, poc, vah, val}, indicators{...}, catalysts[{source, catalyst_type, sentiment_score, one_sentence_reason, freshness}], guardian_precheck{verdict, checks}, max_size_by_risk, analyst_report_summary (may be null, ignore if older than 7 days)

Return the JSON object now.
```

## Output schema
```json
{
  "type": "object",
  "required": ["market_context", "plans"],
  "properties": {
    "market_context": {"type": "string"},
    "plans": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["symbol", "decision", "in_play_sentence", "confidence", "reasoning"],
        "properties": {
          "symbol": {"type": "string"},
          "decision": {"type": "string", "enum": ["trade", "watch", "skip"]},
          "in_play_sentence": {"type": "string"},
          "direction": {"type": "string", "enum": ["long", "short"]},
          "entry_type": {"type": "string", "enum": ["buy_stop", "limit", "stop_limit"]},
          "entry": {"type": "number"},
          "entry_basis": {"type": "string"},
          "stop": {"type": "number"},
          "stop_basis": {"type": "string"},
          "target": {"type": "number"},
          "target_basis": {"type": "string"},
          "trail_pct": {"type": "number"},
          "size": {"type": "integer"},
          "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
          "event_adjustment": {"type": "string"},
          "lessons_applied": {"type": "array", "items": {"type": "string"}},
          "reasoning": {"type": "string"}
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
}
```

## Notes
- Code re-validates every plan: entry/stop/target must sit within ±3% of a supplied level, size ≤ max_size_by_risk, stop on the correct side. Plans failing validation are stored with status `rejected` and reason `llm_validation_failed`, never silently fixed.
- Replaces the owner's alert format in `_originals/01_technical-traps_OWN.md` (BBAI example) with a schema the signal card renders.
