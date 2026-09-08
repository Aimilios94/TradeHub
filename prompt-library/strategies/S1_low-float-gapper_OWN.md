# S1 — Low-Float Gapper

| | |
|---|---|
| **Creator** | own |
| **Provenance** | own — original: [`../_originals/02_S1_low-float-gapper_OWN.md`](../_originals/02_S1_low-float-gapper_OWN.md) |
| **Style / timeframe** | day · 1-min bars for volume math; trades pre-market and at the open |
| **Automation** | full_auto scan → human confirm (Deploy tap) |
| **Status** | backtesting |
| **Frameworks** | F-A (Reason gate via one-sentence test) |
| **Config** | `engine/app/seed/strategies.py` → `S1`; DB row `strategy_configs.code = 'S1'` |

## Universe (screener — Finviz Elite CSV)
Price $2–20 · gap ≥ +10% · float < 20M shares.

## Entry conditions
**Hard (all):**
1. Cumulative pre-market volume (sum of 1-min IEX bars from 04:00 ET) ≥ **5× the 14-day average pre-market volume, same IEX feed** (`relative_volume_premarket`, baseline_days 14, feed iex).
2. Gap ≥ +10% (`gap_pct`).
3. Float < 20M (`float_shares`, Finviz primary / FMP fallback).

**Soft:** news catalyst preferred (score from `news_sentiment`); pre-market high = breakout level for the buy-stop.
**Hermes gate:** `one_sentence_in_play_test`. Hermes then ranks the top 5 and writes plans (template 02): buy-stop above the pre-market high or breakout level.

## Exit logic (ordered)
1. `synthetic_premarket_stop` (pre-market; websocket monitor; closing LIMIT on breach, limit offset 1%).
2. `bracket_oco` attached at 09:30 (stop-loss + take-profit from the plan).
3. `trailing_pct` 4.5% — optional intraday swap.
4. `time_exit` flat by 15:55.

## Risk
Limit orders ONLY (spreads are the killer). risk_per_trade 0.5% · max_concurrent 2 · daily loss limit 2R · session premarket+regular · spread ceiling 2% (Guardian, hard).

## Re-entry
Not allowed.

## Known caveats
- IEX understates true pre-market volume on small caps; the 5× ratio is valid only because the baseline is IEX too (plan §3.2). Log IEX↔SIP calibration during the paper month.
- Backtest must model wide spreads and partial fills; survivorship-bias check mandatory (delisted low-floats) — plan §13.
- Pre-market: no real stops exist (plan §3.4); the synthetic stop is best-effort and the app must say so (plan §19).

## What changed vs the original
The original five bullets are kept verbatim in `_originals/02`. Adaptation: "relative volume 5× the 14-day average" is made feed-consistent (IEX vs IEX); "ideally has news" becomes a soft score plus the F-A one-sentence gate; the exit stack and risk block were added (the original had none).
