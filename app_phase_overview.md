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

## ✅ Phase 6.5 — Guardian briefing
- **Ask it anything, from your phone** — send `/guardian` to the bot (optionally with a question like
  "am I taking too much risk?") and it replies in plain English: where your account stands, what changed,
  active warnings, this month's costs, the tax provision, and up to three concrete suggestions.
  It is a **monitor, not an advisor** — it never gives tax or investment advice, and the tax line always
  says to confirm with an accountant.
- **Monthly rules check** — the system depends on six broker rules (like "pre-market only accepts limit
  orders"). On the 1st of each month it checks whether the broker's own pages changed and tells you which
  rule to re-read. It flags; you decide. It found two dead links the first time it ran.

## ✅ Phase 7 — Backtest engine  ← you are here, just finished
Replay any strategy over past data and find out whether it actually works — *before* risking money.

- **All 32 indicators and exits are now built.** VWAP, EMAs, RSI, the four Kurisko stochastic bands
  (9-3, 14-3, 40-4, 60-10), the quad, embedded readings, divergence, opening range, 52-week bases,
  double tops and bottoms, sector strength, volume profile, plus all nine exit rules (trailing stops,
  the 2-bar stop, partials on rotation, breakeven, time exits). The same code runs live and in the
  backtest, so the two can never drift apart.
- **It replays the SCANNER, not a chart.** For every past morning it rebuilds the list your 08:00 scan
  would have produced — using only what was knowable that morning — and trades from it. Testing one
  chart you already know the answer to is how people fool themselves.
- **It refuses to flatter you.** Four safeguards the plan demanded, each with a test:
  *delisted stocks are kept in* (a low-float universe with no bankruptcies in it is not clean, it is
  missing the ones that went to zero — Alpaca has 2,867 of them, 18% of the market);
  *volume thresholds are rescaled* between the live feed and the history feed;
  *fills are realistic* — a limit order only fills if the market traded THROUGH your price, not just
  touched it; a stop that gaps fills at the open, not at your stop; you never get more than 10% of a
  bar's volume; and a bar that hit both your stop and your target counts as a **stop**;
  *small samples are called small* — under 100 trades the verdict is "not enough data", never "pass".
- **Win rate still decides nothing.** It is shown; expectancy after costs is what is judged.
- **Suggestions get graded automatically.** Sunday's review writes ideas; two hours later the
  backtester runs each one twice — your current settings versus the proposed change, same dates, same
  costs — and marks it validated or rejected. **Nothing is ever applied on its own.** Promoting a
  strategy needs a passing backtest, and going live is still blocked in the code.
- **History is cached on your PC** (`data/parquet/`) so re-running a test does not re-download anything.

**What it found immediately:** five of your strategies could never have produced a single signal.
S4 and S8 asked for "all four stochastics under 20" and "the fast one crossing back up through 20"
*on the same bar* — which cannot both be true, ever. Over 68 days of SPY the first fired 917 times,
the second 422, together **zero**. S5, S6 and S7 had the same flaw in a different shape. Fixed: the
first condition is now "this happened recently", which is what the strategies always meant. It also
found the journal was subtracting your trading spread twice, and that one dead ticker was making the
data provider refuse a whole batch of 100 — costing 99 good stocks their history on every scan.

**First measured baselines** (March–September 2026, SPY/QQQ, $25k account, 0.5% risk per trade):

| Strategy | Trades | Win rate | Verdict |
|---|---|---|---|
| S4 HPS Quad Divergence | 150 | 34% | **fail** |
| S5 20/20 Bull Flag | 45 | 9% | not enough trades to say |
| S6 Bear Flag (PDF) | 54 | 11% | not enough trades to say |
| S7 Bear Flag (variant) | 215 | 16% | **fail** |
| S8 Swing Divergence | 82 | 38% | **fail** |

All five lose money after costs, against the PDF's claimed ~95% win rates. **Read this carefully
before concluding anything:** it is one six-month window of one market, and the entry prices come from
the backtester's own simple rules, not from the trade plans the AI writes live. It is not proof these
strategies are bad — it is proof that none of them may be promoted yet, which is what the gate is for.

**S6 vs S7** (the A/B the plan asked for): S7 is the less bad of the two per trade, but it lost four
times as much in total because it takes four times as many trades. Neither wins.

**S5's optional profit target could not be tuned** — of its 45 trades, 43 were stopped out and 2 ran
out of time. The target was never reached once, so changing it changes nothing. The stop needs
attention first.

## ⬜ Phase 8 — Hardening + paper month *(next)*
At least 4 weeks of paper trading. Nothing goes live before this.

## ⬜ Phase 9 — User interface
The dashboard. Deliberately last, after the engine has proven itself.

---

### Now due
Run `/improve-codebase-architecture` — it scans the code for parts that grew awkward and suggests
tidy-ups, as a visual report. It was deliberately saved until the backtester was done — it is done, so
this can run now. `/diagnosing-bugs` is installed too and can be used any time something breaks.

### Waiting on you
- **Reddit keys** — optional, widens news coverage on small stocks the big outlets ignore.
- **Finviz Elite** — paid, skipped for now. Only affects exact float numbers.
