# T1 — N-bar trailing stop (exit plugin #3) — adapted spec

**Original (verbatim, Pine terms):** [`../_originals/04_T1_2bar-trailing-stop_OWN.md`](../_originals/04_T1_2bar-trailing-stop_OWN.md) · **Plan:** §9 exit plugin #3 · **Engine:** `engine/app/exits/nbar_trailing_stop.py` (Phase 7) · **Registry name:** `nbar_trailing_stop`

## Behaviour (long; short mirrors)
- **Initial stop** at entry = lowest low of the past N bars (N default 2, parameter).
- **Ratchet:** every time price makes a new N-bar high (current high > max high of the past N bars), move the stop to the lowest low of the past N bars.
- **Stop only moves UP, never down.**
- **Exit** when the stop is hit (live: stop order after 09:30 / synthetic pre-market; backtest: fill model applies).

## Acceptance tests (pytest, Phase 7)
1. Entry on bar t → stop == min(low[t-1], low[t]) (N=2).
2. A new 2-bar high raises the stop to the new 2-bar low; a lower 2-bar low afterwards does NOT lower it.
3. State `trailing_started` flips true on the first ratchet (drives the original's red→green color rule in backtest charts).
4. Short side mirrors (highest high, ratchets down only).
5. N is a parameter; N=1 and N=5 behave consistently.

## What moved where
- Pine `strategy.entry/exit` → engine plugin contract (`initial`, `on_bar`).
- Visual requirements (stop line, red/green, triangle/square markers, show/hide) → backtest report charts and the Phase 9 UI, driven by the plugin's emitted state.
