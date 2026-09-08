# F-A — SMB "Stocks in Play" Triple Filter

**Creator:** SMB Capital · **Type:** stock-SELECTION framework (how to choose stocks — not an entry) · **Applies to:** all stock day-trade strategies (S1, S3; the S3 scan is this framework operationalized).
**Source notes:** [`../_originals/10_F-A_stocks-in-play_SMB-CAPITAL.md`](../_originals/10_F-A_stocks-in-play_SMB-CAPITAL.md) · **Config:** `engine/app/seed/frameworks.py` → `FA`; DB row `frameworks.code = 'FA'`.

## 1. Reason — "What changed?"
Fresh information forcing revaluation: earnings, guidance, FDA, M&A, lawsuit, multi-month level break. Weak reasons auto-reject ("moved yesterday", "looks interesting").
**Enforcement:** the Hermes one-sentence test — "Stock X is in play today because ___" — as a hard gate ([template 04](../hermes-core/04_one-sentence-in-play-test.md)).

## 2. Participation — "Who cares?"
Gap ≥ 3% · ADV > 2M · ATR > $1 · pre-market volume tiers as % of ADV: 10% pass, 20 / 50 / 100% escalating score · minimum print count (no "polka-dot" charts).

## 3. Structure — "Where's the battlefield?"
Validate daily → hourly → 15-min before intraday. A clean level is required (52-week high, multi-month base, major S/R, volume-profile POC/VAH/VAL). Reject mid-range gaps: `distance_to_level_atr` > 1.5 ATR = "no man's land".

## Plus
- **Cap the focus list at 3.**
- Pass anything not matching a playbook setup.
- Sector strength alignment: strongest-in-strong-sector longs / weakest-in-weak-sector shorts.
- Alerts instead of watching.
- **11:30 ET midday re-scan** for names exceeding full-day ADV / ATR.
