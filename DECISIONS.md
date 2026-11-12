# Decisions

Every locked or rejected decision with its reason. Settled items are not re-opened without new evidence.

## Locked (from the v2.1 plan, 2026-11-11)

| # | Decision | Reason |
|---|---|---|
| D1 | MVP = Equities only: pipeline, Dashboard, Ticker Pages, daily backtester, Hermes evals | One finished category beats three half-built |
| D2 | Stack: React + Vite, FastAPI, PostgreSQL 16 in Docker, DuckDB + Parquet, n8n, Docker Compose | Self-hosted, €0 |
| D3 | n8n only triggers jobs; all logic in Python | Logic stays testable and versioned |
| D4 | Everything stored in UTC; sessions from a calendar library | No timezone bugs in backtests |
| D5 | Prices and macro history in frozen Parquet snapshots, never in PostgreSQL | Reproducible backtests; small database |
| D6 | MVP backtests use ETFs only (SPY, QQQ, GLD). Yahoo `=F` series are display-only | No contract rolls needed |
| D7 | No USO as an oil proxy; oil backtests wait for back-adjusted CL in v1.2 | Contango decay; 2020 structure change |
| D8 | Driver taxonomy locked at 7 tags plus a scheduled flag | Classification accuracy |
| D9 | Hermes never computes numbers; a number check blocks any output with a figure not in its input | Core principle 1 |
| D10 | No LLM-as-judge in evals | Labels are hand-made |
| D11 | Pass/fail on expectancy and profit factor after costs; win rate never decides | Avoids overfitting to win rate |

## Decided during Phase 0 (2026-11-11)

| # | Decision | Reason |
|---|---|---|
| D12 | PostgreSQL in Docker replaces Supabase | Supabase free plan allows 2 projects, both in use |
| D13 | Better tier model: `nousresearch/hermes-4-405b` on OpenRouter | The only Hermes model OpenRouter lists; volume is a few calls a day, so cost stays inside budget |
| D14 | Promotion thresholds as written in [docs/BACKTESTING.md](docs/BACKTESTING.md) | Owner's starting values; to be tightened after the first runs |
| D15 | Seed watchlist: SPY, QQQ, GLD, TSLA, NVDA and the 11 sector ETFs; everything else is added in the UI | Owner's choice |
| D16 | Alerts go to Telegram, sent by n8n | Owner's choice; wired when alerts are built |
| D17 | TradeHub runs its own n8n container on host port 5680 | Independent of other projects on the same machine |
| D18 | Strip source order: Yahoo, then Twelve Data, then Stooq. Superseded by D40 and D43 | See D19 and D20 |
| D19 | Stooq is "pending": programmatic access needs an API key, requested 2026-11-11. Superseded by D43 | Scripts get an HTML page instead of CSV |
| D20 | Twelve Data free plan is not treated as a strip backup for indices or futures | Probe: SPX and GC need paid plans; VIX, DXY, NQ rejected |
| D21 | StockTwits is additive only, with a minimum count of labeled messages before it enters the composite score | Each call returns a small sample of recent messages |
| D22 | Fixed menus (catalyst type, directions, statuses) are CHECK constraints in PostgreSQL as well as Pydantic rules | A bad value cannot be stored even if validation is bypassed |
| D23 | Migrations are hand-written SQL run by Alembic; no ORM models | About 20 tables; SQL is the clearest description of them |
| D24 | The API container applies migrations and config seeds on every start | One command boots a correct database |
| D25 | Backups run in a sidecar container (`pg_dump` at start and every 24 hours), not from n8n | A backup must not depend on the scheduler being configured |
| D26 | Python 3.12 in Docker and CI | Data libraries used later lag the newest Python release |
| D27 | Host ports: API 8100, SearXNG 8888, n8n 5680, Vite 5273, all on 127.0.0.1 | 8000 and 5173 are used by other projects on the machine |

## Decided during Phase 0.5 (2026-11-11)

