# T3 — Performance analysis — adapted spec for the Analytics module

**Original (verbatim):** [`../_originals/06_T3_performance-analysis_OWN.md`](../_originals/06_T3_performance-analysis_OWN.md) · **Plan:** §14 · **Engine:** `engine/app/analytics/` (Phase 6) · **Consumers:** Analytics section, Library track records, weekly autopsy (template 06), degradation trigger

## Inputs
Primary: `journal` rows (joined to `strategy_configs`). Bonus: CSV importer for external-broker history with the original's columns (Symbol, Entry/Exit Date+Time+Price, Shares, P&L, Setup Type).

## Analyses (all from the original, plus strategy/regime/ticker cuts)
1. **Overall:** total trades, winners, losers, win rate, avg win, avg loss, expectancy, largest win/loss, total & avg P&L (gross and net of fees + spread).
2. **By setup type / by strategy_id:** win rate, avg win/loss, total P&L, count.
3. **By time-of-day block:** pre-market, 9:30–10:30, 10:30–11:30, 11:30–12:30, 12:30–1:30, 1:30–2:30, 2:30–4:00 (the original's six blocks + pre-market).
4. **By day of week.**
5. **By ticker** and **by market regime** (event days vs quiet days, tagged via `economic_events`).
6. **Pattern detection:** best/worst hour, best/worst setup, consistently losing periods → flags.

## Outputs
Tables + a written summary + recommendations → stored (`analytics_snapshots`, Phase 6 decides the exact table) and passed as `analytics_json` into template 06.

## Degradation trigger (defensive twin of the weekly loop)
If a paper/live strategy's rolling expectancy or drawdown falls below its backtest baseline, fire a `hypotheses` row with `source = degradation_alert` immediately.

## Rules
Win rate is information, never the gate. Expectancy and profit factor drive decisions (APP_OVERVIEW.md, plan §20).
