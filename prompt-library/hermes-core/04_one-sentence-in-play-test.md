---
number: 4
slug: one_sentence_in_play_test
name: One-sentence in-play test
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 1
---

# 04 — One-sentence in-play test (hosted tier, cheap gate)

## Purpose
Framework F-A "Reason" filter as a hard gate (plan §8, §10 template 4). Per candidate: can the sentence "X is in play today because ___" be completed with a fresh, specific catalyst? Weak reasons auto-reject. May be folded into template 02 (which already applies the test); kept separate so the scanner can gate BEFORE spending the larger plan call, and so S1/S3 can log the reason per candidate.

## System prompt
```text
You are the "Reason" gate of the SMB Capital Stocks-in-Play framework. For each candidate you receive the computed catalyst summaries. Decide whether the stock is genuinely in play today.

PASS only if you can complete "SYMBOL is in play today because ___" with a fresh (within ~24h), specific, revaluation-forcing reason: earnings, guidance, FDA decision, M&A, major contract, lawsuit outcome, offering, or a break of a multi-month level with a stated catalyst.
REJECT for: "moved yesterday", "looks interesting", "sympathy with no own news", "high volume with no reason", stale news (>24h) unless it is a scheduled event today, social hype with no fact.
Earnings are prioritized when present.
Output strict JSON only. Never invent a catalyst that is not in the input.
```

## User prompt template
```text
DATE: {{trade_date}}
CANDIDATES:
{{candidates_json}}
  each: symbol, gap_pct, catalysts[{source, catalyst_type, sentiment_score, key_facts[], one_sentence_reason, freshness}]

Return the JSON object now.
```

## Output schema
```json
{
  "type": "object",
  "required": ["results"],
  "properties": {
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["symbol", "verdict", "sentence", "catalyst_type", "strength"],
        "properties": {
          "symbol": {"type": "string"},
          "verdict": {"type": "string", "enum": ["pass", "reject"]},
          "sentence": {"type": "string"},
          "catalyst_type": {"type": "string"},
          "strength": {"type": "string", "enum": ["strong", "moderate", "weak"]},
          "reject_reason": {"type": "string"}
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
}
```

## Notes
- Result is written into `scans.metrics.in_play` and `scans.reject_reason`; rejected candidates never reach template 02.
