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

## 2026-09-10 — Phase 4

29. **The in-play gate records its verdict; it does not flip `scans.passed`.** Template 04's own Notes
    prescribe writing `scans.reject_reason`, but the owner chose the softer record (2026-09-10): the
    verdict, sentence, strength and reason go to `scans.metrics.in_play` with `decided_by`, and `passed`
    keeps meaning "the code-based filters passed". Where code and Hermes disagree therefore stays visible,
    which is the record needed to judge whether the gate can be trusted. Only gate survivors get a plan.
30. **A briefing is not a signal.** Template 03's output has no entry, stop, target or size and nothing can
    be executed from it, so it lives in its own `briefings` table (migration 0005) rather than in `signals`
    under a new status. Keeps `signals` meaning exactly one thing — a proposed trade — so a "signals today"
    count is never inflated by briefings. Owner's call.
31. **Phase 4 ships an ADVISORY pre-check, not the Guardian gate.** `context.advisory_precheck` computes the
    cheap read-only slice already available — kill switch, open positions vs `max_concurrent`, live spread
    vs the ceiling, easy-to-borrow for shorts — labels itself `advisory: true`, and cannot block anything.
    It exists so template 02 sees real numbers instead of blanks. Anything it cannot know (the daily loss
    limit, which needs closed trades) reports `unknown`, never `green`. Phase 5 replaces it with the
    enforcing Guardian.
32. **Every prompt is shown its own output schema.** Answers are validated against the template's
    `output_schema`, but that schema was never sent to the model — so it invented a reasonable-looking shape
    and every hosted answer failed validation (template 04 returned `{"CULP": {...}}`; template 03 omitted
    `rows`). Template 01 had survived only because its system prompt enumerates every field in prose.
    `llm.schema_contract` now appends the schema plus the omission rule to every prompt. Related: an
    OPTIONAL key sent as `null` is treated as omitted by the validator — models express an inapplicable
    field that way (a 'skip' plan has no size), and rejecting it failed otherwise-correct answers.
33. **Code pre-computes the stop/size pairing; Hermes only chooses a row.** Template 02 forbids Hermes
    calculating anything, yet the stop it picks determines the correct size — so with only a 1-ATR estimate
    on offer it either invented a stop distance (TNON: `entry - 0.5 x ATR`, landing 3.17% from the nearest
    real level) or sized against the stale cap (CULP: 3607 shares risking $1,407 of a $500 budget). Each
    candidate now carries `stop_options`: every valid stop level paired with the shares that stop allows.
    Two related fixes: `atr_extensions` supply measured-move targets (a target is by nature beyond current
    structure, so without them every target failed the ±3% level check), and `round_numbers` uses a finer
    grid below $10 — at $3.70 a 0.50 step returned NO level within ±5%, silently stripping half the
    candidate's usable structure.
34. **Size is validated against the stop actually chosen, not the estimate.** A tighter stop legitimately
    allows more shares, so capping against the 1-ATR pre-estimate would reject correct plans; capping only
    by it would pass over-risked ones. The check is `size x |entry - stop| <= risk_dollars` (5% rounding
    tolerance), and the message names the maximum shares that stop permits.
35. **Model choice stays with the template.** Templates 02-04 declare Hermes 3 70B. A 405B comparison on the
    same candidate produced a valid answer too (it chose `skip` where the 70B wrote a plan) — judgment,
    not correctness, so the declared model stands and `OPENROUTER_MODEL_HIGH_STAKES` remains free for later
    phases. A full morning costs about one cent; the whole of Phase 4's development consumed $0.06.
36. **Nothing in Phase 4 can execute.** `app/broker/` is read-only by construction (account snapshot and
    positions only) and every `signals` row is a proposal. A plan that fails code re-validation is stored
    `status='rejected'` with `llm_validation_failed: <reasons>` — never silently corrected (template 02
    Notes). Observed working: a plan with `entry == stop` and one with a hallucinated target were both
    rejected with their reasons intact.

