# TradeHub — Full Implementation Specification (Agent Handoff Document)

**Version:** 1.3 · **Date:** 2026-07-12 · **Status:** Design phase closed, ready to build
**v1.1 added:** Hypotheses/Strategy Lab (§20), Alpaca connectivity & SDK policy (§21), Extension Modules (§22), Prompt & Strategy Library folder (§23).
**v1.2 added:** Walkthrough (§25), Ask Hermes (§26), CVD & Volume Profile detectors (§27), manual order entry & position management (§28), economic calendar (§29), Order-Flow Research Track (§30), upgrade-path memo (§31), UI mockup (§32), onboarding & failure handling (§33).
**v1.3 adds:** free macro/news data sources — FRED, EIA, Finnhub (§35); E5 Long-Term Investment Analysis reinstated (§36); Fundamentals Lab with screener + deep-dive modes (§37); Figma rejected for Phase 9 (§38); and `APP_OVERVIEW.md`, the plain-language reference file the builder must ship (§39).
**Owner context:** Single user, based in Portugal (EU). Already self-hosts: n8n, Docker, Ollama. Budget €40–60/mo. Backend first, UI last. Paper trading mandatory before live.

---

## 1. Project Summary

A personal trading hub that: (1) shows real-time market data on demand; (2) runs an automated pre-market pipeline that scans, enriches, and ranks trade candidates; (3) uses an LLM ("Hermes") for judgment tasks (trade plans, autopsies) — never for math; (4) executes orders through Alpaca with a human-confirm button; (5) backtests any strategy deterministically in code; (6) self-improves via a journal → autopsy → hypothesis → backtest → promote loop; (7) protects the user via a "Guardian" cost/compliance agent.

**Core design principle: strategies are DATA, not code.** Every strategy is a JSON config row in `strategy_configs`. No strategy logic is ever hardcoded in workflows, prompts, or the engine.

**Second principle: rules in code, judgment in LLM.** Indicators, backtests, fees, margin checks = deterministic code. Interpretation, ranking, catalyst analysis, coaching = Hermes.

---

## 2. Locked Stack & Budget (~€40–45/mo)

| Component | Role | Cost |
|---|---|---|
| **Alpaca** (paper first) | Broker + PRIMARY market data (free IEX feed: real-time top-of-book quotes, trades, pre-market 1-min bars WITH volume, websocket; free SIP historical bars) | €0 |
| **Finviz Elite** | Pre-market screener via authenticated CSV export (gap %, price, rel-vol, FLOAT <20M — the hard datapoint). One HTTP request, no scraping | ~$39.50/mo (~$25/mo if annual) |
| **Supabase** (free tier) | Database, auth, Edge Functions (API key proxy) | €0 |
| **n8n** (self-hosted, exists) | Orchestrator: crons, workflows, webhooks | €0 |
| **Ollama** (self-hosted, exists) | Local Hermes 3 8B: high-volume/low-stakes summarization | €0 |
| **OpenRouter** | Hosted Hermes 3 70B/405B: low-volume/high-stakes calls (trade plan, autopsy). Pay-per-token | ~€2–5/mo |
| **Reddit official API** | Sentiment source (OAuth, free) | €0 |
| **Yahoo Finance RSS** | Headlines | €0 |
| **Polymarket API** | Event-probability context | €0 |
| **Python container** (Docker, exists) | Backtest engine (FastAPI + vectorbt or backtesting.py) + websocket exit monitor | €0 |
| **FMP free tier** | Backup float data (250 calls/day) | €0 |
| **Twelve Data free key** | BACKUP ONLY: index values (SPX/NDX) and VIX for dashboard display. NOT core | €0 |
| **Firecrawl free credits** | Last-resort scraper for pages with no API. NOT core | €0 |
| **Telegram bot** | Alerts (signals, fills, errors, Guardian warnings) | €0 |
| **Cloudflare Tunnel or Tailscale Funnel** | Expose self-hosted n8n webhooks to the app (UI phase) | €0 |

**Known future upgrades (NOT now):** Alpaca Algo Trader Plus ~$99/mo (full real-time SIP feed — buy only if paper month shows IEX volume ratios are too noisy; one-line feed change `feed=iex`→`feed=sip`). Level 2 depth: NOT available from Alpaca for equities at any price — if ever wanted, it is a human side-screen tool (~€10/mo IBKR/Webull TotalView), never app infrastructure. Futures: Alpaca does not offer futures (roadmap only) — index exposure is via ETFs.

---

## 3. Verified Technical Constraints (source-verified July 2026 — builder MUST respect)

These were verified against current documentation during design. Do not "fix" them from memory of older tutorials.

1. **Twelve Data is NOT the market data source.** Pre/post-market data on its `/time_series` requires the Pro plan (~$99/mo), and even then extended-hours OHLC is supplied WITHOUT volume — which kills pre-market relative-volume math. Free tier is 8 credits/min, 800/day. It survives only as an optional index/VIX display backup.
2. **Alpaca IEX free feed specifics.** Quotes are IEX top-of-book (Level 1), trades are IEX-only (~2–3% of consolidated tape). Pre-market 1-min bars include volume but it is IEX-only volume. **Therefore: the 14-day average pre-market volume baseline MUST be computed from the same IEX feed** so the relative-volume ratio is apples-to-apples. Log IEX-vs-SIP calibration during the paper month (SIP available in historical bars for free after ~15 min delay).
3. **No Level 2 for equities on Alpaca at any tier.** Only Level 1. (L2 exists only for crypto.) Do not design anything that needs equity order-book depth.
4. **Alpaca extended-hours order rules.** Pre/post-market accepts ONLY: `type=limit`, `time_in_force=day`, `extended_hours=true`. Bracket orders are NOT accepted for extended hours. Stop, stop-limit, trailing-stop orders DO NOT TRIGGER outside regular hours. Trailing stops are single orders only (never bracket legs). Market orders are rejected pre-market.
5. **The PDT rule NO LONGER EXISTS (retired; Alpaca implemented the Intraday Margin Framework on 2026-06-04).** No $25,000 minimum, no day-trade counting. Replaced by Intraday Buying Power + intraday margin calls; repeated failures to meet calls within 5 business days → account restriction up to 90 days. $2,000+ equity unlocks margin/shorting and 4x intraday buying power. **API fields `pattern_day_trader`, `daytrade_count`, `daytrading_buying_power` were REMOVED from the Alpaca API (by 2026-07-06). Build ONLY on `buying_power`.** Older tutorials referencing removed fields are wrong.
6. **Alpaca tradeable assets:** US stocks, US-listed ETFs, options, crypto. NO futures, forex, or international equities. Index trading = index ETFs (SPY, QQQ, DIA, IWM, sector ETFs). Leveraged/inverse ETFs (TQQQ/SQQQ) are permitted intraday-only tools.
7. **Shorting** requires margin account ($2,000+ equity) and easy-to-borrow status per ticker — non-issue for SPY/QQQ; Guardian must check ETB for individual stocks.
8. **Finviz has no free API.** Finviz Elite's CSV export URL (authenticated) is the screener source. Do not scrape finviz.com HTML.
9. **Scraping Google = CAPTCHA.** Use Reddit official API + Yahoo RSS + Polymarket API. Firecrawl only as last resort, with n8n Loop + Wait 2–3s stagger.
10. **LLMs never compute.** No indicator math, no backtest arithmetic, no fee/margin calculations in prompts. Hermes receives computed values and returns judgments.
11. **API keys never ship to the frontend.** All frontend market-data reads go through a Supabase Edge Function proxy (keys server-side, 1–5s response cache).
12. **Commission-free but not cost-free:** SEC/TAF micro-fees on sells (log from Alpaca activities); spread/slippage is the dominant real cost on low-floats (0.5–2% round trip) — limit orders only.

---

## 4. System Architecture ("two engine speeds")

```
┌─────────────────────────────  USER APP (UI phase, later)  ─────────────────────────────┐
│  Dashboard · charts · watchlist · signal cards · Deploy button · Guardian button        │
└──────┬────────────────────────────────┬────────────────────────────────┬───────────────┘
       │ (reads, real-time)             │ (reads, analytical)            │ (actions)
       ▼                                ▼                                ▼
┌───────────────────┐          ┌────────────────┐              ┌──────────────────────┐
│ Supabase Edge Fn  │          │   Supabase DB   │◄─────────────│  n8n (self-hosted)   │
│ proxy → Alpaca    │          │  (9 tables)     │   writes     │  webhooks + crons    │
│ data (IEX feed)   │          └────────────────┘              └───────┬──────────────┘
│ 1–5s cache        │                    ▲                              │
└───────────────────┘                    │ results                      │ orchestrates
                                         │                              ▼
                              ┌──────────┴──────────┐   ┌─────────────────────────────────┐
                              │ Python service       │   │ Finviz CSV · Alpaca data/trading │
                              │ (Docker/FastAPI):    │   │ Reddit API · Yahoo RSS ·         │
                              │ backtester +         │   │ Polymarket · Ollama (8B local) · │
                              │ websocket exit       │   │ OpenRouter (Hermes 70B/405B) ·   │
                              │ monitor              │   │ Telegram alerts                  │
                              └─────────────────────┘   └─────────────────────────────────┘
```

- **Visual Radar (fast path):** app → Edge Function proxy → Alpaca quotes/bars. Zero n8n, zero DB writes. Millisecond-fresh charts on open.
- **Execution Factory (slow path):** n8n crons/webhooks run scan → enrich → decide → (human confirm) → execute → journal. All state in Supabase.
- **Deterministic core:** Python service owns all math (indicators via detectors, backtests, exit monitoring).

Daily timeline (ET): 8:00 scan cron → 8:05–8:15 enrichment + Hermes plan → signal cards + Telegram push (Guardian pre-checks already attached) → human confirms → pre-market limit entry with synthetic stop → 9:30 attach real OCO exits → intraday monitors (incl. 11:30 midday re-scan) → EOD journal cron → weekly autopsy cron → monthly rules-freshness cron.

