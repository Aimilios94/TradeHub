---
number: 12
slug: pm_eod_report
name: Polymarket EOD report (E3)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in extension phase E3 — PAPER ONLY
---

# 12 — Polymarket EOD report (E3)

## Purpose
Daily Telegram report from computed figures: paper PnL, best/worst trade and wallet, rule changes made in the sandbox today (versioned `pm_rules` rows with reason/evidence/before/after), top lesson, and the **benchmark verdict**: did bot-filtered beat blind leaderboard copy, the watchlist, and the skipped set (missed winners / avoided losers)? That benchmark is the whole point of E3.

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["headline", "pnl_line", "best_worst", "rule_changes", "top_lesson", "benchmark_verdict"],
  "properties": {
    "headline": {"type": "string"},
    "pnl_line": {"type": "string"},
    "best_worst": {"type": "string"},
    "rule_changes": {"type": "array", "items": {"type": "string"}},
    "top_lesson": {"type": "string"},
    "benchmark_verdict": {"type": "string"}
  }
}
```