| # | Decision | Reason |
|---|---|---|
| D28 | The recorder stores news from both Finnhub and Yahoo RSS, not RSS only when Finnhub fails | The aim is to build history; RSS holds only 20 items per symbol. Phase 1 dedupe handles overlaps |
| D29 | `news_items.source_tickers` (migration `0002`) holds the tickers the source attached to a headline | News is fetched per ticker; without it that link is lost until the condenser runs |
| D30 | Recordings go to `data/live/`; a snapshot is a frozen copy in `data/<version>/` named `v<UTC date>` | Only one place changes; backtests read only frozen data |
| D31 | A snapshot holds only rows dated before the freeze day (UTC) | A bar still forming on the freeze day can never be frozen |
| D32 | Intraday recording: 1m, 5m and 1h for the watchlist. Daily bars: every seeded ticker with a Yahoo symbol | 5m and 1h give 60 days and 2 years of history at once; 1m only builds from now |
| D33 | Schedules in UTC: bars 22:30 daily, news 07:00, 13:00 and 21:30 daily, COT Friday 22:00 | After the US close; daily runs so a missed run is covered by the next |
| D34 | A job with any failure answers HTTP 502 with the full result. What succeeded is still stored | The n8n run shows as failed and the real errors are visible |
| D35 | Yahoo open/high/low/close are stored as received (split-adjusted), with `adj_close`, dividends and split ratios as separate columns | Yahoo does not serve prices unadjusted for splits; the manifest states the method |
| D36 | Spot gold comes from Twelve Data only | Yahoo has no `XAUUSD=X` |
| D37 | COT uses the CFTC legacy futures-only report; gold only for now | Longest history (1986); the only starter strategy that needs COT is Gold + Dollar + COT |
| D38 | Keys go in request headers where the provider allows it, and every stored error is scrubbed of key-like text | A key must not reach `sources.last_error`, a log or the UI |
| D39 | n8n workflows are imported from `n8n/*.json` and published by hand in the n8n editor | The n8n 2.x command line can import a workflow but cannot publish one |
| D40 | Massive (ex-Polygon) free plans are used as the futures backup for the strip, ahead of Stooq, and as a third news feed. Yahoo stays the main source | Owner's decision, 2026-11-11. Twelve Data has no futures and Stooq has not answered; Massive was verified for all 7 futures. This reverses the earlier rejection, which was about stock prices |
| D41 | The futures backup shows the most active of the 4 nearest contracts, not the nearest by expiry | The nearest is often an untraded serial month |
| D42 | A sentiment label that a provider attaches to a headline is stored in `news_items.source_meta` (migration `0003`) as a reference only | It is another model's opinion: never TradeHub's sentiment, never an eval label (D10) |
| D43 | Stooq is `dropped` (key pending), not waiting. It returns to `working` only if a key arrives and an adapter passes its contract test | Owner's decision, 2026-11-11. The acceptance list needs a final state for every source, and nothing depends on Stooq |

## Decided during Phase 1 (2026-11-12)

D46, D47 and D54 hold starting values chosen by the agent. They are hypotheses for the owner to review, not measured results.

