# TradeHub — phase overview

Plain-language status. What each phase does, and whether it's built.

**Costs money:** OpenRouter only (the big AI that writes trade plans). $0.07 used of $10 so far.
Everything else is free — Alpaca, Supabase, Finnhub, Yahoo, Telegram, and the small AI on your own GPU.

**Nothing can place an order yet.** That starts in Phase 5.

---

## ✅ Phase 0 — Foundations
Database, the 8 strategies, the prompt library, the empty engine.

## ✅ Phase 1 — Market data
Prices, volume and pre-market history from Alpaca. Learns each stock's *normal* pre-market volume
so it can tell when today is unusual. Runs nightly at 23:00.

## ✅ Phase 2 — Scanner
Every night it measures all ~13,000 US stocks. Every morning at 08:00 it finds the ones matching
your strategy rules and writes the list. Re-checks at 11:30 for names already trading abnormally.

## ✅ Phase 3 — Catalyst & sentiment
For the top names, reads the news (Finnhub + Yahoo) and the small AI on your GPU summarises *why*
the stock is moving — earnings, FDA, contract, hype. Attaches that reason to the morning list.
*Reddit is built but switched off until you add the keys.*

## ✅ Phase 4 — The decision node  ← you are here, just finished
Three steps at 08:00, straight after the scan:
1. **In-play test** — "is this stock genuinely in play today?" Weak or stale reasons get thrown out.
2. **Trade plan** — for survivors, the big AI writes entry, stop, target, size and its reasoning.
3. **Watchlist briefing** — your 8 big-cap names ranked with catalysts, levels and likely setups.

Every number the AI writes is re-checked by code. A plan that breaks a rule is saved as **rejected**
with the reason, never quietly fixed. It has already caught a target invented out of nothing and a
position 3× too big for your risk limit.

These are **proposals only**. You see them; nothing acts on them.

---

## ⬜ Phase 5 — Execution *(next — the careful one)*
The first phase where an order can actually reach the broker.
- **Deploy button** — you approve each trade by hand; nothing is automatic.
- **Guardian gate** — the real risk checks with teeth: spread too wide, too many positions open,
  daily loss limit hit, not shortable → order blocked.
- **Kill switch** — one flag stops everything.
- Pre-market safety stop, real stop attached at 09:30, Telegram alerts.

## ⬜ Phase 6 — Journal & analytics
Records every finished trade, the real P&L after fees, and what worked. Weekly review turns your own
results into ideas to test. This is what starts feeding lessons back into the morning plans.

## ⬜ Phase 6.5 — Guardian briefing
A plain-English "here's your risk right now" summary on demand.

## ⬜ Phase 7 — Backtest engine
Replay a strategy over past data with realistic fills and fees, so you know if it works *before*
risking money. Also adds the remaining chart indicators (VWAP, EMAs, volume profile).

## ⬜ Phase 8 — Hardening + paper month
At least 4 weeks of paper trading. Nothing goes live before this.

## ⬜ Phase 9 — User interface
The dashboard. Deliberately last, after the engine has proven itself.

---

### Waiting on you
- **Reddit keys** — optional, widens news coverage on small stocks the big outlets ignore.
- **Finviz Elite** — paid, skipped for now. Only affects exact float numbers.
