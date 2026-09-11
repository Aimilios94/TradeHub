# TradeHub — phase overview

Plain-language status. What each phase does, and whether it's built.

**Costs money:** OpenRouter only (the big AI that writes trade plans). $0.07 used of $10 so far.
Everything else is free — Alpaca, Supabase, Finnhub, Yahoo, Telegram, and the small AI on your own GPU.

**Orders are possible now, but only when you tap Deploy** — and only on the paper account.
Live trading is blocked in the code itself, and stays blocked until the paper month is done and you
approve a separate change that lifts it.

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

## ✅ Phase 7 — Backtest engine
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
| S1 Low-Float Gapper | 42 | 43% | not enough trades to say — but the only one in profit |
| S3 SMB Stocks-in-Play | 156 | — | **fail** |
| S4 HPS Quad Divergence | 150 | 34% | **fail** |
| S5 20/20 Bull Flag | 45 | 9% | not enough trades to say |
| S6 Bear Flag (PDF) | 54 | 11% | not enough trades to say |
| S7 Bear Flag (variant) | 215 | 16% | **fail** |
| S8 Swing Divergence | 82 | 38% | **fail** |

**S1 is the only one that makes money** — about $22 a trade after costs. Before reading anything into
that: it only took 42 trades, which is too few to judge, and when the test split the six months in half
the profit was in one half and not the other. That is the shape a lucky streak makes as well as the
shape a real edge makes, and 42 trades cannot tell you which. The other six lose money after costs,
against the PDF's claimed ~95% win rates.

**Read all of it carefully before concluding anything:** it is one six-month window of one market, and
the entry prices come from
the backtester's own simple rules, not from the trade plans the AI writes live. It is not proof these
strategies are bad — it is proof that none of them may be promoted yet, which is what the gate is for.

**S6 vs S7** (the A/B the plan asked for): S7 is the less bad of the two per trade, but it lost four
times as much in total because it takes four times as many trades. Neither wins.

**S5's optional profit target could not be tuned** — of its 45 trades, 43 were stopped out and 2 ran
out of time. The target was never reached once, so changing it changes nothing. The stop needs
attention first.

## ✅ Phase 8 (part 1) — Hardening  ← you are here
The engine now behaves properly when something **outside** it breaks, and it measures whether it is
allowed to go live instead of asking you to remember.

- **It retries what is safe to retry, and nothing else.** Every call that leaves your PC — prices, news,
  the AI, Telegram — is spaced out so you never hit a free-plan limit, and retried if the other side is
  briefly down. **Placing an order is never retried.** If the broker times out, nobody at this end can
  tell "it never arrived" from "it filled and the reply got lost", and guessing would buy the position
  twice. It stops, tells you, and the truth comes from re-checking with the broker.
- **A wrong key fails immediately** instead of being retried three times, so the alert reaches you fast
  and says what is actually wrong.
- **The safety rules are now code that runs, not a paragraph in the plan.** Before any order:
  is the broker reachable, is the database up, is the kill switch off, is the price actually fresh, is
  the pre-market stop watcher alive? Anything unknown **blocks** — "we couldn't check, assume it's fine"
  is exactly the mistake this exists to prevent. It only ever blocks *opening* a position: closing one
  is never blocked.
- **It will not plan or trade on a stale price.** If the feed goes quiet during the session, the 08:00
  run writes **no plans at all** rather than plans built on a price that has moved on — and it says so
  before spending a cent on the AI. Outside market hours it knows an old quote is normal and stays quiet.
- **The pre-market stop watcher now reports in every 30 seconds.** Before, a watcher that had silently
  died looked exactly like a watcher with nothing to do. Pre-market it is the *only* thing protecting a
  position, so silence is now an alarm.
- **One alert, not four hundred.** A broken feed fails on every symbol. Every failure is still recorded
  — the count is the evidence — but the phone buzzes once per problem per half hour.
- **A half-finished scan says so first.** The message now starts with "PIPELINE INCOMPLETE — treat this
  as a partial view, not as today's market". "3 candidates" from a broken scan reads exactly like a
  quiet morning otherwise.
- **Nightly backup, and a restore that cannot make things worse.** Every table is copied to your PC at
  23:30 with a checksum per file (65,929 rows, 18 MB the first time). Restoring is a separate command
  that does a dry run first, checks every checksum before loading anything, and **adds back what is
  missing without deleting what is newer**. Your `.env` keys are deliberately *not* in the backup —
  keep your own copy where you keep passwords.