---

## 5. Database Schema (Supabase / Postgres — 10 core tables; extension modules add their own, see §22)

Single-user system: enable RLS on all tables, policy = authenticated owner only. All JSONB fields validated in code.

1. **`strategy_configs`** (central table — everything references it)
   - `id`, `name`, `creator` (own | smb_capital | daytraderrockstar | …), `provenance` (**pdf_canonical | interview_supplement | own | derived**)
   - `style` (scalp | day | swing), `timeframe` (e.g. 1min/5min/15min/60min/daily), `automation_mode` (**full_auto | semi_auto**), `status` (**draft → backtesting → paper → live → retired**)
   - `universe` JSONB: `{type: "screener" | "watchlist", params: {...} | symbols: [...]}`
   - `entry_conditions` JSONB (detector expressions + parameters)
   - `exit_logic` JSONB (ordered list of exit plugins + parameters)
   - `risk` JSONB: `{sizing_rule, risk_per_trade_pct, max_concurrent, daily_loss_limit_R, session}`
   - `reentry` JSONB: `{allowed: bool, max_entries, cooldown_bars}` (default `{allowed:false}`)
   - `prompt_template_id` (nullable, for Hermes-involved strategies), `notes`, timestamps
2. **`scans`** — daily screener output per strategy: `strategy_id`, `run_at`, `symbol`, raw metrics JSONB (gap, relvol, float, ATR, ADV, premarket vol %, levels), `rank`, `passed` bool.
3. **`signals`** — Hermes/engine trade plans: `strategy_id`, `symbol`, `direction`, `entry_price`, `stop`, `target`, `trail_pct`, `size`, `confidence`, `reasoning`, `guardian_precheck` JSONB (green/yellow/red + details), `status` (proposed | approved | rejected | expired | executed).
4. **`orders`** — every Alpaca order: `signal_id`, alpaca order id, type, side, qty, limit price, `extended_hours` bool, status timeline, fill price/time, raw payloads.
5. **`journal`** — one row per completed trade: `strategy_id`, `signal_id`, symbol, direction, entry/exit price+time, qty, P&L (gross/net), fees, spread_cost, catalyst, setup_type, market_regime, planned-vs-actual fields, `subjective_note` (user's post-trade note — feeds tilt detection), `autopsy` JSONB (filled by Hermes).
6. **`news_sentiment`** — per symbol per day: source (reddit|yahoo|polymarket|other), condensed JSON from local 8B: `{catalyst_type, sentiment_score, key_facts[], one_sentence_reason}`.
7. **`backtests`** — `strategy_id`, config snapshot, date range, feed used, metrics JSONB (trades, win rate, expectancy, avg win/loss, max DD, profit factor, per-year breakdown), `fill_model` params, `hypothesis_id` (nullable), verdict.
8. **`guardian_ledger`** — fees (SEC/TAF from Alpaca activities), realized spread cost per fill (fill vs quote midpoint at order time), realized P&L, running **tax provision (28% of net realized gains, PT flat CGT — display only, not advice)**, FX/funding events.
9. **`compliance_events`** — Guardian warnings/blocks with reasons, intraday-margin-call records, rules-file updates (from monthly freshness check), kill-switch toggles.
10. **`hypotheses`** — the home of every improvement suggestion (full spec §20): `strategy_id`, `proposed_change` JSONB (config diff), `why` (Hermes reasoning + cited journal evidence), `source` (weekly_autopsy | manual | degradation_alert), linked `backtest_id`, `status` (**proposed → backtesting → validated | rejected → promoted**), timestamps. Rejected rows are kept visible (prevents re-suggesting dead ideas).

Seed data shipped with the schema: 8 strategy rows (§7), 2 framework definitions (§8), 7 core prompt templates (§10), engine plugin registry (§9), default `promotion_criteria` (§20).

**Bar storage policy:** keep only recent bars (rolling ~90 days, 1-min) and daily bars in Supabase; deep 1-min history lives as parquet files on the home server, pulled by the backtester — protects the Supabase free-tier quota.

---

## 6. Strategy-as-Config Contract

Every strategy MUST be fully expressible as: `universe → entry_conditions (detector expressions) → exit_logic (plugin chain) → risk → reentry`. If a strategy needs a detector or exit the engine lacks, the engine gains a **plugin** (one small, tested Python function) — the strategy config then references it. Parameter changes (thresholds, lookbacks, trail %) require NO code. Promotion between statuses is a field flip gated by backtest evidence.

---

## 7. Seed Strategies (8 configs — encode as INSERT rows in Phase 0)

### S1 — Low-Float Gapper
- **creator:** own · **provenance:** own · **style:** day · **timeframe:** 1min bars for volume math, trade pre-market/open · **automation:** full_auto scan → human confirm · **status:** backtesting
- **Universe:** screener (Finviz Elite CSV): price $2–20, gap ≥ +10%, float < 20M shares.
- **Entry conditions:** cumulative pre-market volume (sum of 1-min IEX bars from 04:00 ET) ≥ **5× the 14-day average pre-market volume (same IEX feed)**; news catalyst preferred (soft score from `news_sentiment`); Hermes ranks top 5 and writes plans (buy-stop entry above pre-market high or breakout level, per plan).
- **Exit logic:** pre-market = synthetic stop (websocket monitor); at 09:30 attach OCO (stop-loss + take-profit); optional trailing % swap intraday.
- **Risk:** limit orders ONLY (spreads are the killer here); risk-per-trade from config; Guardian spread ceiling applies hard.
- **Known data caveats:** IEX understates true pre-market volume on small caps — ratio is valid only because the baseline is IEX too. Backtest must model wide spreads/partial fills; survivorship bias check mandatory (delisted low-floats).

### S2 — Pre-Market Watchlist Briefing
- **creator:** own · **provenance:** own · **style:** day (decision support) · **automation:** full_auto briefing, trades manual · **status:** paper-ready immediately (no order automation required)
- **Universe:** watchlist `["TSLA","NVDA","AMD","META","AAPL","GOOGL","MSFT","AMZN"]` (user-editable).
- **Pipeline:** for each symbol auto-assemble: pre-market price/%/volume (Alpaca), overnight catalysts (news_sentiment), key levels R/S (engine: prior-day H/L/C, pre-market H/L, round numbers). Hermes (hosted) returns per stock: catalyst driver, price-action assessment, levels to watch, likely setups (ORB, VWAP bounce, momentum continuation), priority High/Med/Low — as a ranked table + 2–3 sentence market context.
- **Note:** this replaces the user's original manual copy-paste prompt; everything it asked the user to paste is auto-filled by the pipeline.

### S3 — SMB Stocks-in-Play
- **creator:** SMB Capital · **provenance:** derived (from their public framework) · **style:** day · **timeframe:** daily context + intraday execution · **automation:** full_auto scan → human confirm · **status:** backtesting
- **Universe:** screener: gap up/down ≥ 3%, ADV > 2M shares, ATR > $1.00. (Liquid names — best-quality data on free IEX feed.)
- **Entry conditions (three filters, all required):**
  1. **Reason:** fresh catalyst matched from news_sentiment; earnings prioritized. Hard gate: Hermes one-sentence test — "Stock X is in play today because ___" or REJECT.
  2. **Participation:** pre-market volume as % of ADV, scored in tiers (≥10% pass, 20/50/100% escalating score); avoid thin "polka-dot" charts (min pre-market print count).
  3. **Structure:** clean level on daily/hourly (52-week high, multi-month base breakout, major S/R); REJECT if gapping into mid-range "no man's land" — codified as distance-to-nearest-major-level > X ATRs.
- **Selection:** rank, **cap focus list at 3**; sector relative strength favors strongest-in-strong-sector longs / weakest-in-weak shorts. **Midday re-scan cron 11:30 ET:** flag stocks already exceeding full-day ADV or ATR.
- **Exit logic:** per-setup (ORB / level-based bracket), OCO after open.

### S4 — HPS Quad Divergence ("Super Signal")
- **creator:** DayTraderRockStar (John Kurisko) · **provenance:** **pdf_canonical** · **style:** day · **timeframe:** 5-min (ported up from 1-min futures scalping — our stack does not scalp 1-min on IEX data) · **instruments:** SPY, QQQ + liquid stocks from S3 · **automation:** **semi_auto** (engine detects, human visually confirms channel/trend context, taps deploy) · **status:** backtesting
- **Indicators:** stochastic quad **9-3, 14-3, 40-4, 60-10** (40-4 is canonical per PDF; "44-4" from interview = transcription error, do not seed), EMA 20/50/200, VWAP, prior-day levels.
- **Entry (long form; short mirrors):** Stage 1: ALL four bands < 20. Stage 2: bounce lifts 9-3. Stage 3: price returns to equal-or-lower low (or double bottom) while 9-3 holds above its prior low (divergence). Trigger: 9-3 turns up through 20. Optional confluence (config flags): 5-min/60-min stoch turning up off oversold; reversal candle capping the low; alignment with lower channel/trendline (trendlines = human-confirm step, not code).
- **Exit logic:** stop 1–2 ticks under pattern-low candle (pattern-low stop plugin); partial at first 9-3 rotation back above 80; trail remainder — **trend determines exit style** (Framework B): confirmed uptrend → wider trailing stop, time exit via 60-10 rotation.
- **Note:** PDF claims ~95% win rate — treat as marketing; backtest produces the real number and overrides.

### S5 — HPS 20/20 Bull Flag
- **provenance:** **pdf_canonical** · **style:** day · **timeframe:** 5-min · **instruments:** SPY/QQQ/liquid stocks · **automation:** semi_auto · **status:** backtesting
- **Entry:** market uptrend (price above 50 EMA); aggressive flagpole off the 20 EMA (steeper-than-average move — measure vs rolling slope baseline); slow (60-10) stochastic **embedded above 85, preferably 90** and medium holding above 80; pullback to 20 EMA/50 EMA zone; 9-3 rotates down to 20. **Buy into weakness** at 20 EMA touch or 9-3 = 20.
- **Exit:** partial profits as 9-3 crosses back above 80; stop to breakeven on remainder; begin profit-taking on first candle out of the flag. Optional non-canonical target (interview): ATR-relative "next push" 0.5–1.0× ATR — parameter for backtester, flagged provenance=interview_supplement.

### S6 — HPS Bear Flag (Canonical)
- **provenance:** **pdf_canonical** · **style:** day · **timeframe:** 5-min · **direction:** SHORT (PDF slide's "go long" at the 80 touch is an obvious typo in a short setup — treat as short) · **automation:** semi_auto · **status:** backtesting
- **Entry:** slow AND medium stochastics holding **under 20**; price at/below the 20 EMA; short when 9-3 rotates UP to the 80 line. Single entry per setup.
- **Exit:** partial profits as 9-3 crosses back below 20; stop to breakeven on remainder (mirror of S5).
- **Instruments note:** on SPY/QQQ shorting is trivial (ETB, margin acct ≥ $2,000); alternatively express via inverse ETF (SQQQ) intraday-only. Guardian ETB check for individual stocks.

### S7 — HPS Bear Flag (Variant B — interview supplement)
- **provenance:** **interview_supplement** (A/B test against S6; promote whichever the backtest prefers) · **style:** day · **timeframe:** 5-min · **automation:** semi_auto · **status:** backtesting
- **Entry regime:** price trending DOWN under the **200 EMA**; 60-10 stochastic **embedded below 20–30** ("banging around the bottom"); short **every** 9-3 rotation up above 80. **Re-entry enabled:** `{allowed:true, max_entries: N (default 3), cooldown_bars: config}`.
- **Exit:** **rotation-complete exit** — cover as soon as the 9-3 completes its fast rotation back down (momentum-cycle exit, not price-based). "Must take it off" is a hard rule (perfect for automation).

### S8 — HPS Swing Divergence
- **provenance:** pdf_canonical (PDF shows the same divergence on 60-min SP-500 and daily Verizon charts) · **style:** swing · **timeframe:** 60-min and daily · **instruments:** SPY/QQQ/large caps · **automation:** semi_auto · **status:** backtesting
- **Entry:** same Lane/quad divergence logic as S4 on 60-min/daily bars; divergence strongest when aligned with falling-wedge/lower-channel context (human confirm) and daily oversold.
- **Exit:** pattern-low stop; scale out on stochastic rotation; wider trailing; holds overnight/days.
- **Why it matters:** easiest strategy for our stack — daily bars are free and perfect quality, latency irrelevant, no pre-market complexity. Good early live candidate.

---

## 8. Selection Frameworks (2 — these gate strategies, they are not strategies)

### F-A — SMB "Stocks in Play" Triple Filter (applies to all stock day-trade strategies)
1. **Reason (What changed?):** fresh information forcing revaluation — earnings, guidance, FDA, M&A, lawsuit, multi-month level break. Weak reasons auto-reject ("moved yesterday", "looks interesting"). **One-sentence test enforced by Hermes** as a hard gate.
2. **Participation (Who cares?):** gap ≥3%, ADV >2M, ATR >$1, pre-market volume tiers (10/20/50/100% of ADV). No polka-dot charts.
3. **Structure (Where's the battlefield?):** validate daily → hourly → 15-min before intraday; clean level required; reject mid-range gaps (distance-to-level in ATRs).
Plus: **cap focus list at 3**; pass anything not matching a playbook setup; sector strength alignment; alerts instead of watching; **11:30 ET midday re-scan** for names exceeding full-day ADV/ATR.

### F-B — Kurisko Trend-First Rule (applies to all HPS strategies)
**First step before any HPS trade: determine the prevailing trend — the trend determines the exit strategy.** Only trade the confirmed signal (the "Super Signal" = quad rotation + playbook setup together; skip partial setups). In a confirmed uptrend (rising channel / price above 200 EMA/VWAP): give the trade room, wider trailing stop, time exits with the 60-10 rotation. In a downtrend (below 200 EMA, 60-10 weak/under 30): take profits fast as the 9-3 reaches its extreme, move to breakeven immediately. Divergences cluster at active times (US open, ~03:00 ET European open) — scheduling hint for detectors.

---

## 9. Engine Vocabulary (Python service plugins — build in Phase 7, referenced from Phase 2 onward)

### Detectors / indicators (21)
1. EMA 20/50/200 (any period, param)
2. RSI (period param)
3. VWAP (session-anchored)
4. Stochastic bands — four parameter sets **9-3, 14-3, 40-4, 60-10** (all params configurable)
5. Quad-under-20 / quad-over-80 detection (all four bands beyond threshold)
6. Stochastic divergence (swing-point comparison: price lower-low vs stoch higher-low, and inverse)
7. **Embedded stochastic** (value beyond threshold for ≥ N consecutive bars; threshold + N params) — doubles as a general trend-strength filter
8. ATR (period param)
9. Average daily volume (ADV, lookback param)
10. Relative volume — cumulative pre-market vs 14-day same-window average, **same-feed baseline (IEX vs IEX)**
11. Gap % (vs prior close)
12. Float (Finviz Elite primary, FMP fallback)
13. Opening range (first N minutes high/low)
14. N-bar high/low (rolling)
15. Prior-day high/low/close
16. Pre-market high/low
17. 52-week-high proximity + multi-month base/consolidation breakout
18. Distance-to-nearest-major-level in ATR units ("no man's land" filter)
19. Double top / double bottom (swing-point based)
20. Sector relative strength (sector ETF performance ranking)
21. Flagpole steepness (move off EMA vs rolling slope baseline — for S5)
22. **CVD — cumulative volume delta** (three-tier, see §27)
23. **Volume profile — POC / VAH / VAL** (see §27)

### Exit plugins (9)
1. Fixed bracket / OCO (stop-loss + take-profit) — attached at/after 09:30 only
2. Trailing % stop
3. **N-bar trailing stop** (user strategy 3: initial stop = lowest low of past N bars; ratchets up on new N-bar high; never down; N param, default 2)
4. **Pattern-low stop** (1–2 ticks under divergence/pattern candle; ticks param)
5. Stochastic-rotation partial exit (partial at 9-3 crossing 80 long / 20 short)
6. Scale-out + breakeven stop (move stop to entry after first partial)
7. Time-based exit (flat by HH:MM; also "intraday-only" enforcement for leveraged ETFs)
8. **Synthetic pre-market stop** — websocket listener (Alpaca trade/quote streams) in the Python service; reacts < 1s; fires closing LIMIT order on breach. Required because real stops don't trigger pre-market
9. **Rotation-complete exit** (S7: full exit when the fast stochastic completes its rotation back)

Chart-pattern items that are NOT codified (deliberately): hand-drawn trendlines, channels, wedges, "coils" — these are the human step in semi_auto strategies. The signal card shows computed evidence; the user supplies visual confirmation.

---

## 10. AI Layer — Hermes Hybrid

**Local tier (Ollama, Hermes 3 8B):** high-volume, low-stakes → strict-JSON summarization of Reddit threads, Yahoo headlines, Polymarket blurbs into `news_sentiment` rows. Free, private.
**Hosted tier (OpenRouter, Hermes 3 70B or 405B):** low-volume, high-stakes judgment. One n8n HTTP node. Cents/day.

**Global prompt rules:** Hermes receives only computed values (never raw bars to eyeball, never math to do). All decision outputs are strict JSON with a stated schema. Hermes proposals NEVER auto-apply — the backtester validates first. Prompts are stored as versioned templates in the DB, referenced by `prompt_template_id`.

### The 7 prompt templates
1. **Sentiment condenser** (local 8B) → `{catalyst_type, sentiment_score, key_facts[], one_sentence_reason}` per source per symbol.
2. **Morning trade plan** (hosted) — inputs: scan metrics, computed indicators, catalyst summaries, last N relevant journal lessons. Output per candidate: `{decision, entry, stop, target, trail_pct, size, confidence, reasoning}` → `signals`.
3. **Watchlist briefing** (hosted) — S2's ranked table + market context.
4. **One-sentence in-play test** (hosted, cheap call or folded into #2) — pass/reject gate per F-A.
5. **Per-trade autopsy** (hosted) — see spec below.
6. **Weekly batch autopsy → hypotheses** (hosted) — reads the week's journal + analytics module output; writes lessons + testable hypotheses ("rel-vol 7× would have cut 3 of 4 losers") → each hypothesis becomes a backtest job, never a live change.
7. **Guardian briefing** (hosted) — plain-language state report (see §11).

Plus a **monthly rules-freshness job**: Hermes + web search checks whether the Guardian rules file is stale (broker rule changes, e.g. exactly how the PDT retirement would have been caught) → writes to `compliance_events`.

### AI Trade Autopsy — full spec (template 5)
- **Auto-filled inputs (no manual pasting, no screenshots):** setup details from `signals`/`orders`/`journal` (planned entry/stop/exit vs actual), the strategy config's playbook rules, actual OHLCV bars around the trade (text-serialized, small window), computed indicator values at entry/exit, day context (trade # that day, running P&L), and the user's `subjective_note` (e.g. "felt it rolling over").
- **Questions answered (from the user's own template):** (1) Did the entry meet the playbook rules — was it clean? (2) Was the exit plan followed; what was violated? (3) What behavioral pattern is showing (tilt detection: revenge trading, early exits after consecutive losses, oversizing, moving stops)? (4) What one thing to improve? (5) Coach-style feedback paragraph.
- **Output:** structured JSON (rule_compliance per rule, violations[], behavioral_flags[], focus_area, coach_note) written to `journal.autopsy`; weekly batch aggregates these.
- Text-only model: we feed real bar data, which is strictly more reliable than chart screenshots.

---

## 11. Guardian Agent (cost/compliance protector)

**Design rule: rules in code, explanations from Hermes.**

### Layer 1 — Pre-trade gate (code, blocking, runs BEFORE any order; result attached to the signal card so perceived latency ≈ 0; lightweight re-validation ~100ms on Deploy tap)
Checks: order value vs current **`buying_power`** (with safety buffer — never flirt with an intraday margin call); live bid/ask spread vs ceiling (default: block > 2%); position size vs risk-per-trade rule; max concurrent positions; daily loss limit (default 2R → block further entries); self-cross/wash check vs own open orders; shortable + easy-to-borrow when direction=short. Output: green (proceed) / yellow (warn on confirm screen) / red (block + reason) → logged to `compliance_events`.

### Layer 2 — Always-on ledger (code, background, on every fill)
Log to `guardian_ledger`: actual SEC/TAF fees (Alpaca activities endpoint); realized spread cost (fill vs quote midpoint at order time); realized P&L; running tax provision (**28% of net realized gains — PT flat CGT; display-only, "confirm with an accountant" disclaimer**); FX/funding cost events.

### Layer 3 — On-demand briefing button (Hermes)
Gathers: equity, buying power, open positions, ledger totals, YTD tax provision, recent warnings, margin-call events, rules knowledge file → plain-language report + conversational follow-ups. Hard boundary in prompt: flags considerations and computes provisions; tax-filing specifics → "ask an accountant"; it is a monitor, not an advisor.

Also: global **kill switch** flag (in DB) that every execution workflow checks first; monthly rules-freshness job (§10).

---

## 12. Execution Engine Rules (Phase 5 — the careful one)

1. **Human-in-the-loop:** full_auto strategies auto-generate signals but still require the Deploy tap (auto-approve is a config flag OFF by default). semi_auto strategies additionally expect visual confirmation.
2. **Pre-market entries (04:00–09:30 ET):** LIMIT only, `extended_hours=true`, `time_in_force=day`. NO brackets, NO stops, NO market orders (broker rejects/ignores them — see §3.4).
3. **Pre-market protection = synthetic stop:** Python websocket listener subscribes to trades/quotes for open positions; on stop breach fires a closing LIMIT order in < 1s. (Not a 30–60s n8n poll — too slow for low-floats.)
4. **At 09:30:** attach real OCO (take-profit + stop-loss) to open positions; optionally replace with trailing stop later per exit_logic.
5. **Order lifecycle:** every submit/replace/cancel/fill → `orders`; fills roll up to `journal` at close of position; Telegram alert on every signal, fill, block, and error.
6. **Latency budget:** Deploy tap → order at Alpaca ≤ 2s (gate pre-computed; re-validation only). Entry latency is a non-issue for plan-based limit orders; engineering care goes to the EXIT monitor.
7. **Wash-trade guard:** cancel/skip if a conflicting own resting order exists (gate check).
8. **Kill switch** checked at the top of every execution workflow.

---

## 13. Backtest Engine (Phase 7 — deterministic, scanner-aware)

- **Runtime:** Python container (FastAPI), vectorbt or backtesting.py core, called by n8n with `{strategy_config, date_range, fill_model}`; writes metrics to `backtests`.
- **Scanner replay (the differentiator):** for each historical day, reconstruct the UNIVERSE the screener would have produced (not single-symbol curve fitting — this is why TradingView/Pine was rejected: single-chart testing cannot test a scanner strategy and is survivorship-biased by construction).
- **Data:** Alpaca historical bars — SIP (full consolidated) is free historically; deep 1-min history cached as parquet on the home server.
- **Four mandatory trap countermeasures:**
  1. **Survivorship bias:** verify Alpaca's coverage of delisted symbols (critical for low-float gappers); flag universes that look "too clean"; report coverage stats with every S1 backtest.
  2. **Feed mismatch:** live scanner uses IEX volume, history is SIP — maintain a calibration factor logged during the paper month; never compare a live 5× threshold to a SIP-derived 5× directly.
  3. **Fill realism:** explicit fill model — spread cost, partial fills on limits, no mid-bar magic; parameters in `fill_model`, reported with results.
  4. **Overfitting guards:** minimum sample sizes before any conclusion; Hermes hypotheses are validated here before any config promotion; walk-forward split for parameter tuning.
- **Outputs:** trades, win rate, expectancy, avg win/loss, profit factor, max drawdown, per-year and per-regime breakdowns; A/B comparison mode (e.g. S6 vs S7 bear flags).
- **Promotion workflow:** draft → backtesting (must pass `promotion_criteria`, §20) → paper (≥ 4 weeks) → live; every promotion is a status flip backed by a `backtests` row and (for hypothesis-driven changes) a one-tap human approval. **Win rate is NEVER the gate** — it is displayed as information only.

---

## 14. Analytics Module (Phase 6 — feeds autopsies and the strategy library UI)

Pandas module reading `journal` (+ optional CSV importer for external-broker history; columns: Symbol, Entry/Exit Date+Time+Price, Shares, P&L, Setup Type):
- Overall: total trades, winners/losers, win rate, avg win, avg loss, expectancy, largest win/loss, total & avg P&L.
- Breakdowns: by setup type / strategy_id; by time-of-day blocks (pre-market, 9:30–10:30, 10:30–11:30, 11:30–12:30, 12:30–1:30, 1:30–2:30, 2:30–4:00); by day-of-week; by ticker; by market regime.
- Pattern detection: best/worst hour, best/worst setup, consistently losing periods → flags.
- Output: tables + written summary + recommendations → stored, and passed as input to the weekly autopsy (template 6).
- This module IS the data layer of the future "strategy library / history" UI section (win rate, timeframe, ticker, P&L per strategy — all simple joins on `strategy_id`).

---

## 15. Implementation Phases (backend first; UI last; ~6–8 weeks part-time to paper-ready)

### Phase 0 — Foundations (2–3 days)
Accounts: Alpaca (paper), Supabase, OpenRouter, Reddit API app, Finviz Elite, Telegram bot. All keys → n8n credentials store / Supabase secrets (never in workflow JSON, never in frontend). **Deliverables:** complete SQL schema (9 tables, §5) + RLS; seed INSERTs: 8 strategy configs, 2 framework definitions, 7 prompt templates, plugin registry; kill-switch flag.

### Phase 1 — Market data layer (3–5 days)
Supabase Edge Function proxy → Alpaca data (quotes + bars; 1–5s cache) — the future frontend's ONLY data door. Verify in practice: pre-market 1-min IEX bars return volume for representative small caps. Nightly job: compute/maintain 14-day average pre-market volume per relevant ticker **from the IEX feed**. Start the IEX↔SIP calibration log.

### Phase 2 — Scanner workflow (3–4 days)
n8n cron 08:00 ET: pull Finviz Elite CSV → run EVERY `active` strategy's screener filters from config (one pull serves all) → per survivor compute rel-vol, ATR, ADV, levels (via Python service) → rank → write `scans`. Config-driven from day one (no hardcoded filters). Second cron 11:30 ET: SMB midday re-scan.

### Phase 3 — Catalyst & sentiment (4–5 days)
For top candidates only (cap 5): Reddit API search, Yahoo RSS, Polymarket where relevant; residual Firecrawl scraping behind Loop + Wait 2–3s. Local 8B condenses to structured JSON → `news_sentiment`.

### Phase 4 — Hermes decision node (2–3 days)
Templates 2/3/4 wired: strict-JSON plans → `signals`; watchlist briefing; one-sentence gate. Include last-N journal lessons in context.

### Phase 5 — Execution engine (1 week; the careful one)
Deploy webhook (authenticated) → Guardian gate (Layer 1) → order submission per §12. Python websocket exit monitor (synthetic pre-market stop, sub-second) — built on the official **alpaca-py** SDK streaming client (§21). 09:30 OCO attachment job. Order/fill logging. Telegram alerts. Kill switch. Auto-approve flag exists but ships OFF.

### Phase 6 — Journal, analytics & feedback loop (4–5 days)
EOD cron: closed positions + Alpaca activities (fees) → `journal` + `guardian_ledger` (Layer 2). Analytics module (§14). Weekly autopsy cron (templates 5 batch + 6) → rows in `hypotheses` → automatic backtest jobs. **Degradation trigger:** if a paper/live strategy's rolling performance falls below its backtest baseline (drawdown or expectancy limit), the analytics module immediately fires a "review this strategy" hypothesis request — the defensive twin of the weekly improvement loop.

### Phase 6.5 — Guardian briefing (2–3 days)
Layer 3 button/report workflow + monthly rules-freshness job.

### Phase 7 — Backtest engine (1 week)
FastAPI service per §13 with the four trap countermeasures; seed the full detector/exit plugin registry (§9); run S1–S8 baselines; A/B S6 vs S7; calibrate S5's optional ATR target.

### Phase 8 — Hardening + paper month (≥ 4 weeks calendar)
Retry logic, rate-limit guards, error alerting, backup/restore of Supabase + parquet store. **Onboarding flow** (first-run: enter API keys, choose paper/live, set risk defaults, pick which strategies start active, verify broker + data connectivity). **Failure-handling policy** (§33). Full pipeline runs daily on paper. Expose n8n via Cloudflare Tunnel / Tailscale Funnel when the UI starts.

### Phase 9 — UI (after backend proves itself; separate spec)
Wishlist to design against: dashboard, small-chart market overview, central heat map, watchlist, alerts row, smart filters/categories, company insights/financials/analyst ratings, news sentiment panel, earnings summaries, VIX display (Twelve Data backup key), bid/ask spread display, EMA 20/50/200 crossover badges, signal cards with Guardian pre-check + Deploy/Cancel, strategy library section (add new strategies as configs; history per strategy: win rate, timeframe, tickers, P&L — reads §14), **Strategy Lab** (the `hypotheses` UI: suggested changes, why, backtest verdict, one-tap approve/reject — §20), trade journal + autopsy viewer, backtest comparison view, Guardian button, and eight Personal Research sections in this sidebar order: Portfolio risk, Analyst (E2), **Long-term analysis** (E5, §36), **Fundamentals Lab** (§37), Polymarket, Researcher (E4), **Ask Hermes** (§26), **Walkthrough** (§25). Build against the reference mockup file (§32); add a **Manual order** button to the top bar and a **Position management** view (§28). Frontend NEVER holds keys; reads via Edge proxy + Supabase; actions via authenticated n8n webhooks.

---

## 16. Security Requirements
- Keys only in n8n credentials / Supabase secrets. Frontend gets zero keys (Edge proxy pattern).
- n8n webhooks: secret header/HMAC + Supabase JWT check; exposed only via Cloudflare Tunnel or Tailscale Funnel.
- Paper and live Alpaca keys are separate credentials; live keys enter the system only at go-live.
- Supabase RLS on all tables; single authenticated owner.
- Kill switch halts all execution workflows instantly.
- Alerting on every anomaly: duplicated orders, missing fills, connectivity loss (automated-trading table stakes).

## 17. Acceptance Criteria (gate to real money)
1. All 8 strategies backtested with the four countermeasures active; results recorded in `backtests` (including S6-vs-S7 verdict and IEX/SIP calibration factor).
2. ≥ 4 consecutive weeks of full paper operation: daily scans, signals, ≥ 20 paper trades journaled, autopsies generated, zero unhandled execution errors.
3. Guardian verified: at least one forced red-block test per check; ledger reconciles against Alpaca activities to the cent; tax provision accumulating.
4. Latency verified: Deploy→order ≤ 2s; synthetic stop reaction < 1s in live pre-market conditions.
5. Only then: fund modestly, W-8BEN filed, first live strategy = single config (S8 swing or S3 recommended — best data quality), sizes small.

## 18. Explicitly Deferred / Out of Scope (v1)
Options trading; futures (not offered by Alpaca); Level 2 order-book anything (unavailable for equities); 1-minute scalping (needs paid SIP + tighter stack); Twelve Data as core; multi-user; auto-approve ON; leveraged ETFs beyond intraday tools.

## 19. Compliance & Disclaimers (bake into the app)
- The system provides engineering + decision support; it is not investment, legal, or tax advice.
- Tax provision = display estimate (PT flat 28% CGT); user must confirm with an accountant before live trading.
- W-8BEN with Alpaca (non-US person).
- Low-float pre-market trading risks surfaced in-app: wide spreads, halts, no guaranteed stops pre-market (synthetic stop is best-effort).
- Claimed win rates from any source (e.g. "95%") are never displayed as fact; only backtest/paper/live measured numbers are shown, with sample sizes.


---

## 20. Hypotheses System & Strategy Lab (the formalized self-improvement loop)

**How self-improvement actually works (in-context learning — the model's weights never change; the intelligence accumulates in the database):**
Journal + autopsies record experience → weekly Hermes autopsy *proposes* hypotheses → the backtester *grades* them against years of data → validated changes wait for the user's one-tap approval → promoted changes alter tomorrow's configs, and confirmed lessons are injected into future prompts. **Hermes proposes, the backtester disposes, the database remembers.** Nothing is ever auto-applied to paper/live strategies.

**`hypotheses` lifecycle:** `proposed → backtesting → validated | rejected → promoted`. Sources: weekly autopsy cron, the user's manual ideas, or the degradation trigger (Phase 6). Every row stores the proposed config diff, the reasoning + evidence cited, and the linked backtest verdict. Rejected hypotheses remain visible forever (anti-re-suggestion memory).

**`promotion_criteria` (global config block, defaults — user-tunable without code):**
- Positive expectancy after modeled fees + spread — **non-negotiable**
- Profit factor ≥ 1.5
- Minimum sample size: ≥ 100 backtest trades for day strategies (lower bound configurable for swing)
- Max drawdown within user tolerance (config)
- Improvement hypotheses must **beat the current config in an A/B run**, not merely be profitable in isolation
- Win rate: displayed as information, never used as the gate (45% win rate with 3:1 winners beats 85% with rare huge losers)

**Early-life guard:** with few trades the loop is deliberately conservative — minimum sample sizes prevent "learning" from noise. The system gets smarter over months, not days; that is a feature.

**Strategy Lab (UI, Phase 9):** list of suggestions with what/why/source/backtest result/status + approve/reject buttons.

---

## 21. Alpaca Connectivity & SDK Policy (decided — do not substitute)

Three touchpoints, three deliberate choices:
1. **n8n → Alpaca Trading API:** plain REST via n8n HTTP Request nodes. Auth = `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers from n8n's credentials vault. Paper vs live separated by base URL (`paper-api.alpaca.markets` vs `api.alpaca.markets`) with different key pairs; live keys enter the system only at go-live. No SDK here — HTTP nodes are transparent and debuggable.
2. **Python service → Alpaca:** official **`alpaca-py`** SDK — WebSocket streaming client (synthetic-stop exit monitor, sub-second), historical bars for the backtester, retries, typed models.
3. **Supabase Edge Function → Alpaca Market Data:** plain `fetch()` (Deno/TS), key server-side, 1–5s cache. Skip the Node SDK — it is overkill for two endpoints.

**Explicitly rejected: Zapier** (it is a hosted n8n competitor, not an SDK: per-task billing that would cost $30–75+/mo for what self-hosted n8n does free, a thin Alpaca integration that cannot express extended-hours/OCO logic, an extra latency hop, and keys in third-party cloud). **Frontend later:** `supabase-js` only. Alpaca's MCP server: dev-time toy against paper only, never in the production path.

---

## 22. Extension Modules (E1–E4) — INDEPENDENT, ON-DEMAND, NOT part of the base trading workflow

**Architecture rule (hub-and-spoke, no multi-agent framework):** each module = an n8n workflow (button-triggered or lightly scheduled) + its own tables + a Hermes prompt template. Modules NEVER talk to each other and NEVER sit inside the trading pipeline. All communication is one-directional through the database, with staleness rules. Exactly two sanctioned read-points by the core: (a) the morning trade plan MAY cite a recent `analyst_reports` row for a candidate (ignore if older than 7 days); (b) the Guardian MAY read the latest `portfolio_risk_reports` row for concentration warnings. Nothing else.

**Build order and timing: ONLY after the core (Phases 0–8) runs its paper month.** Order: E1 → E2 → E3 → E4. The #1 project risk is scope: one working trading loop beats six half-built agents.

### E1 — Portfolio Risk Assessment (build first; ~90% deterministic math)
- Inputs: Alpaca positions (auto) + `external_holdings` table (manually entered outside assets). Optional params: risk tolerance, benchmark, horizon.
- Python service computes: portfolio value/weights, volatility, beta, Sharpe, Sortino, max drawdown, VaR 95/99 per holding, correlation matrix + average pairwise correlation, diversification ratio, HHI concentration, marginal risk contribution, upside/downside capture; five stress scenarios (−10% equities; +100bps rates; top-sector −20%; VIX +50%; −30% correlated shock); MPT optimization via **PyPortfolioOpt** with expected volatility reduction.
- Hermes writes: client-ready summary, top-3 risk holdings, diversification grade A–F, trim/hold/accumulate suggestions, projected volatility bands. → `portfolio_risk_reports`.
- Integrations: Guardian concentration read; weekly autopsy may cite it.

### E2 — Financial Analyst (single-ticker deep dive)
- **Ported with the core rule enforced: code computes, Hermes concludes.** The user's original prompt is preserved verbatim in the Prompt Library (§23) as the coverage specification, but the build splits it: FMP free tier (fundamentals, ratios, growth, margins, insider transactions) + Alpaca (price history) → Python computes DCF (explicit assumptions: growth, WACC, terminal value), technicals (50/200 MA, RSI, MACD, Bollinger, OBV), and the weighted composite score (weights in config; defaults: Fundamentals 40% / Technicals 20% / Institutional-Insider 10% / Sector-Macro 15% / Sentiment 15%) → Hermes writes classification (Undervalued ≥70 / Fairly priced 40–69 / Overpriced <40), reasoning, risks.
- **Corrections vs the original prompt:** no LLM math anywhere; the "6/12-month price forecast" is reframed as DCF sensitivity scenario ranges (bear/base/bull), clearly labeled as scenarios, never predictions. Institutional-holdings data is patchy on free tiers — score runs degraded with a data-quality flag (future option: FMP paid ~$15–20/mo).
- Output → `analyst_reports` (ticker, date, sub-scores, composite, classification, report text).

### E3 — Polymarket Agent (paper-only subsystem, ported onto OUR rails — do NOT build the PDFs' separate Next.js/SQLite/Vercel app)
- **Hard safety inherited from the source docs and non-negotiable:** paper trading only; no private keys ever stored or requested; no transaction signing; no live execution (live Polymarket = USDC on Polygon, a different rail we do not run); if an API fails, show the real error — never fake data; demo data must be labeled.
- **Ported concepts (from the two PDFs, mapped to our stack):** leaderboard scan (top wallets, 30-day lookback) → wallet scoring: ROI, consistency, copyability, category edge, liquidity quality, entry timing, resolved-trade count, and the **one-hit-wonder penalty** (penalize profit concentrated in one lucky trade, illiquid habits, uncopyable entry timing, wide typical spreads) → trade monitor detects new trades from tracked wallets → trade scoring (wallet score, entry-vs-current price, movement since entry, spread, liquidity, time to resolution, thesis clarity) → decision labels **paper_copy | watchlist | skip** → simulated positions $5–20 sized by confidence → hourly PnL snapshots → outcome reviews (1h/6h/24h + resolution) → **benchmark: bot-filtered vs blind leaderboard copy vs watchlist vs skipped** (tracks missed winners / avoided losers — this benchmark is the whole point: it measures whether filtering adds edge) → EOD Telegram report (paper PnL, best/worst trade & wallet, rule changes, top lesson, did filtering beat blind copy).
- **Rule self-updates:** allowed autonomously INSIDE the paper sandbox only (per the source spec), but every change is versioned with reason/evidence/before/after — implemented as `hypotheses`-pattern rows in `pm_rules`. Any hypothetical future live mode inherits the full human-approval gate + Guardian.
- Five sub-strategies from the cheat-sheet PDF become five `pm_` strategy configs (strategies-as-data, same as core): smart-money copy, crypto short-window (needs external BTC/ETH price feed), weather (official sources only: NOAA/NWS/ECMWF-class; social posts are never evidence), arbitrage/correlated (skeptical by default; edge must survive spread+fees+slippage+resolution-criteria match), event-driven news (fresh-vs-priced-in classification).
- Data: Polymarket public APIs primary; Bullpen CLI as optional adapter. Tables: `pm_wallets`, `pm_observed_trades`, `pm_market_snapshots`, `pm_decisions`, `pm_paper_trades`, `pm_outcome_reviews`, `pm_rules` (schema fields per the source PDF's models, adapted to Postgres).
- Cross-feed: Polymarket probabilities continue feeding the stock pipeline as context (already in core); the news-agent concept feeds the Polymarket side.
- Honest edge note for the builder's README: leaderboard copy-trading is crowded; late entry, spread, and illiquidity are the killers — the benchmark decides if anything survives.

### E4 — Researcher (alternative investments discovery; build last — fuzziest)
- **New infrastructure required:** web search for n8n — self-hosted **SearXNG** (preferred, fits Docker) or Brave Search API free tier.
- Flow: theme/geo/type/horizon input → search + discovery of 10–20 candidates with sub-segments (e.g., AI: chips / infra / SaaS / robotics / data-center) → FMP+Alpaca quantitative comparison (valuation, growth, momentum, beta, drawdown, Sharpe where available) → Python normalizes 0–100 scores → Hermes writes ranked top-5 with entry zones, horizons, top-3 rationale, risks, and a diversification mix suggestion (weights config; defaults: Valuation 30 / Growth 25 / Momentum 15 / Sentiment 15 / Macro 15). → `research_reports`.
- Positioning: a research assistant that saves hours, not an oracle; output quality depends on search quality.

**Extension tables total:** `analyst_reports`, `research_reports`, `external_holdings`, `portfolio_risk_reports`, + 7 `pm_*` tables → system total ≈ 21 tables. **Hermes templates grow 7 → 12** (adds: risk summary, analyst conclusion, researcher report, PM wallet/trade scoring rationale, PM EOD report).

---

## 23. Prompt & Strategy Library — MANDATORY instructions to the builder agent

The builder agent MUST create a dedicated repository folder that collects EVERY prompt and strategy definition in one place, differentiated by type and creator. These files are the human-readable source of truth; the database seed rows (§5) are generated FROM them and reference them via `prompt_template_id` / config `notes`. Keep user-provided originals verbatim; adapted/system versions live alongside, clearly marked. Version every change (git history is sufficient).

```
/prompt-library/
  README.md                        ← index of everything below, with one-line description each
  /strategies/                     ← the 8 trading setups (NAME + CREATOR in every filename)
    S1_low-float-gapper_OWN.md
    S2_premarket-watchlist-briefing_OWN.md
    S3_stocks-in-play_SMB-CAPITAL.md
    S4_hps-quad-divergence_JOHN-KURISKO.md
    S5_hps-2020-bull-flag_JOHN-KURISKO.md
    S6_hps-bear-flag-canonical_JOHN-KURISKO.md
    S7_hps-bear-flag-variantB_JOHN-KURISKO-interview.md
    S8_hps-swing-divergence_JOHN-KURISKO.md
  /frameworks/                     ← stock-SELECTION strategies (how to choose stocks — not entries)
    FA_stocks-in-play-triple-filter_SMB-CAPITAL.md      (Reason / Participation / Structure + one-sentence test)
    FB_trend-first-rule_JOHN-KURISKO.md                  (trend determines exit; only trade the confirmed signal)
  /hermes-core/                    ← the 7 core templates (§10), numbered 01–07
  /hermes-extensions/              ← templates 08–12 (§22)
  /extension-modules/              ← the user's ORIGINAL module prompts, verbatim, + the adapted build spec next to each
    E1_portfolio-risk_original.md / E1_adapted.md
    E2_financial-analyst_original.md / E2_adapted.md
    E3_polymarket_source-notes.md   (key rules distilled from the two source PDFs + safety rules)
    E4_researcher_original.md / E4_adapted.md
  /tests/                          ← test specifications given by the user
    T1_2bar-trailing-stop_spec.md   (user's strategy-3 exit logic, translated from Pine concept)
    T2_ai-trade-autopsy_template.md (playbook-vs-actual + tilt detection questions, verbatim + adapted)
    T3_performance-analysis_spec.md (user's journal/statistics requirements)
```

Each strategy file must contain: name, creator/actor, provenance (pdf_canonical | interview_supplement | own | derived), style (scalp→ported/day/swing), timeframe, automation mode, full entry conditions, full exit logic, risk block, re-entry block, instruments, known caveats, and the marketing-claims disclaimer where relevant (e.g., "95% win rate" claims are recorded but never displayed as fact). Content for every file comes from §7, §8, §10, §14, §20, §22 of this document.

---

## 24. Changelog v1.0 → v1.1
1. Added `hypotheses` table (core table #10), full lifecycle, `promotion_criteria` basket (expectancy/profit-factor/sample-size/drawdown/A-B — win rate never gates), degradation trigger in Phase 6, Strategy Lab in UI wishlist.
2. Added §21 connectivity: n8n = HTTP nodes; Python = `alpaca-py`; Edge Functions = fetch; Zapier explicitly rejected.
3. Added §22 Extension Modules E1–E4: independent, on-demand, hub-and-spoke through the database, built ONLY after the core paper month, in order E1→E2→E3→E4; two sanctioned core read-points (morning plan ↔ analyst_reports ≤7 days; Guardian ↔ portfolio_risk_reports).
4. Added §23 mandatory Prompt & Strategy Library folder with naming (strategy NAME + CREATOR), differentiation (entry strategies vs selection frameworks vs core/extension Hermes templates vs tests), verbatim-plus-adapted policy.
5. System totals now: ~21 tables, 12 Hermes templates, 21 detectors, 9 exit plugins, 8 strategies + 2 frameworks + 5 Polymarket paper sub-strategies.


---

## 25. Walkthrough Section (Personal Research — last position; UI phase, content written earlier)

Purpose: so the owner never forgets how the system works, and any third-party user can understand the app before using it. Content is sourced from this specification — no new research needed.

**Page structure (in order):**
1. **Overview** — what the app is in one screen: what it does for you, capability list (§ sections), the tools it uses (stack summary), and the honest limitations (paper-first, not financial advice, ~€40/mo, out-of-scope list from §18).
2. **Section cards** — one card per app section (all 10 Trading Core + all 6 Personal Research): *what it does · when to check it · what to do there*. Short, scannable.
3. **Workflow pages** — "Your day in the app" (open → Dashboard glance → Signals → visual confirm where required → Deploy/Cancel → evening journal note) and "The Sunday ritual" (Analytics → Strategy Lab approvals → Library promote/retire).
4. **How Hermes works** — proposes / engine proves / you approve; what Hermes never does (no math, no backtests, no autonomous changes).
5. **Four interactive SVG infographics** (built as SVG/React components in-repo — versioned, dark-mode aware, instant):
   - **Daily pipeline**: 08:00 scan → enrich → Hermes plans → Deploy tap → execution → journal
   - **Learning loop**: journal → hypothesis → backtest → approval → config update
   - **Strategy lifecycle**: draft → backtesting → paper → live → retired, showing where Library / Scanner / Strategy Lab each sit
   - **System architecture**: n8n (manager) · Supabase (shared brain) · Python (calculator) · Hermes (judge), with the two-speed split (live data path vs execution path)
   Diagrams are **clickable** — tapping a node jumps to that section or its walkthrough card.

**Access:** a "?" shortcut in every section header jumps to that section's card; the Overview page is shown on first login as onboarding.
**Explicitly NOT used:** NotebookLM or any external doc tool — no embeddable output, no API into the app. (Fine as a personal aid outside the product.) Same verdict for Obsidian: every job it could do is already owned by the Journal, Library, prompt-library folder, or Walkthrough — adding it would create a second source of truth.

---

## 26. Ask Hermes (Personal Research — conversational access)

An open chat with Hermes over the app's own state. Three uses: (a) **explain decisions** — "why was AMD skipped today?" → reads `scans`/`signals`/`news_sentiment` and answers; (b) **navigate the app** — "where do I see win rate by hour?" → points to Analytics (an interactive companion to the Walkthrough); (c) **query your own data** in plain language — "how did S1 do this month?".

**Hard rules:** strictly **read-only** — never executes trades, never edits configs, never approves hypotheses (all actions stay in their own sections behind their existing gates). Answers **only from database state**; if the data doesn't contain the answer, it says so rather than inventing one. Implementation: one Hermes template (#13) + one n8n workflow + read-only DB access. Cost: pennies per question (hosted tier).
**UI note (Phase 9):** also expose as a floating chat button on every screen so questions can be asked in context, not only from the sidebar section.

---

## 27. New Engine Detectors — CVD (#22) and Volume Profile (#23)

### Detector 22 — CVD (cumulative volume delta)
Three accuracy tiers, each labeled in the UI so the user always knows what they are looking at:
- **Tier 1 (free, default):** bar-based approximation — up-closing bar volume counts as buying, down-closing as selling, cumulated per session. Reasonable on liquid names (SPY/QQQ/large caps), unreliable on thin small caps.
- **Tier 2 (free, true classification, tiny sample):** classify IEX prints against IEX quotes (at ask = buy-initiated, at bid = sell-initiated). True method, ~2–3% of market volume — usually worse than Tier 1 in practice.
- **Tier 3 (after SIP upgrade):** full consolidated tape → institutional-grade CVD, backtestable and usable inside strategy rules.
**Status in v1: display-only** on signal-card charts as context for human confirmation. Never an entry rule until Tier 3 data exists AND the backtester proves it adds edge. Live imbalance readings are NOT shown on free data (too noisy to be honest).

### Detector 23 — Volume profile (POC / VAH / VAL)
Implement in-house (~30 lines: bin prices, distribute each bar's volume across its range, POC = heaviest bin, value area = 70% of volume around POC). Do **not** add pandas-ta as a dependency for one function; a library may be used as a dev-time cross-check only.
- **Prior-day and multi-day profiles: accurate** — computed from free Alpaca SIP historical bars (full market volume). These are the levels that matter most as today's support/resistance.
- **Developing intraday profile: approximate** — free live feed is IEX-only; label it as such.
Joins the levels family (prior-day H/L/C, pre-market H/L, 52-week levels), feeds the SMB structure / "no man's land" filter with sharper battlefield lines, and draws on signal-card charts. Usable as a config filter; promotion to entry rule follows the normal backtest gate.

---

## 28. Manual Order Entry & Position Management (core trading, not everything flows from a signal)

**Manual order ticket** — a top-bar button opening a simple order form (symbol, side, qty/size, order type, limit price, TIF, extended-hours flag). **Passes the full Guardian Layer-1 gate** exactly like a signal-driven order (buying power, spread ceiling, risk sizing, daily loss limit, wash-trade, shortable/ETB) and logs to `orders` + `journal` with `source = manual` so analytics can separate manual from systematic performance.

**Position management view** — for open positions: adjust stop, take partial profits, close now, or convert to trailing. All changes route through the same execution rules (§12: pre-market = limit only; real stops only after 09:30) and are logged. The synthetic websocket monitor must reload its parameters when a stop is edited.

---

## 29. Economic Calendar & Macro Context

A lightweight macro layer, near-zero cost:
- **Event calendar:** FOMC, CPI, NFP and similar high-impact dates ingested from a free calendar feed into an `economic_events` table.
- **In the morning plan:** Hermes receives today's events and reflects them in the plan ("FOMC 2:00 PM — reduced conviction, tighter sizing").
- **In Guardian:** yellow-flag entries close to a scheduled release; configurable blackout window.
- **In backtests:** tag historical days by event type so strategies can be evaluated separately on high-volatility (event) days vs quiet days (§30).
- **Fed odds:** sourced from **Polymarket** (already integrated) rather than CME — same signal, no new dependency. CME FedWatch remains a manual reference only; no free API.
- **UI:** small event badge on the dashboard (as in the reference mockup).

---

## 30. Order-Flow Research Track (OPTIONAL — post-paper-month, research only)

**Purpose:** find out whether order-flow data would improve the existing strategies **before** paying for live data. Nothing here is required for v1, and no live order-flow feature ships until the engine proves edge.

**Method:**
1. Use **Databento free credits** ($125) with the **slice method** — pull one or two specific historical weeks, not years — to develop and debug detectors offline.
2. Build order-flow detectors (true bid/ask CVD, depth/liquidity levels, deep-trade logic from MBO) and run everything **locally** against downloaded files; do not re-hit the API for each backtest iteration.
3. Test primarily as **filters on existing strategies** (A/B: S4 as-is vs S4 + CVD confirmation), not as standalone scalping systems. This is a normal `hypotheses` row + backtest job; Hermes reads the verdict, the engine produces it.
4. Order-flow strategies may exist in the Library with status `backtesting` and a data-source tag (`requires_databento_historical`); they simply never graduate to the scanner while live data is unavailable.

**Microstructure realism requirements (mandatory for this track — otherwise results are fake):**
- **Slippage buffer:** minimum 1 tick against you on market-style fills; spread cost always modeled.
- **Queue position rule:** a passive limit at a level counts as filled ONLY if volume traded *through* that level exceeds a conservative queue estimate for your size. Never assume a fill on touch — this asymmetry (unfilled winners, filled losers) is what turns fake-profitable backtests negative.
- **Fees always on:** SEC/TAF, even though commissions are $0.
- **Vectorized processing:** MBO data is millions of rows per minute — use **Polars / NumPy**, never Python loops.
- **Regime-split testing:** evaluate separately on high-volatility event days (FOMC/CPI, tagged via §29) and quiet days; indicators that only work in one regime must say so.

**Explicitly NOT modeled, by design:** transatlantic latency (~80ms Lisbon↔NY). Our strategies decide on 5-min bars and execute pre-planned limit prices, so sub-second travel time does not change fills. **Any strategy whose edge requires winning a latency race is out of scope** — retail cannot beat colocated firms, and accommodating it would corrupt the architecture. Likewise out of scope: "execution edge / order-routing optimization" (Alpaca routes; we have no venue control). "Dynamic position sizing" is in scope but as a normal Strategy Lab hypothesis, not a new subsystem.

---

## 31. Order-Flow Upgrade Path (memo for the builder — no work now)

Documented so nobody re-litigates it later: live order-flow analytics (delta, imbalance, large-print alerts, real-time tape) become buildable **only** with the Alpaca Algo Trader Plus upgrade (~$99/mo, full consolidated SIP) — a one-line feed change (`feed=iex` → `feed=sip`) that also fixes live VWAP and volume-ratio precision. **Equity Level 2 depth is never available from Alpaca at any tier**; if depth is ever wanted it is either a Databento research pipeline (§30) or a human side-screen tool (IBKR/Webull TotalView, ~€5–25/mo), never app infrastructure. **TradingView Premium (~$59/mo) was evaluated and rejected**: it provides visualization only (footprint charts on Premium and above; not available on Free/Essential/Plus), has no public API, and cannot feed our pipeline — poor value against the SIP upgrade which yields backtestable data. TradingView **free** remains recommended for manual chart eyeballing outside the app.

---

## 32. UI Reference Mockup (ships with this spec)

A file `dashboard_mockup.html` accompanies this document. It is the **layout and grouping reference** for Phase 9 — not pixel-final design. What it fixes: two-group sidebar (Trading core / Personal research, in the documented order); top bar with ticker search, Manual order button, paper-mode and kill-switch badges; account row (equity, buying power, P&L today, tax provision); live chart with VWAP + volume profile; watchlist; **signal card as the visually dominant element** (strategy name, evidence, confidence, Guardian badge, plan, Deploy/Cancel/View chart); sector heatmap with economic-event badge; Strategy Lab peek. Design principle to preserve: the signal card is the loudest thing on screen because it is the only element requiring action, and Guardian status is never more than one glance away.

---

## 33. Onboarding & Failure Handling (Phase 8 hardening detail)

**Onboarding (first run):** guided setup — enter API keys (stored in n8n credentials / Supabase secrets, never client-side), choose paper mode (default; live requires explicit switch), set risk defaults (risk per trade, daily loss limit, max concurrent, spread ceiling), select which seed strategies start active, and run a connectivity self-test (broker reachable, data feed live, Telegram delivering, kill switch responsive).

**Failure policy (fail safe, never fake):**
- **Broker unreachable:** block new entries, alert immediately via Telegram, surface a red banner; open positions fall back to the synthetic monitor, and if that also cannot reach the broker, escalate the alert — never silently retry forever.
- **Data feed down / stale:** if quotes exceed a staleness threshold, mark the dashboard data stale and refuse to generate or deploy signals on stale prices.
- **Scanner or enrichment failure at 08:00:** produce a "pipeline incomplete" notice rather than partial signals presented as complete; log to `compliance_events`.
- **LLM/API failure:** show the real error and stop. **Never fabricate data or plans**; demo/sample data must be visibly labeled.
- **Post-incident:** every failure writes to `compliance_events` so the Guardian briefing and weekly review can surface recurring problems.

---

## 34. Changelog v1.1 → v1.2
1. **§25 Walkthrough** (Personal Research, last): Overview + per-section cards + workflow pages + how-Hermes-works + 4 clickable SVG infographics; "?" shortcuts everywhere; first-login onboarding. NotebookLM and Obsidian explicitly rejected as components, with reasons.
2. **§26 Ask Hermes**: read-only conversational access to decisions, navigation, and personal data; template #13; floating button in UI.
3. **§27 Detectors #22 CVD** (three tiers, display-only in v1) and **#23 Volume profile** (in-house; prior-day accurate via SIP history, intraday approximate via IEX).
4. **§28 Manual order entry + position management** (both pass the Guardian gate; `source=manual` tagged for analytics).
5. **§29 Economic calendar & macro context** (morning plan, Guardian blackout window, regime tagging for backtests; Fed odds from Polymarket).
6. **§30 Order-Flow Research Track** (optional, post-paper-month) with mandatory microstructure realism rules — slippage buffer, queue-position fill rule, fees, Polars/NumPy vectorization, regime-split testing; latency modeling and routing-edge explicitly out of scope.
7. **§31 Order-flow upgrade-path memo**; **§32 UI reference mockup file**; **§33 onboarding + failure-handling policy**.
8. Deferred/unchanged: options, futures, forex, 1-min scalping, live equity L2, multi-broker (commodity/index ETFs like GLD/USO/XLE are already usable today as ordinary config symbols), Long-Term Investment Analysis module (dropped by owner decision to keep scope clean).
9. Totals now: ~22 tables (adds `economic_events`), 13 Hermes templates, **23 detectors**, 9 exit plugins, 8 strategies + 2 frameworks + 5 Polymarket paper sub-strategies, 16 app sections (10 Trading core + 6 Personal research).

---

---

## 35. Free Macro & News Data Sources (feed §29 and the sentiment pipeline)

Three additions, all genuinely free, no new paid dependency:
- **FRED (Federal Reserve Economic Data) API** — the Fed's own public API: interest rates, inflation, employment, GDP, every macro series the Fed publishes. The authoritative source for "what the Fed actually said/printed," far more reliable than scraped headlines. Feeds §29 with **values**, not just event dates (e.g. "CPI printed 3.2% vs 3.0% expected").
- **EIA (U.S. Energy Information Administration) API** — official U.S. energy data, free: WTI/Brent prices, inventories. Covers oil/energy context for macro-sensitive sessions and any commodity-ETF strategies (GLD/USO/XLE are ordinary Alpaca symbols).
- **Finnhub (free tier)** — supplementary market and company news plus basic economic/market-calendar endpoints; fills coverage gaps Yahoo RSS misses. Feeds the Phase 3 sentiment pipeline alongside Reddit/Yahoo/Polymarket.

**Evaluated and NOT adopted:** Alpha Vantage news+sentiment (redundant with Hermes' own sentiment condenser), and the paid aggregators (marketaux, EODHD, APITube, finlight, NewsAPI, LexisNexis) — thin free tiers, nothing FRED+EIA+Finnhub don't already cover for €0.

---

## 36. E5 — Long-Term Investment Analysis (reinstated; Personal Research)

**Why it exists:** E2 answers "is this fairly priced right now" (trader's snapshot). E5 answers "is this a business worth owning for 5–10 years." Same data pipeline (FMP + Alpaca), different lens — **one engine, two lenses**, so this is one extra scoring function + one extra Hermes template, not a duplicate module.

**Fixed methodology (same five checks, every ticker — this is what distinguishes it from Fundamentals Lab):**
1. **Growth consistency** — revenue/EPS growth over 5/10/15 years: steady or lumpy? (multi-year CAGR averages, not just the latest figure)
2. **Margin & ROIC/ROCE/ROE trend** — is the moat widening or eroding over time? (5/10/15-year averages, direction of travel)
3. **Debt trajectory** — leverage, interest coverage, improving or worsening across years
4. **Shareholder returns** — dividend history, payout ratio, buybacks vs share dilution
5. **Valuation vs its own history** — current P/E, EV/EBITDA, FCF yield against the stock's **own 10-year range** (not only vs sector)

**Output:** long-term quality score + classification (**Wonderful / Fair / Avoid**) + the specific risks that would break the thesis. Written to `longterm_reports`. Hermes template #14. All math in Python; Hermes writes the judgment.

**Provenance note for the builder:** this five-check list was authored during design (not supplied by the owner, not an inherent model behaviour). The **Richard Coffin fundamental research process** supplied by the owner is stored verbatim in the prompt library (§23, `/extension-modules/`); its quantitative steps (finances, strategy/capital efficiency, valuation) may be adopted as E5's methodology in a later revision — the owner deferred that decision. Its qualitative steps (screening, understanding the business, skepticism/devil's advocate) are already owned by §37.

**Build order:** slots after E2 (shares its pipeline): E1 → E2 → **E5** → **§37 Fundamentals Lab** → E3 → E4.

---

## 37. Fundamentals Lab (new Personal Research section — the owner's OWN methodology, not Hermes')

**The distinction that justifies a separate section:** E2 and E5 apply *fixed* methodologies to a ticker you name. Fundamentals Lab runs **your** criteria — with or without a ticker. It is the only section where the analysis logic itself is user-supplied.

### Mode A — Screener (build first; cheap and fast, use liberally)
- Input: plain-language criteria, **no ticker needed** (e.g. "revenue growth >15%, D/E <0.5, profitable, market cap >$1B").
- Hermes translates the sentence into structured filter parameters (small, cheap call) → **FMP stock-screener endpoint** does the filtering (free tier, one API call) → ranked candidate list with the key numbers returned in seconds.
- Cost: pennies per run. This is Coffin's Step 1 (screen wide before going deep).

### Mode B — Deep-dive (build second; the most expensive single action in the app — use selectively)
- Input: **one ticker** (from Mode A or your own) **plus, optionally, your own thesis/checklist** ("I think this is a moat play because X — check whether that holds").
- Pulls primary documents: **SEC EDGAR** filings (10-K/10-Q, free public API) and recent earnings-call transcripts.
- Hermes reads and produces: business summary (segments, revenue nature, geography, key players), **SWOT**, sector-specific KPIs vs peers, management execution track record (do multi-year ROIC/ROCE results match past promises?), and a deliberate **devil's-advocate pass** — arguments against the thesis, red flags on "adjusted" earnings language, related-party/conflict-of-interest checks, and a "what would have to be true for this to fail" section.
- Cost note for the builder: long documents mean materially higher token usage than any other module (still likely well under €1 per report on the hosted tier, but not "pennies"). On-demand only, never scheduled.

**Tables:** `fundamentals_screens` (criteria, parsed filters, results), `fundamentals_deepdives` (ticker, user thesis, sources fetched, SWOT, KPIs, devil's-advocate output). **Templates:** #15 (criteria→filters), #16 (deep-dive analysis with adversarial pass).
**Naming note:** deliberately NOT called "Researcher" — E4 Researcher is thematic discovery ("best AI investments"); Fundamentals Lab is user-defined fundamental logic.

---

## 38. Figma — evaluated and rejected for Phase 9

Figma (and the Figma MCP server) was considered for UI design and rejected. Reasons: the free Starter plan allows only ~6 MCP tool calls per month (a demo, not a workflow), making a paid Dev/Full seat (~$12–16/mo) effectively mandatory; and more fundamentally, the tool bridges a gap this project doesn't have — the UI is built **directly in code** (React/HTML + Tailwind), which is the very output Figma-to-code pipelines are trying to produce. The versioned `dashboard_mockup.html` (§32) already serves as the shared visual source of truth. **Phase 9 approach:** iterate on rendered HTML/React mockups in-repo using the frontend-design guidance; no design-tool dependency, no subscription.

---

## 39. `APP_OVERVIEW.md` — MANDATORY plain-language reference file

The builder MUST create `/APP_OVERVIEW.md` at the repository root: a short, non-technical map of the whole product, written for the owner (and any third party) to check at a glance. It is NOT a duplicate of this specification — it is the one-page answer to "what is in this app and how do I use it." It must be updated whenever a section is added or renamed, and its content is the source for the Walkthrough section's Overview page (§25).

**Required contents:**
1. **Trading Core (runs automatically, daily)** — one line per section: Dashboard, Scanner, Signals, Journal, Autopsy, Analytics, Backtests, Strategy Lab, Library, Guardian.
2. **Personal Research (on-demand)** — one line per section: Portfolio Risk, Analyst, Long-Term Analysis, Fundamentals Lab, Polymarket, Researcher, Ask Hermes, Walkthrough.
3. **Background (invisible, always running)** — Telegram alerts, synthetic stops, economic-calendar warnings, self-learning loop.
4. **Tools we use** — grouped: paid (~€40/mo), free core stack, free support tools, optional/future — each with a one-line purpose (mirrors §2).
5. **How the app works** — a short paragraph: scan → enrich → Hermes plans → you deploy → journal → weekly learning loop.
6. **Where you INPUT vs where you only READ** — an explicit two-column split (see below), because this is the question the owner asks most often.
7. **How to use each one well** — one practical line per input section (e.g. "screen liberally, deep-dive selectively").

**Input vs read-only split (must appear verbatim in substance):**
- **You provide input:** Signals (Deploy/Cancel decision) · Manual order (your own ticket) · Position management (adjust/close) · Journal (subjective note after a trade) · Strategy Lab (approve/reject) · Library (add/activate/retire strategies) · Backtests (choose strategy + date range) · Analyst & Long-Term (a ticker) · **Fundamentals Lab (your own criteria or ticker + your own thesis — the deepest input in the app)** · Researcher (a theme) · Ask Hermes (any question).
- **You mainly read:** Dashboard · Scanner results · Autopsy · Analytics · Guardian reports · Portfolio Risk · Polymarket · Walkthrough.

---

## 40. Changelog v1.2 → v1.3
1. **§35** free macro/news sources added: FRED (Fed data), EIA (energy), Finnhub (news) — feeding §29 and the sentiment pipeline. Paid aggregators and Alpha Vantage evaluated and rejected.
2. **§36 E5 Long-Term Investment Analysis reinstated** (fixed five-check methodology, `longterm_reports`, template #14), with an explicit provenance note about the Coffin document.
3. **§37 Fundamentals Lab added** — the only section driven by user-supplied analysis logic; Mode A screener (cheap, no ticker) and Mode B deep-dive (EDGAR filings + transcripts + devil's-advocate pass, costliest action in the app); tables `fundamentals_screens`, `fundamentals_deepdives`; templates #15–16.
4. **§38** Figma/Figma MCP evaluated and rejected for Phase 9, with reasons.
5. **§39** `APP_OVERVIEW.md` made a mandatory deliverable, including the input-vs-read-only split.
6. Personal Research is now **8 sections**; app total **18 sections** (10 Trading Core + 8 Personal Research).
7. Totals now: ~25 tables, 16 Hermes templates, 23 detectors, 9 exit plugins, 8 strategies + 2 frameworks + 5 Polymarket paper sub-strategies.
8. Unchanged and still deferred: options, futures, forex, 1-min scalping, live equity L2, multi-broker (commodity/index ETFs already usable as ordinary config symbols).

---
*End of specification v1.3. Build order: Phase 0 (schema + §23 prompt-library folder + §39 APP_OVERVIEW.md) first — every later phase reads from them. Extension modules and the Order-Flow Research Track strictly after the core paper month.*
