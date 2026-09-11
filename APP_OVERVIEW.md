# TradingHub — App Overview

Quick reference for what's in the app and how to use it. Keep this updated whenever a section is added or renamed.

---

## TRADING CORE (runs automatically, daily)

| Section | What it does |
|---|---|
| **Dashboard** | See live prices, charts, watchlist, heatmap, account balance |
| **Scanner** | Finds today's trade candidates automatically at 8:00 AM ET (+ 11:30 re-scan) |
| **Signals** | Read ready trade plans and deploy them with one tap |
| **Journal** | See every trade you made with full details and P&L |
| **Autopsy** | AI reviews your trades: did you follow rules, where you slipped |
| **Analytics** | See your stats: win rate, best hours, best setups |
| **Backtests** | Test any strategy on historical data before risking money |
| **Strategy Lab** | Review and approve AI-suggested improvements |
| **Library** | Manage all your strategies and their track records |
| **Guardian** | Safety checks, cost tracking, tax provision, kill switch |

## PERSONAL RESEARCH (on-demand, when you want it)

| Section | What it does |
|---|---|
| **Portfolio Risk** | Stress-test your holdings and see your weak spots |
| **Analyst** | Deep dive on any single stock: is it overpriced or cheap right now |
| **Long-Term Analysis** | Same stock, 10-year lens: growth consistency, moat trend, valuation vs its own history |
| **Fundamentals Lab** | Your own fundamental rules: screen for stocks matching your criteria, or deep-dive one ticker against your own thesis |
| **Polymarket** | Paper-trade prediction markets and track smart wallets |
| **Researcher** | Discover investment opportunities by theme |
| **Ask Hermes** | Chat with your AI about any decision or question |
| **Walkthrough** | Learn what every section does and how to use the app |

## BACKGROUND (invisible, always running)

Telegram alerts · synthetic stops · economic calendar warnings · self-learning loop

**Safety net (Phase 8):** before any order the app checks that the broker answers, the database is up,
the kill switch is off, the price is actually fresh and the pre-market stop watcher is alive — anything
it cannot check **blocks** the order rather than assuming it is fine. It never blocks *closing* a
position. A half-finished scan says "PIPELINE INCOMPLETE" before it says anything else, and it will not
write trade plans on a stale price. Every table is backed up to your PC at 23:30 with a checksum, and
restoring adds back what is missing without deleting what is newer. Every Sunday the ten Guardian
safety checks are drilled (each one forced to block, no real order sent) and you get the go-live
scorecard in plain English on Telegram.

---

## TOOLS WE USE

**Paid (~€40/month)**
1. **Finviz Elite** ($39.50/mo) → Pre-market screener + float data via CSV export
2. **OpenRouter** (~€2–5/mo) → Hosted Hermes 70B/405B for high-stakes decisions

**Free — core stack**
3. **Alpaca** → Broker (paper + live) AND primary market data (IEX live, SIP historical)
4. **Supabase** → Database + Edge Functions (API key proxy)
5. **n8n** (self-hosted) → The orchestrator: all workflows, crons, webhooks
6. **Ollama** (self-hosted) → Local Hermes 8B for bulk summarization
7. **Python + Docker** (self-hosted) → Backtest engine, all math, websocket exit monitor
8. **Reddit API** → Sentiment source
9. **Yahoo Finance RSS** → News headlines
10. **Polymarket API** → Event probabilities + Fed odds
11. **FRED API** → Official Fed macro data (rates, inflation, employment)
12. **EIA API** → Official oil/energy data
13. **Finnhub** (free tier) → Supplementary market + company news
14. **Telegram Bot** → Alerts and daily reports
15. **Cloudflare Tunnel / Tailscale** → Expose n8n securely to the app

**Free — support tools**
16. **FMP** (free tier) → Fundamentals, backup float data, stock screener endpoint
17. **SEC EDGAR** → 10-K/10-Q filings for Fundamentals Lab deep-dives
18. **Twelve Data** (free key) → Backup: index values, VIX display only
19. **Firecrawl** (free credits) → Last-resort scraper
20. **TradingView** (free) → Manual chart eyeballing, outside the app
21. **alpaca-py SDK** → Python connection to Alpaca
22. **PyPortfolioOpt** → Portfolio optimization math

**Optional / future**
23. **Databento** ($125 free credits) → Orderflow research, historical L2/MBO
24. **Alpaca SIP upgrade** ($99/mo) → Only if paper trading proves you need real-time full-market data
25. **SearXNG** (self-hosted) → Web search for Researcher module

---

## HOW THE APP WORKS

At 8:00 AM ET the system wakes up on its own: it scans the market through every active strategy's filters, computes the numbers, reads news and social sentiment for why each stock is moving, then Hermes judges the candidates and writes trade plans — entry, stop, target, size, confidence. You open the app, glance at the Dashboard, read the Signals, and tap Deploy or Cancel. The app executes, protects the position, and logs everything to the Journal. Each week Hermes reviews your results, proposes improvements, the backtest engine proves or kills them, and you approve the survivors in Strategy Lab.

**One line:** Hermes proposes, the Python engine proves, you decide.

---

## WHERE YOU INPUT vs WHERE YOU READ

**You provide input:**
- **Signals** → Deploy or Cancel decision
- **Manual order** → your own order ticket
- **Position management** → adjust stop, take partials, close
- **Journal** → your subjective note after a trade
- **Strategy Lab** → approve or reject suggestions
- **Library** → add, activate, retire strategies
- **Backtests** → choose strategy + date range
- **Analyst / Long-Term Analysis** → a ticker
- **Fundamentals Lab** → your own criteria, or ticker + your own thesis *(the deepest input in the app)*
- **Researcher** → a theme
- **Ask Hermes** → any question

**You mainly read:**
- Dashboard · Scanner results · Autopsy · Analytics · Guardian reports · Portfolio Risk · Polymarket · Walkthrough

---

## HOW TO USE THEM WELL

- **Signals** → your one daily decision point; 10 minutes, then close the app
- **Journal** → add the subjective note the same day, while you remember how it felt (it feeds tilt detection)
- **Strategy Lab** → Sunday only; never approve a suggestion without reading its backtest verdict
- **Library** → retire strategies that stop working; don't hoard dead ones
- **Backtests** → win rate is information, not the gate — look at expectancy and profit factor
- **Analyst** → for "should I buy this now"; **Long-Term** → for "should I own this for years"
- **Fundamentals Lab** → screen liberally (nearly free), deep-dive selectively (costliest action)
- **Portfolio Risk** → before adding money or a big position
- **Guardian** → check monthly, and before funding
- **Ask Hermes** → when you want a "why," not a prediction

---

## THE HONEST PART

Paper trading only for the first month. Not financial advice. ~€40/month in tools. It won't make you rich — it makes you systematic, which is the part that has to come first.
