# Monitoring

What is watched and where to look. Items marked "planned" are designed but not built yet.

## Service health (built)

- `GET /health` — API and database. Returns 503 with the real error when the database is unreachable.
- `GET /health/deps` — database, SearXNG and Ollama, each with its own state and error.
- `docker compose ps` — container state. The `api` container has a health check.

## Source health (built)

- The `sources` table holds `last_success_at`, `last_error_at` and `last_error` per source, shown by `GET /api/sources` and on the status page.
- Every job that calls a source updates that source's row. A source is healthy when its last success is newer than its last error.
- `last_error` is the real error text with anything key-like removed.
- `POST /jobs/check_sources` makes one real call per adapter (Yahoo, Yahoo RSS, Finnhub, Massive, SearXNG, CFTC, StockTwits) and updates their rows.
- A failed source keeps its last data with a stale label; no rows are invented.

## Recorder jobs (built)

- `record_news`, `record_bars` and `record_cot` are triggered by n8n. Each answers with what was stored and every error.
- A run with any failure answers HTTP 502, so it shows as a failed execution in n8n (Executions tab).
- A job that is still running refuses a second trigger with HTTP 409.

## Snapshots (built)

- `docker compose exec api python -m scripts.snapshot list` shows the registered snapshots.
- `docker compose exec api python -m scripts.snapshot verify <version>` re-hashes a snapshot and reports any difference from its manifest and from the `datasets` table.

## News pipeline (built)

- `process_news`, triggered by n8n after each news run, does four steps: `process` (dedupe, tags, importance), `condense` (Hermes), `social` (StockTwits) and `score` (daily composite). Each step reports its own result; a step that fails does not stop the others.
- The job answers 502 when `process`, `condense` or `score` failed. A StockTwits failure is shown in the result and in source health but does not fail the job.
- `process.duplicate_rate` is the share of new headlines that were duplicates in that run.
- `GET /api/news/top?day=YYYY-MM-DD` shows a day's ranked headlines with tags and condenser output; `GET /api/scores?day=YYYY-MM-DD` shows the daily scores.

## Hermes calls (built for the condenser)

- Every call is logged to `llm_traces`: template, prompt version, tier, model, attempt, latency, tokens, cost, outcome, and the news item it was for.
- Outcomes: `ok`, `invalid_json`, `schema_failed`, `number_check_failed` (a rejected answer; the error column holds the reason), `error` (the call itself failed), `capped`.
- A rejected answer is retried once. Two rejections leave the item without a row; nothing is stored from a rejected answer.
- Share of headlines that ended valid, per day:

```sql
SELECT created_at::date AS day,
       count(*) FILTER (WHERE outcome = 'ok') AS valid,
       count(*) FILTER (WHERE outcome IN ('invalid_json', 'schema_failed', 'number_check_failed')
                          AND attempt = 2) AS failed_twice,
       count(*) FILTER (WHERE outcome = 'error') AS failed_calls
FROM llm_traces WHERE template = 'condenser' GROUP BY 1 ORDER BY 1;
```

- The month's cost is the sum of `cost_usd` in `llm_traces`. A local Ollama call costs 0; a provider that reports no cost leaves the column empty rather than a guess.

## Budget and caps (built, alert planned)

- `CONDENSER_DAILY_CAP` (300) calls per UTC day and `CONDENSER_RUN_CAP` (100) per run. When a cap stops a run with headlines left, one row with outcome `capped` records how many.
- Monthly budget: `LLM_MONTHLY_BUDGET_EUR` (5 by default). When the month's cost reaches it, the condenser stops and logs `capped`. The Telegram alert is planned.
- The OpenRouter key also has its own hard limit set on the OpenRouter side.

## Alerts (planned)

- Channel: Telegram, sent by an n8n workflow.
- Triggers: a source failing, the budget threshold, a failed backup.

## Backups (built)

- The `backup` container runs `pg_dump -Fc` when it starts and every 24 hours, into `backups/` on the host, and deletes dumps older than 14 days.
- Check: `docker compose logs backup` shows `backup ok` or `BACKUP FAILED`.
- Restore: `pg_restore --clean --if-exists -d tradehub /backups/<file>.dump` from inside the `backup` container.
- Planned: weekly copy of Parquet snapshots to a second disk or cloud folder, and a monthly restore test.
