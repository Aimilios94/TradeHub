# F-B — Kurisko Trend-First Rule

**Creator:** John Kurisko (DayTraderRockStar) · **Type:** context/exit framework · **Applies to:** all HPS strategies (S4–S8).
**Source:** the PDF in `../_originals/pdfs/` (canonical) and `../_originals/11_…` (interview). **Config:** `engine/app/seed/frameworks.py` → `FB`.

## The rule
**First step before any HPS trade: determine the prevailing trend — the trend determines the exit strategy.**
Only trade the confirmed signal: the "Super Signal" = quad rotation **plus** a playbook setup together. Skip partial setups.

| Regime | Definition | Exit style |
|---|---|---|
| Confirmed uptrend | rising channel; price above the 200 EMA / VWAP | give the trade room; wider trailing stop; time exits with the 60-10 rotation |
| Downtrend | below the 200 EMA; 60-10 weak / under 30 | take profits fast as the 9-3 reaches its extreme; move to breakeven immediately |

## Scheduling hint
Divergences cluster at active times — the US open and ~03:00 ET (European open). Detectors may weight those windows.

## What stays human
Hand-drawn trendlines, channels, wedges, "coils" are deliberately NOT codified. On semi_auto strategies the signal card shows the computed evidence; the trader supplies the visual confirmation (plan §9).
