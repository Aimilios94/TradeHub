# Build decisions log

Decisions taken during implementation that refine or deviate from `tradehub_implementation_plan.md` v1.3. The spec stays the design authority; this file records how it was built.

## 2026-09-08 — Phase 0

1. **Thin n8n, fat Python.** The spec placed scan/enrich/execute logic in n8n workflows. Owner accepted: n8n keeps crons (ET), Telegram, and inbound webhooks; every pipeline step is a FastAPI endpoint in `engine/` with pytest coverage. §21 stays intact (n8n→Alpaca via HTTP where n8n still touches Alpaca; Python via alpaca-py; Edge Function via fetch).
2. **Backtester core will be a custom pandas/NumPy event loop** rather than vectorbt / backtesting.py — neither handles multi-symbol scanner-universe replay with an explicit fill model. Final call at Phase 7.
3. **Tables added beyond §5's ten:** `app_settings`, `plugin_registry`, `frameworks`, `prompt_templates`, `economic_events`, `bars_1min`, `bars_daily`, `premarket_volume_baseline`, `feed_calibration`. All implied by the spec (§10, §11, §20, §29, Phase 1).
4. **Seed is generated, not hand-written.** `engine/scripts/gen_seed.py` renders `0002_seed.sql` from `app/registry.py`, `app/seed/*.py` and `prompt-library/hermes-core/*.md`; pytest fails when the SQL is stale. Strategy `status` is never overwritten on re-seed.
5. **Strategy DSL** (`engine/app/models.py`): `universe` (screener|watchlist) → `entry_conditions` {all, soft, stages, human_confirm, hermes_gate} → `exit_logic` [ {plugin, params, when} ] → `risk` → `reentry`. Validation enforces the pre-market rules (limit only, synthetic stop present, no bracket pre-market) and rejects unknown plugin names.
6. **Prompt library layout:** owner originals live in `prompt-library/_originals/` (never edited, never moved). §23's `E*_original.md` filenames were not created; adapted files link to the originals instead.
7. **Kurisko provenance conflicts** resolved as the spec says: PDF wins (40-4 not 44-4; 60-10 embed 85/90 not 80; S6 = PDF bear flag, S7 = interview variant for A/B).
8. **Host:** everything runs on the owner's Windows 10 PC (RTX 2070, 16 GB). Ollama runs natively for GPU access; engine + n8n in Docker. Python 3.12 and Git were installed via winget during this session.
9. **Hermes model ids** default to `nousresearch/hermes-3-llama-3.1-70b` / `-405b` on OpenRouter and `hermes3:8b` on Ollama; overridable via `.env` and per template.

## 2026-09-10 — Phase 1