- **A setup walkthrough** (`scripts/onboarding.py`): keys, risk limits, which strategies get scanned,
  then a connectivity test that sends you a real Telegram message. Re-runnable, never prints a key, and
  it will not offer to switch you to live — that is still refused in the code itself.

### The fire drill — 10 of 10
The Guardian has ten checks that stand between a signal and an order. A typo in any one turns it into a
permanent green, **and a permanent green looks exactly like a healthy market**. So there is now a drill
that forces each check to block, one at a time, with made-up inputs — no order is ever sent. First run
(11 Sept): all ten blocked correctly, including a live test that engaged the real kill switch, confirmed
nothing could trade, and released it again. It runs itself every Sunday.

### The go-live scorecard
`/ops/acceptance` answers the five conditions for real money by **querying the database**, not by asking
you what you remember. You also get it in plain English on Telegram every Sunday.

| | Condition | Where it stands |
|---|---|---|
| 1 | Every strategy backtested | ✅ **met** — 7 of 7 (S2 is excused: a watchlist briefing has no entry or stop to replay) |
| 2 | 4 straight weeks of paper trading, 20+ trades | ⬜ not started — one command begins the clock |
| 3 | Guardian proven, ledger reconciles, tax provision running | ✅ **met** |
| 4 | Deploy under 2 seconds, stop reaction under 1 second | ❓ unknown — it needs real taps to measure |
| 5 | Funding, W-8BEN, first small strategy, your accountant | 🙋 **yours** — the engine can never tick these |

Condition 4 says **unknown**, not "fine". A stop that never had to fire tells you nothing about how fast
it reacts, so it stays unverified until it fires once.

**Two things the scorecard caught the first time it ran:**
1. **S3 had never been backtested, and S1's only run was a 12-day smoke test with no trades.** Nothing
   was asking, so nobody had noticed. Both now have real baselines: S3 156 trades, **fail**, −$10.29 a
   trade; S1 42 trades, **+$22 a trade but too few to judge**. The condition asks only that the
   measurement exists, not that it flatters — and the scorecard now refuses to count a run that produced
   zero trades or covered under 90 days as a baseline at all.
2. **Two numbers in every backtest report were always zero** — "symbol-days screened" and "symbol-days
   passed" were being displayed but never counted. Fixed. Backtests recorded before today still show 0
   there; the funnel figures next to them were always correct.

## 🕒 Phase 8 (part 2) — The paper month *(running)*
**Day zero is Monday 14 September 2026** — stamped on a weekday on purpose, because a week that starts
on a Saturday holds only five trading days and one market holiday would break the streak and reset the
clock.

Four straight weeks from there, and at least 20 trades recorded. What it needs from you:

- **Leave the PC and Docker on for the 08:00 ET scan.** A week only counts if the scan actually ran on
  at least four of its days. A week where the machine was off does not count, and the streak restarts.
- **Activate workflow 11 in n8n** (localhost:5678) — without it there is no nightly backup and no Sunday
  scorecard. The other ten are already running.
- **Tap Deploy on real signals.** The 20 trades and the deploy speed can only come from your taps;
  nothing about waiting produces them.

You will get the scorecard in plain English every Sunday evening. Progress any time: `/ops/acceptance`.

## ⬜ Phase 9 — User interface
The dashboard. Deliberately last, after the engine has proven itself.

---

### Now due
Phase 9 — the dashboard. The paper month is four weeks of waiting, the UI is four weeks of work, so they
run side by side.

Also available: `/improve-codebase-architecture` — it scans the code for parts that grew awkward and suggests
tidy-ups, as a visual report. It was deliberately saved until the backtester was done — it is done, so
this can run now. `/diagnosing-bugs` is installed too and can be used any time something breaks.

### Waiting on you
- **Activate workflow 11** in n8n (localhost:5678) — the nightly backup and Sunday scorecard.
- **Keep the machine on at 08:00 ET** for the four weeks, and tap Deploy on the signals you like.
- **Your own go-live steps**, whenever you get to them: fund the account modestly, file the W-8BEN with
  Alpaca, pick one small first strategy, and confirm the tax treatment with an accountant. The scorecard
  tracks these as yours; the engine will never tick them off for you.
- **Reddit keys** — optional, widens news coverage on small stocks the big outlets ignore.
- **Finviz Elite** — paid, skipped for now. Only affects exact float numbers.
