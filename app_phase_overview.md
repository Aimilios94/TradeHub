# TradeHub — phase overview

Plain-language status. What each phase does, and whether it's built.

**Costs money:** OpenRouter only (the big AI that writes trade plans). $0.07 used of $10 so far.
Everything else is free — Alpaca, Supabase, Finnhub, Yahoo, Telegram, and the small AI on your own GPU.

**Orders are possible now, but only when you tap Deploy** — and only on the paper account.
Live trading is blocked in the code itself until Phase 8.

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

## ✅ Phase 4 — The decision node
Three steps at 08:00, straight after the scan:
1. **In-play test** — "is this stock genuinely in play today?" Weak or stale reasons get thrown out.
2. **Trade plan** — for survivors, the big AI writes entry, stop, target, size and its reasoning.
3. **Watchlist briefing** — your 8 big-cap names ranked with catalysts, levels and likely setups.

Every number the AI writes is re-checked by code. A plan that breaks a rule is saved as **rejected**
with the reason, never quietly fixed. It has already caught a target invented out of nothing and a
position 3× too big for your risk limit.

These are **proposals only**. You see them; nothing acts on them.

---

## ✅ Phase 5 — Execution
Orders can now reach the broker — but only your paper account, and only when you approve.

- **Deploy button in Telegram** — each signal arrives as a card with ✅ Deploy / ✖️ Cancel.
  Nothing happens until you tap. Only your chat can tap; anyone else is refused.
- **Guardian gate** — 10 checks with teeth, run again the moment you tap: spread too wide, not enough
  buying power, position too big for your risk limit, too many positions, daily loss limit hit,
  a conflicting order, not shortable → **blocked, with the reason**. It already blocked a real one
  (TNON, 3.3% spread against a 2% ceiling).
- **Pre-market safety stop** — a watcher in its own container reacts in under a second if price hits
  your stop before the market opens, because a normal stop does not work pre-market.
- **Real stop at 09:31** — once the market opens, the broker takes over protecting the position.
- **Kill switch** — one command stops all trading instantly.

Three things it refuses to do: place a live-money order, place anything while the kill switch is on,
or place a pre-market order type the broker would silently ignore.

## ✅ Phase 6 — Journal & analytics
The system starts learning from you.

- **Every finished trade is recorded automatically at 16:30** — what you actually paid, what you actually
  made, and how far it drifted from the plan.
- **Real costs, not guesses** — fees and the spread you paid are tracked per trade, plus a running
  28% tax provision (display only, confirm with an accountant).
- **Your numbers** — win rate, expectancy, best and worst time of day, best and worst setup, losing
  streaks. It tells you when there is not enough data to judge yet, instead of pretending.
- **A coach note per trade** — did you follow your own rules, what pattern is showing, what to fix.
  You can add your own note and it gets read too.
- **Sunday review** — turns the week into lessons and concrete suggestions to test.

Suggestions are never applied on their own. Anything that would loosen a risk limit is thrown out
automatically, and the rest waits for a backtest (Phase 7) and your approval.

## ✅ Phase 6.5 — Guardian briefing  ← you are here, just finished
- **Ask it anything, from your phone** — send `/guardian` to the bot (optionally with a question like
  "am I taking too much risk?") and it replies in plain English: where your account stands, what changed,
  active warnings, this month's costs, the tax provision, and up to three concrete suggestions.
  It is a **monitor, not an advisor** — it never gives tax or investment advice, and the tax line always
  says to confirm with an accountant.
- **Monthly rules check** — the system depends on six broker rules (like "pre-market only accepts limit
  orders"). On the 1st of each month it checks whether the broker's own pages changed and tells you which
  rule to re-read. It flags; you decide. It found two dead links the first time it ran.

## ⬜ Phase 7 — Backtest engine *(next)*
Replay a strategy over past data with realistic fills and fees, so you know if it works *before*
risking money. Also adds the remaining chart indicators (VWAP, EMAs, volume profile).

## ⬜ Phase 8 — Hardening + paper month
At least 4 weeks of paper trading. Nothing goes live before this.

## ⬜ Phase 9 — User interface
The dashboard. Deliberately last, after the engine has proven itself.

---

### After Phase 7
Run `/improve-codebase-architecture` — it scans the code for parts that grew awkward and suggests
tidy-ups, as a visual report. Deliberately saved until the backtester is done, so it is not rearranging
code we are still building on. `/diagnosing-bugs` is installed too and can be used any time something
breaks.

### Waiting on you
- **Reddit keys** — optional, widens news coverage on small stocks the big outlets ignore.
- **Finviz Elite** — paid, skipped for now. Only affects exact float numbers.
