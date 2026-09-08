# S7 — HPS Bear Flag (Variant B — interview supplement)

| | |
|---|---|
| **Creator** | John Kurisko (DayTraderRockStar) |
| **Provenance** | **interview_supplement** — source: [`../_originals/11_HPS-setups_JOHN-KURISKO_interview-supplement.md`](../_originals/11_HPS-setups_JOHN-KURISKO_interview-supplement.md) §3. Kept as an A/B variant against S6; on every OTHER conflict the PDF wins. |
| **Style / timeframe** | day · 5-min · SHORT |
| **Instruments** | as S6 |
| **Automation** | semi_auto |
| **Status** | backtesting |
| **Frameworks** | F-B |
| **Config** | `engine/app/seed/strategies.py` → `S7` |

## Entry regime
- Price trending DOWN under the **200 EMA**.
- 60-10 stochastic **embedded below 20–30** ("banging around the bottom") — seeded threshold 30.
- **Short every 9-3 rotation up above 80.**
- **Re-entry enabled:** `{allowed: true, max_entries: 3, cooldown_bars: 2}` (max_entries and cooldown are config parameters).

## Exit
- **`rotation_complete_exit`** — cover as soon as the 9-3 completes its fast rotation back down (momentum-cycle exit, not price-based). "Must take it off" is a hard rule — perfect for automation.
- Protective stop above the rotation high (`pattern_low_stop`).
- `time_exit` 15:55.

## Risk
As S6.

## Why it exists
The interview describes a materially different bear flag from the PDF (200 EMA vs 20 EMA; embedded 60-10 vs slow+medium under 20; every rotation vs single entry; rotation-complete exit vs partial at 20). Rather than pick, both are seeded and the backtester decides (plan §7 S7, §13 A/B mode).
