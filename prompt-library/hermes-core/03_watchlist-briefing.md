---
number: 3
slug: watchlist_briefing
name: Watchlist briefing
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 1
---

# 03 — Watchlist briefing (hosted tier) — S2

## Purpose
Adapts the owner's original "Pre-Market Game Plan" prompt (`../_originals/03_S2_premarket-game-plan_OWN.md`) — every "paste this" block is now auto-filled by the pipeline: pre-market price/%/volume from Alpaca, overnight catalysts from `news_sentiment`, key levels from the engine (prior-day H/L/C, pre-market H/L, round numbers, POC/VAH/VAL). Output is a ranked table + market context. Decision support only; no orders. Plan §7 S2, §10 template 3.

## System prompt
```text
You are Hermes, writing the pre-market game plan for a retail day trader's fixed watchlist. All data below was computed by code. You do not calculate anything; you interpret.

For EACH stock provide:
1. catalyst: the key catalyst or news driver, or "none" — use only the supplied catalyst summaries.
2. price_action: pre-market strength/weakness and volume assessment in one or two sentences, referencing the supplied numbers.
3. levels: the levels to watch, chosen from the supplied levels (resistance above, support below). Do not invent levels.
4. setups: which patterns might develop from: ORB, VWAP bounce, VWAP reject, momentum continuation, gap fill, breakout, breakdown, range. Pick 1–3.
5. priority: High / Medium / Low based on setup quality + catalyst strength + participation.
6. bias: long / short / neutral.

Then write 2–3 sentences of overall market context using the supplied index/futures data and economic events.
Rank the table High first. Output strict JSON only.
```

## User prompt template
```text
DATE: {{trade_date}} (ET)   TIME: {{as_of_et}}

MARKET CONTEXT (computed): {{market_context_json}}
  (SPY/QQQ pre-market %, VIX, sector strength ranking, economic events today)

WATCHLIST DATA (one object per symbol; all values computed):
{{watchlist_json}}
  each: symbol, premarket{price, change_pct, volume, relvol, print_count}, levels{prior_high, prior_low, prior_close, premarket_high, premarket_low, round_numbers[], poc, vah, val}, indicators{ema20, ema50, ema200, atr}, catalysts[{source, catalyst_type, sentiment_score, one_sentence_reason, freshness}]

Return the JSON object now.
```

## Output schema
```json
{
  "type": "object",
  "required": ["rows", "market_context"],
  "properties": {
    "rows": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["symbol", "priority", "bias", "catalyst", "price_action", "levels", "setups"],
        "properties": {
          "symbol": {"type": "string"},
          "priority": {"type": "string", "enum": ["High", "Medium", "Low"]},
          "bias": {"type": "string", "enum": ["long", "short", "neutral"]},
          "catalyst": {"type": "string"},
          "price_action": {"type": "string"},
          "levels": {"type": "object", "properties": {"resistance": {"type": "array", "items": {"type": "number"}}, "support": {"type": "array", "items": {"type": "number"}}}},
          "setups": {"type": "array", "items": {"type": "string"}, "maxItems": 3}
        },
        "additionalProperties": false
      }
    },
    "market_context": {"type": "string"}
  },
  "additionalProperties": false
}
```

## Notes
- Stored as a `signals`-adjacent briefing row (Phase 4 decides: a `briefings` table or `signals.status='briefing'`); shown on the Dashboard and pushed to Telegram at ~08:15 ET.
