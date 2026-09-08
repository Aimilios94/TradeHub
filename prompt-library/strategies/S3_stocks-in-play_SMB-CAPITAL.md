# S3 — SMB Stocks-in-Play

| | |
|---|---|
| **Creator** | SMB Capital |
| **Provenance** | derived (from their public framework) — source notes: [`../_originals/10_F-A_stocks-in-play_SMB-CAPITAL.md`](../_originals/10_F-A_stocks-in-play_SMB-CAPITAL.md) |
| **Style / timeframe** | day · daily context + 5-min execution |
| **Automation** | full_auto scan → human confirm |
| **Status** | backtesting |
| **Frameworks** | F-A (this strategy IS the framework applied as a scan) |
| **Config** | `engine/app/seed/strategies.py` → `S3` |

## Universe (screener)
Gap up or down ≥ 3% · ADV > 2M shares · ATR > $1.00. Liquid names — best-quality data on the free IEX feed.

## Entry conditions — three filters, all required
1. **Reason** — fresh catalyst from `news_sentiment`, earnings prioritized. Hard gate: Hermes one-sentence test "Stock X is in play today because ___" or REJECT (template 04).
2. **Participation** — pre-market volume as % of ADV in tiers (≥10% pass; 20 / 50 / 100% escalate the score); minimum print count to avoid thin "polka-dot" charts.
3. **Structure** — clean level on daily/hourly (52-week high, multi-month base breakout, major S/R, volume-profile levels); REJECT if gapping into mid-range "no man's land": `distance_to_level_atr` > 1.5 ATR.

**Selection:** rank; **cap the focus list at 3**; sector relative strength favors strongest-in-strong-sector longs / weakest-in-weak shorts.
**Midday re-scan 11:30 ET:** flag names already exceeding full-day ADV or ATR.

## Exit logic
Per-setup ORB / level-based bracket → `bracket_oco` after the open; `scale_out_breakeven` after the first partial; `time_exit` 15:55.

## Risk
risk_per_trade 0.5% · max_concurrent 3 · daily loss limit 2R · regular session · limit orders.

## Notes
Recommended (with S8) as the first live candidate — best data quality (plan §17.5).
