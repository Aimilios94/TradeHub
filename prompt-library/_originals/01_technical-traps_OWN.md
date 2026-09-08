# Technical Traps — original notes (owner-supplied)
Source: owner (pasted, design phase). Verified/updated versions live in plan §3.

three hidden technical traps that will break your automated architecture if you don't account for them before writing code.
Because you are using a REST API approach instead of a live WebSocket, your strategy must explicitly address these three gaps:

## 1. The Twelve Data "Time Stamp" Trap
The Issue: When you fetch a stock's pre-market data via Twelve Data's /time_series endpoint, the volume returned is the volume of that current 1-minute bar, not the Cumulative Total Pre-Market Volume since 4:00 AM.
The Fix: Your n8n workflow must use a small code block to sum up all the historical 1-minute bars from 4:00 AM to the current time to calculate the true active morning volume, before comparing it to your 14-day average.

## 2. The Firecrawl "Rate Limit" Trap
The Issue: Finviz might hand you a list of 15 top gainers at 8:00 AM. If you instantly send all 15 symbols to Firecrawl to scrape Google, Yahoo, and Reddit simultaneously, you will hit the rate-limit blocks on Firecrawl's lowest/free tier, or Google will throw a CAPTCHA at your scraper.
The Fix: You must use n8n's Loop Node paired with a Wait Node set to 2–3 seconds. This staggers your text queries safely so your scraping account stays active and free.

## 3. The Alpaca "Extended Hours" Flag
The Issue: Standard Alpaca market orders are rejected if sent before the 9:30 AM EST opening bell.
The Fix: To make sure Alpaca accepts your bracket orders at 8:30 AM EST to lock in your strategy, n8n must explicitly pass the advanced parameter flag extended_hours=true and use a limit or stop_limit order type. Alpaca completely blocks market orders (type=market) during pre-market hours.

## Frontend / architecture notes (owner-supplied)
- Frontend should make direct REST calls to the data provider on screen load for live charts (NOTE: superseded in plan §3.11 — must go through a Supabase Edge Function proxy so API keys never ship to the client).
- Routing live chart rendering through n8n or Supabase DB writes would create a bottleneck and exhaust free-tier storage.
- Frontend fetches directly (real-time): /quote endpoint (flashing price, pre-market % change, daily high/low); /time_series with interval=1min&prepost=true (live intraday candles).
- Frontend fetches from Supabase (analytical): account balance, equity curve, daily P&L; the curated AI morning watchlist (entry triggers, stop-losses, trailing stop %); long-term backtest accuracy logs by sector.

## Owner's intended daily workflow (original wording)
- 8:00 AM EST — Open the App: dashboard immediately displays real-time market data; live pre-market candles for top indices/tech sectors; portfolio stats load from Supabase.
- 8:15 AM EST (strategy time) — Automated routine runs: n8n cron downloads the Finviz gap list, computes the 14-day relative volume multiplier, scrapes news, scans Polymarket, asks Hermes to formulate the day's targets.
- 8:17 AM EST — Watchlist pops up: n8n writes Hermes' decisions to Supabase; the app catches the update and flashes "Strategy Match Found".
- 8:20 AM EST — Execution confirmation: tap "Deploy to Alpaca"; an isolated n8n node converts Hermes' parameters into a live extended-hours bracket limit order.
  (NOTE: superseded in plan §12 — Alpaca does not accept bracket orders in extended hours; use limit + TIF=day + extended_hours=true, synthetic stop pre-market, OCO attached at 09:30.)

## Two-engine concept (owner's original framing, retained in plan §4)
- Step 1 — The Visual Radar (direct frontend): instant charts on open, manual lookup of any ticker, zero DB writes.
- Step 2 — The Execution Factory (n8n on command): heavy automated calculations begin when the user presses "Execute Pre-Market Strategy".
- Step 3 — Safety confirmation: Hermes writes setups to Supabase; app shows the curated selections with [DEPLOY TO ALPACA PAPER] / [CANCEL TRADE]; user keeps total control.

## Example alert format (owner-supplied)
🎯 STRATEGY ALERTS FOUND
Ticker: BBAI | Price: $3.40 | Volume: 7.2x
Catalyst: Confirmed Q1 Earnings Beat (Scraped via Firecrawl)
AI Plan: Buy Stop at $3.55 | Stop-Loss at $3.20 | Trailing Stop 4.5%
[ DEPLOY TO ALPACA PAPER ] [ CANCEL TRADE ]