## 2026-09-10 — Phase 5

37. **Live trading is refused in code, not by configuration.** `broker.orders.assert_paper_only()` raises
    if `paper_mode` is false or the base URL is not a paper endpoint, and it guards every submission path
    including the synthetic stop monitor. Owner's decision: a wrong flag, a stray edit or a bug must not
    be able to reach real money before Phase 8's paper month. Lifting it is a deliberate one-line change
    with its own commit, not a setting.
38. **Order shapes are validated in code against the session, never trusted from the caller** (plan §3.4,
    §12 rule 2). Pre-market and after-hours accept ONLY limit + TIF day + extended_hours=true; market,
    stop, stop-limit, bracket and OCO are refused with the reason. This matters because Alpaca *accepts* a
    pre-market stop and then never triggers it — the dangerous failure is silent acceptance, not rejection.
39. **`Guardian.evaluate` is the enforcing Layer-1 gate** (plan §11), replacing Phase 4's advisory version
    (#31). Ten checks: kill switch, account status, order shape, buying power with buffer, spread ceiling,
    position risk, max concurrent, daily loss limit, wash trade, shortable/ETB. Two rules decide every
    ambiguous case: **unknown is never green** (a missing quote, an unreachable broker or an empty journal
    yields yellow with the reason), and **a strategy's risk block may tighten a global default but never
    loosen it** (`_tighter`), so a config cannot widen the spread ceiling or raise the loss limit.
    Verified live on the first run: TNON was blocked on a 3.29% spread against its 2% ceiling.
40. **Deploy is a tap, never automatic.** `auto_approve` exists in settings, ships OFF, and the deploy path
    does not read it (§12 rule 1). Approval arrives as a Telegram inline button because there is no
    dashboard until Phase 9. The callback carries only an action and a signal id — the engine re-loads the
    signal, re-checks the kill switch and re-runs the whole gate before submitting, so a stale or replayed
    tap cannot skip a check. Only the configured `TELEGRAM_CHAT_ID` is honoured; any other chat gets 403.
    Re-deploying a signal that is no longer `proposed` returns 409.
41. **The synthetic stop runs in its own container** (`tradehub-monitor`, same image, different entrypoint).
    Rebuilding the engine after a code change happens constantly and must never take the stop down while a
    position is open; a crash here restarts only this. It reloads its watch list every 30 s so an edited
    stop is picked up without a restart, fires at most once per symbol, and exits with a **bounded limit**
    priced `MONITOR_SLIP_THROUGH_PCT` through the stop — never a market order, which on a low-float
    pre-market name is how a stop becomes a 20% slip. A position with no stop on today's plan is logged and
    left unwatched rather than guessed at, and a failed exit alerts Telegram saying the position is still
    open and unprotected (§33). It is best-effort by nature: a halt, a gap or a dead socket can all beat it.
42. **Positions are protected by the broker from 09:31**, not by our process. Workflow 05 attaches a real
    OCO (take-profit + stop-loss) to every open position with a plan — one minute after the open, because
    real stops are only accepted in regular hours and firing at 09:30:00 risks landing a second early. It
    is idempotent (a position already carrying a resting stop or OCO is skipped) and a position with no
    plan raises a compliance event instead of getting an invented stop.
43. **No migration was needed.** `orders`, `compliance_events` and `guardian_ledger` all shipped in 0001.
    Guardian Layer 2 (the fee/spread/tax ledger) stays Phase 6 as planned — Phase 5 writes `orders` and
    `compliance_events` only.
44. **End-to-end proof on the paper account (2026-09-10):** an AAPL limit 20% below market passed the gate
    green, reached Alpaca, wrote its `orders` row with `quote_mid_at_submit` for later spread-cost
    accounting, was refused on a second deploy (409), and was cancelled cleanly. The kill switch refused a
    submission before the broker was contacted, and the live lockout refused `paper_mode=false`. The test
    signal and its order row were deleted afterwards so they cannot pollute Phase 6 analytics.

## 2026-09-10 — Phase 6

45. **The journal is built from real fills, FIFO, one row per closed round trip.** A scale-in averages the
    entry, a scale-out averages the exit and keeps the trip open until flat, and a fill that flips long
    straight to short splits into two trips. Anything still open at the end of the day is reported as open
    and never written as a closed trade. Lot-level tax accounting is deliberately not attempted: §14 asks
    for per-trade coaching and strategy statistics, where the round trip is the unit.
46. **A paper account is never charged SEC/TAF**, discovered when the activities endpoint returned FILL
    rows and no fee rows for a real round trip (2026-09-10). Guardian Layer 2 therefore reads the broker's
    fees when they exist and otherwise computes them from the published rates, marking the row
    `estimated: true` and saying so in the Telegram summary. The rates live in `app_settings.fee_model`
    because the SEC rate resets annually. An estimate that is labelled is honest; an estimate presented as
    a charge is what §33 forbids. Also: `alpaca-py` 0.44 dropped its activities helper, so this calls
    `/account/activities` through the client's raw `get()`.
47. **`r_multiple` is null when there is no plan.** R is measured against the risk that was *planned*, so a
    manual ticket or an unlinked fill gets no R rather than one invented from the fill price. The same rule
    governs `spread_cost`, which needs the `quote_mid_at_submit` Phase 5 records at order time: no
    recorded midpoint means zero, not a guess.
48. **Win rate is displayed and never gates anything** (§20), and every breakdown carries its trade count
    plus `sufficient`. With one closed trade the analytics says so in its own flags — "below the 100 needed
    to judge anything; treat every number here as provisional" — rather than presenting a 0% win rate as
    though it meant something.
49. **Two analytics bugs the first real trade exposed**, both fixed: `max_drawdown` measured from the first
    trade rather than from zero, so an account that had only ever lost money reported a 0.00 drawdown; and
    `largest_win` was taken from all trades rather than from the winners, so with no winners it reported
    the least-bad loss as the best win.
50. **The weekly review's guards are in code, not in the prompt.** A prompt is a request; these are rules.
    A proposal is discarded if it loosens any Guardian risk limit (§20), names a detector or exit plugin
    outside the registry, names a strategy that does not exist, repeats a previously rejected idea, or is a
    parameter change with no config diff to test. Rejected hypotheses are kept forever as anti-re-suggestion
    memory.
51. **The degradation trigger needs no model.** A strategy with negative expectancy over a sufficient sample
    (a tenth of the promotion bar, floor 5 trades) raises its own `review` hypothesis with
    `source = degradation_alert`. It is the defensive twin of the weekly improvement loop and runs even when
    the LLM call fails.
52. **Nothing in Phase 6 changes a strategy.** `/review/hypotheses/{id}/decide` records `promoted` as a
    decision only — applying a config diff waits for the Phase 7 backtest that grades it. Verified live:
    the first weekly run proposed **zero** hypotheses off one trade, which is the early-life guard working
    exactly as §20 intends.
53. **End-to-end proof (2026-09-10):** a real 1-share AAPL paper round trip produced a journal row
    (net −0.1692 = −0.16 gross less 0.0092 estimated fees), three ledger rows, a correct 13:30-14:30 time
    block, a null R (no plan), an honest autopsy that marked both playbook rules "unknown" rather than
    guessing, and an idempotent second EOD run that wrote 0 and skipped 1.

## 2026-09-10 — Phase 6.5

54. **The rules-knowledge file is `docs/BROKER_RULES.md`**, and it exists because six broker rules are
    load-bearing in this engine: extended-hours order types, no brackets pre-market, PDT retired, the free
    plan's SIP delay, sell-side fees, and the shorting requirements. Each entry states the rule, why the
    code depends on it, and its source page. A rule nobody wrote down is a flag nobody can act on — a test
    asserts every watched rule has a section in the doc.
55. **Freshness detection is deterministic; interpretation is human.** The monthly job fetches each source
    page, strips markup and per-request noise, hashes it, and compares against `app_settings.rules_file`.
    A changed hash raises a `compliance_events` row naming the rule that depends on that page. The job
    **never decides a rule changed and never edits the doc** — its own Telegram summary says so. `rebaseline`
    accepts the new page after a human has read it. An unreachable page is reported, never treated as
    unchanged.
56. **Two of the spec-era URLs were already dead** (`/docs/fees` and `/docs/margin-and-shorting` both 404 on
    2026-09-10) — which is the freshness problem demonstrating itself before the job had run once. Replaced
    with reachable pages and re-baselined. R1 and R2 deliberately share a source page, so both are flagged
    when it moves; that is correct, and the test fixture had to be taught it.
57. **The tax disclaimer is enforced in code, not requested in the prompt.** If the briefing comes back
    without "confirm with an accountant" in its tax line, `run_briefing` appends it. The boundary — monitor,
    not advisor — is repeated in the response payload itself.
58. **A failed briefing still returns the figures.** If the model call fails the endpoint returns the
    gathered, code-computed inputs with an explicit error and the note that the numbers are unaffected,
    plus a Telegram alert pointing at `/analytics/ledger`. Losing the narration must not look like losing
    the data.
59. **The briefing is reachable from Telegram** (`/guardian` + an optional question, workflow 08) since
    there is no dashboard until Phase 9. Non-`/guardian` messages are ignored by the workflow, and the
    engine independently refuses any chat that is not the configured owner.

## 2026-09-11 — Phase 7

60. **The backtester grades a RULE, not Hermes.** Live, an S1/S3 entry price comes from a Hermes plan.
    A backtest cannot call Hermes and must not pretend to — replaying a model's judgment against data
    it can now see is not a measurement. So every backtestable strategy carries a `backtest` block
    (`BacktestSpec`, migration 0006) declaring a deterministic entry rule, stop rule and target rule,
    and every result reports which rule produced it. S1 = a buy-stop 0.1% above the pre-market high;
    S3 = a 15-minute opening-range break; S4–S8 = the next bar's open after the trigger. What the
    backtest grades is therefore the rule plus the exit chain. **S2 has no block and is reported "not
    backtestable" with the reason** — a briefing has no entry, stop, target or size; there is nothing
    to replay, and saying so is better than a silent skip.
61. **The semi_auto strategies fill at the NEXT bar's open, not the trigger bar's close.** S4–S8 require
    the owner to confirm the channel visually before tapping deploy, and that cannot happen inside the
    bar that fired. Paying one bar of slippage is the honest model of a human in the loop.
62. **The four countermeasures (plan §13), each with a test that fails when it is removed:**
    1. *Survivorship*: the replay universe includes Alpaca's INACTIVE assets, which is where the
       delisted low-floats are, and the cap keeps the delisted share intact rather than taking the
       first N alphabetically. Every run reports `delisted_pct`; a screener universe that comes back
       under 1% delisted is flagged `too_clean` **in words** — that is not a clean result, it is a
       missing-data result.
    2. *Feed mismatch*: relative-volume thresholds are rescaled by the median IEX/SIP factor from
       `v_feed_calibration_30d` before the replay uses them. No calibration logged yet → the factor is
       reported unknown and the threshold is left ALONE, never quietly assumed to be 1.0.
    3. *Fill realism*: `app/backtest/fills.py`. A passive limit fills only when volume traded THROUGH
       the level (§31's queue rule), never on a touch; a stop fills through the level and a gap fills at
       the open, not at the stop; fills are capped at 10% of the bar's volume; a bar that touched both
       the stop and the target books the STOP. Spread is estimated from a price-band table because
       historical bars carry no quotes, and the parameters ship in `app_settings.backtest` and are
       reported with every result.
    4. *Overfitting*: minimum sample sizes, and a walk-forward split that flags an edge living in only
       one half of the range.
63. **S4 and S8 could never have fired, and the backtester is how we found out.** Both listed
    `stoch_quad_extreme` (all four bands UNDER 20) and `stoch_divergence` (trigger = the 9-3 turning UP
    THROUGH 20) as same-bar hard gates. Those are mutually exclusive by construction: on 68 sessions of
    SPY the quad passed on 917 bars and the trigger on 422, and the two coincided on **zero**. The
    strategy's own `stages` always said they were different moments. `Condition.within_bars` now makes
    a condition a SETUP — satisfied if it held within the last N bars — and S4 uses 30 bars (2.5h on
    5-min), S8 twelve (~2 sessions on 60-min). A config that can never fire looks exactly like a
    strategy that never triggers, which is why it survived four phases.
64. **The divergence detector confirms only its FIRST leg.** Requiring both swing lows to be confirmed
    swing points costs `swing_lookback` bars on the right-hand side of each, by which time the 9-3 has
    long since crossed 20 — so the trigger was unreachable and the detector would have returned False
    forever while looking healthy. It now checks two shapes: the last two confirmed swings (the textbook
    double bottom), and the last confirmed swing against the RUNNING low since it (what a trader is
    looking at while the second low forms). Both are causal.
65. **The live journal was deducting the spread twice.** `pnl_gross` is computed from actual fill
    prices, which already contain the spread paid; `spread_cost` measures the same money again against
    the midpoint at submit, and `build_row` subtracted it a second time. Net P&L was understated by the
    spread on every trade, which would have made backtest and paper numbers non-comparable — precisely
    the comparison §13's promotion workflow rests on. Fixed: `net = gross - fees`, and `spread_cost`
    is now labelled an attribution in the ledger row. Found while defining the backtester's P&L
    convention; it had no test, which is why it survived Phase 6.
66. **A hypothesis's config diff merges key by key.** A shallow merge would replace a whole `risk`
    block, so a proposal that only tightens `risk_per_trade_pct` would silently reset `max_concurrent`
    and `daily_loss_limit_r` to their defaults — loosening limits the proposal never mentioned, which
    §20 forbids. Lists still replace wholesale: an exit chain is ordered, and merging it item by item
    would build a chain nobody proposed.
67. **`/backtest/promote` will not flip a status without evidence, and never to `live`.** It requires a
    `backtests` row belonging to that strategy whose verdict is `pass`; anything else is refused with
    the unmet and unanswered checks named. Live promotion is refused outright — Phase 8's paper month
    plus the separate, owner-approved change that lifts the in-code lockout (#37).
68. **Unknown is never green, in the verdict too.** An unanswered check (no A/B comparison, no equity
    to measure drawdown against) makes the verdict `inconclusive`, never `pass`. Too few trades beats
    everything: below `promotion_criteria.min_trades_*` the verdict is `inconclusive` even when the
    numbers look bad, because §20 forbids a conclusion on a short sample — and that includes the
    conclusion "this fails". Win rate is computed, displayed, and consulted by nothing.
69. **Deep history is parquet on disk, per plan §5.** `data/parquet/bars/<timeframe>/<feed>/` holds one
    file per symbol for range access, plus `_days/<date>.parquet` for the scanner replay's opposite
    access pattern (every candidate, one session). Supabase keeps its rolling 90 days. The stochastic
    bands a config asks for are precomputed once per symbol-day — every window is rolling, so slicing
    the session frame is identical to recomputing on the prefix, with no lookahead; it cut an S4 run
    from 107 s to 45 s.
70. **First real result (2026-09-11):** S4 over 2026-06-01→09-05 on SPY/QQQ produced 88 trades, a 33%
    win rate and negative expectancy, verdict **inconclusive** — 88 is below the 100 the style needs.
    The PDF's claimed ~95% win rate met measured data, which is exactly what §7 said would happen and
    why win rate is never the gate.