| # | Decision | Reason |
|---|---|---|
| D44 | Two headlines are the same story when their normalized titles are equal, or when one title's words are all in the other and the word sets overlap by at least 0.8 (Jaccard), and they were published within 72 hours of each other. The first-stored item stays; later ones get `duplicate_of` | Checked on the first 1,563 recorded headlines: word overlap alone merged templated headlines that differ only by a company name (19 of 20 such pairs were different stories). Result on that set: 17.7% duplicates |
| D45 | Driver tagger v1 is keyword rules in `backend/app/tagger/rules.json`, matched against the title only. A direction is +1 or -1 only when the rules that fired agree; otherwise 0. The condenser may fill a direction only where the code left 0 and the item has exactly one tag; it never overwrites a direction the code set | Title-only keeps precision up; the plan gives the condenser the unclear cases. Precision and recall are measured in Phase 4 |
| D46 | Importance score v1 (0–100): catalyst wording 0.30, copies of the story 0.25, a ticker named in the title 0.20, a driver tag 0.15, number of watchlist tickers 0.10, minus 0.30 for listicle or opinion wording. Time is not part of the score: a day is ranked by score, then newest first | Starting hypothesis. Checked against a hand-ranked day once a month (plan, signal checks); a change is a new version string |
| D47 | Composite score v1 (-100 to +100): news = mean condenser sentiment scaled by volume (full weight at 5 headlines); driver = mean of tag direction × expected direction from the sensitivity matrix; core = 0.6 news + 0.4 driver, or the one that exists; StockTwits adds up to ±20 points when it has at least 10 labeled messages. No input for a ticker on a day means no row. A day is the UTC date of `ingested_at` | Starting hypothesis; social is additive (D21); `ingested_at` is the point-in-time the plan requires. Checked against next-day and 5-day returns once 60 days exist |
| D48 | Until the Phase 4 model eval decides the cheap tier, the condenser runs on local Ollama `llama3.2` (`OLLAMA_MODEL`), at most 300 calls a UTC day and 100 a run, most important headlines first, over the last 3 days. `OPENROUTER_MODEL_CHEAP`, when set, takes over | Free and nothing leaves the machine. Measured on this PC: about 13 seconds a headline. Open question 1 is unchanged: this is a placeholder, not the decision |
| D49 | A condenser answer is rejected, like an off-menu catalyst, when it names a ticker outside the candidate list, gives a driver direction when the item has no driver, or writes a number that is not in the item (the number check runs on its facts and reason). The one retry tells the model why the first answer was rejected | Tickers and directions are fixed menus too (core principle 3); a retry at temperature 0 without the reason would repeat the same answer |
| D50 | An item is finished for a prompt version after a valid answer or two rejected ones. A failed call (model down, HTTP error) is logged as `error` and leaves the item pending; three in a row stop the run and fail the job | A broken model must not mark every headline as failed. The acceptance "valid or logged as failures" counts rejected answers, not outages |
| D51 | The pipeline is one job, `process_news` (dedupe, tags, importance, condenser, StockTwits, daily score), triggered by one n8n workflow at 07:15, 13:15 and 21:45 UTC. A StockTwits failure is reported and written to source health but does not fail the job | One schedule to publish. StockTwits is additive and its endpoint answers 403 now and then; D34 still applies to every required step |
| D52 | StockTwits: one sample per watchlist ticker per UTC day, counts only (`social_sentiment`, migration `0004`). A ticker whose fetch failed is tried again by the day's later runs. No retry inside a run and no workaround for the 403 | Matches the access rule (no. 10). Message text is never stored |
| D53 | The golden day is 24 synthetic headlines in the recorder's shape (`sample-data/golden_day/`). The test stores them through the recorder's code, runs the real pipeline and compares duplicates, tags and ranking with `expected.json`. Rows are marked `is_demo` | Real news text is never committed. `expected.json` changes only on purpose, after reviewing the difference |
| D54 | The label set is 100 shown headlines: 60 drawn at random and 40 from headlines containing a macro word, with a fixed seed. Each item records its stratum, and evals report per stratum. Labels are the owner's; no model output is shown while labeling. The working file is `data/eval/labels.json` (git-ignored); only IDs, URL hashes and labels are exported to the repo | Company news almost never carries a driver tag, so a purely random 100 could not measure the tagger. The macro part is not representative of the news flow and must not be mixed into one accuracy figure |
| D55 | The monthly LLM budget (euros) is compared with the reported cost in US dollars as if they were equal | A dollar is worth less than a euro, so the cap triggers slightly early, never late, and no exchange rate has to be fetched |

## Rejected

| Item | Reason |
|---|---|
| Alpaca or any broker execution | Owner trades by hand; removes the riskiest part |
| Supabase | See D12 |
| Finviz Elite | No free API |
| CryptoPanic | Free developer plan ended April 2026 |
| X/Twitter API | Paid |
| StockTitan | No public developer API |
| Brave Search API | Free tier replaced by metered billing in Feb 2026 |
| Alpha Vantage | 25 requests a day is too few |
| Massive (ex-Polygon) for stock prices; US Treasury API | Overlap with Tiingo and FRED. Massive is used for futures backup and news, see D40 |
| A 4th category "Indices" | Covered by the strip and index tickers |
| Per-category LLM prompts | One engine, one template set, different configs |
| Real-time CME or paid feeds | Delayed and daily data fit this use |
| Taxonomy beyond about 10 tags | Classification accuracy |
| Prices in PostgreSQL | See D5 |
| 8-week MVP | Too much for part-time work |

## Open questions (answered by tests or evals, not opinion)

1. Cheap tier: Ollama or a small OpenRouter model. Decided by `eval_models.py` in Phase 4.
2. Databento intraday backfill: decided at the start of Phase 3 from the cost estimate.
3. Crypto paper trading in v1.1 or later: decided at the v1.1 start.
4. SLC Stochastic settings: defaults (14, 3, 3; 80/20; %K) until the owner's chart settings replace them.
5. Price-action defaults (markets, first-touch zones, stop-first, shorts allowed): confirmed or changed after the first Phase 3 run.