10. **Pre-market relative volume uses SIP (delayed), not IEX.** Verified on the free Alpaca plan: IEX 1-min pre-market bars are near-empty (AAPL printed 2 bars on 2026-09-09 04:00–09:30; PLUG 19; BNGO 1; SNDL/GNS/MULN none) while SIP pre-market bars for the SAME morning are available up to ~15 min ago (AAPL 323 bars). §3.2 assumed SIP was history-only; it is not. So: `app_settings.data_feed.premarket_volume = "sip"`; the nightly job stores the baseline for BOTH feeds (`premarket_volume_baseline.feed`), and `/data/premarket/{symbol}` always compares live cumulative volume against the same feed at the same checkpoint (clamped to the delayed feed's latest bar). The spec's invariant "same feed on both sides" is preserved; only the feed changed. Quotes/charts stay IEX (real-time). The Phase 2 scan at 08:00 ET will therefore see SIP volume through ≈07:45 ET. **Owner confirmed 2026-09-10.**
11. **Cumulative checkpoint profile** (`premarket_volume_baseline.cum_profile`, migration 0003): volume-by-05:00 … 09:30 per day, so an 08:00 scan compares against the 14-day average *by 08:00*, not against full-window totals.
12. **Calibration log is a by-product of the nightly job**: it fetches both feeds anyway and writes `feed_calibration` rows for the pre-market and regular windows; `v_feed_calibration_30d` exposes the median IEX/SIP factor for §13 trap 2.
13. **Engine auth**: every non-trivial endpoint requires `Authorization: Bearer <ENGINE_API_TOKEN>` (`app/auth.py`); n8n stores it as a Header-Auth credential. Jobs send their own Telegram summary (`app/notify.py`) so n8n stays a scheduler.
14. **Supabase connection from this PC** goes through the session pooler (`aws-0-eu-west-2.pooler.supabase.com`); the direct `db.<ref>` host is IPv6-only. Migrations are applied with psycopg from the venv, not the SQL editor.
15. **Edge Function deployed 2026-09-10** via `npx supabase functions deploy market-proxy --use-api` (Node 24, owner access token in `.env`). New-style `sb_secret_`/`sb_publishable_` keys go ONLY in the `apikey` header (no Bearer), otherwise the gateway answers "Conflicting API keys". Smoke-tested: IEX quotes + SIP bars pass through.

## 2026-09-10 — Phase 2

16. **Universe feeder = Alpaca, not Finviz Elite / FMP.** Finviz Elite is deferred (paid). The FMP *free* tier refuses its screener, batch quotes and float endpoints (HTTP 402 verified 2026-09-10); only `profile` (250 calls/day) and `biggest-gainers` work. So the nightly job (`POST /scan/universe/run`, workflow 01) builds `symbol_stats` for all ~13k tradable listed US equities from Alpaca assets + SIP daily bars (free): prev close/OHLV, ADV20/50, Wilder ATR14, 20/60-day highs/lows, shortable/ETB flags. `ScreenerUniverse.source` gained `"alpaca"` and S1/S3 seeds use it. Float: `symbol_stats.float_shares` is filled only when Finviz arrives; until then the scanner uses FMP `profile` shares outstanding (≥ float, flagged `approximate`, ≤ 30 calls per scan, cached 7 days) and never rejects a symbol whose float is unknown — it flags it.
17. **Two-stage scan.** Stage A filters `symbol_stats` locally with the strategy's `universe.params` (price band, ADV, ATR, float cap when known). Stage B fetches today's SIP pre-market 1-min bars for the union of candidates in one bulk pass (~45 s for the full list, far less after stage A), applies the gap filter, then runs the implemented detectors and evaluates `entry_conditions.all` per strategy. Hard gates that cannot run at scan time (Hermes one-sentence test, news catalyst, Phase-7 detectors) are recorded as `pending_gates`, never silently passed. `scans.passed` therefore means "screener + every computable gate passed"; Phases 3–4 consume the pending gates.
18. **Which strategies the 08:00 scan covers:** every strategy with a screener universe, plus full_auto watchlist strategies (S2 briefing rows). semi_auto intraday strategies (S4–S8) are Phase 7's live-detector loop, not a screener. Strategies in `backtesting` ARE scanned (no orders can result before Phase 5 + Guardian) so the scanner proves itself before promotion; the cron default is `statuses=backtesting,paper,live`.
19. **Structure filter levels** (`distance_to_level_atr`) use daily structure only — prior-day H/L/C, 20/60-day extremes, round numbers — never the pre-market high/low (the latest print always sits there).
20. **`scans.scan_date`** (migration 0004) + unique index (strategy, scan_date, symbol, run_kind): a re-run replaces the day's rows instead of duplicating them.

## 2026-09-10 — Phase 3

21. **Enrichment adds information; it never changes a verdict.** `POST /enrich/run` writes a `catalyst`
    block into `scans.metrics` and adjusts `metrics.score` / `rank`, but never touches `scans.passed`.
    A catalyst is evidence for the Hermes decision node (Phase 4), not a screener filter. Owner confirmed
    2026-09-10.
22. **There is no `news_catalyst` hard gate to clear.** The Phase 3 plan assumed enrichment would resolve a
    pending gate of that name; checking the seeded strategies showed none exists. S1 treats the catalyst as
    a SOFT input ("news catalyst preferred (soft score from `news_sentiment`)", §7) and S3/F-A feeds it into
    the Hermes one-sentence in-play test (template 04, hosted tier). `metrics.pending_gates` holds
    unevaluable HARD conditions plus that Hermes slug — every one of them Phase 4's or Phase 7's to satisfy.
    So the write-back leaves `pending_gates` untouched, and the catalyst's scan-time job is exactly the soft
    score the plan asked for. The clearing logic was removed rather than left as a no-op.
23. **The score bonus uses catalyst MAGNITUDE, not direction**: `catalyst_weight * |sentiment_score|`
    (default 2.0, `app_settings.sentiment`), and only for a catalyst that is both identified
    (`catalyst_type != "none"`) and `fresh`. "In play" (F-A, the SMB Capital stocks-in-play triple filter,
    §8) means a real catalyst exists; whether it argues long or short is judgment, and judgment is Hermes'
    in Phase 4. Re-running enrichment is idempotent — the previous bonus is backed out before the new one
    is applied, so the score never drifts.
24. **Phase 3 needed no migration.** `news_sentiment` already shipped in `0001` with
    `unique (symbol, trade_date, source)`, and `news_source_t` already covered
    reddit/yahoo/polymarket/finnhub/firecrawl/other. Only `0002_seed.sql` changed (one new `app_settings`
    row, `sentiment`), and it was applied as that single idempotent statement rather than re-running the
    whole seed, so no other settings row was overwritten.
25. **Sources: Finnhub + Yahoo RSS now, Reddit implemented-but-inert.** Yahoo RSS is parsed with stdlib
    `xml.etree.ElementTree` — no `feedparser` dependency for four fields. Reddit's OAuth fetcher is written
    and tested; with blank keys it returns `skipped: no credentials`, never a silent "no news" (§33).
    Adding it later is a `.env` edit plus `"reddit"` in `app_settings.sentiment.sources` — no code change.
    Polymarket and Firecrawl stay out of Phase 3 (Firecrawl is the plan's explicit last resort, §57).
26. **LLM contract, enforced in one place** (`app/llm/`): strict JSON in, validated against the template's
    own `output_schema` (stdlib validator, no `jsonschema` dependency), one retry with the validation error
    appended to the prompt, then FAIL. A source whose model output never validates is recorded `failed` and
    writes no `news_sentiment` row — nothing is smoothed over or invented. Prompt text is read from the
    seeded `prompt_templates` row at run time, falling back to the markdown source when Supabase is
    unreachable (the fallback is reported in `PromptSpec.origin`, not hidden).
27. **Ollama host resolution**: `host.docker.internal` also resolves on the Docker Desktop *host*, but
    points where a localhost-bound Ollama is not listening — so resolvability is not the test. `app/llm/
    ollama.py` rewrites the alias to `localhost` only when NOT running inside a container (`/.dockerenv`).
    Verified both paths work: engine container → `host.docker.internal:11434`, host pytest → `localhost`.
28. **n8n import is CLI-driven and per-file** (`n8n/build_import.py`). `n8n import:workflow` does not
    de-duplicate tags within a batch, so importing 01–03 together aborts on
    `UNIQUE constraint failed: tag_entity.name`; one file per command reuses the existing tag. The tracked
    workflow JSON carries no `id` (that is deployment state), so the generator injects a stable
    `tradehub-wf-NN` — without it the CLI refuses the import, and with a random one every re-import would
    duplicate the workflow. Imported workflows arrive inactive by design.
