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
- `POST /jobs/check_sources` makes one real call per adapter (Yahoo, Yahoo RSS, Finnhub, Massive, SearXNG, CFTC) and updates their rows.
- A failed source keeps its last data with a stale label; no rows are invented.

## Recorder jobs (built)

- `record_news`, `record_bars` and `record_cot` are triggered by n8n. Each answers with what was stored and every error.
- A run with any failure answers HTTP 502, so it shows as a failed execution in n8n (Executions tab).
- A job that is still running refuses a second trigger with HTTP 409.

## Snapshots (built)

- `docker compose exec api python -m scripts.snapshot list` shows the registered snapshots.
- `docker compose exec api python -m scripts.snapshot verify <version>` re-hashes a snapshot and reports any difference from its manifest and from the `datasets` table.

## Hermes calls (table built, writers planned for Phase 1)

- Every call is logged to `llm_traces`: template, prompt version, tier, model, attempt, latency, tokens, cost, outcome.
- Outcomes: `ok`, `invalid_json`, `schema_failed`, `number_check_failed`, `error`, `capped`.
- The month's cost is the sum of `cost_usd` in `llm_traces`.

## Budget (planned)

- Monthly budget: `LLM_MONTHLY_BUDGET_EUR` (5 by default). An alert fires when the month's cost reaches it.
- The OpenRouter key also has its own hard limit set on the OpenRouter side.
- A daily cap on condenser calls; calls over the cap are logged with outcome `capped`.

## Alerts (planned)

- Channel: Telegram, sent by an n8n workflow.
- Triggers: a source failing, the budget threshold, a failed backup.

## Backups (built)

- The `backup` container runs `pg_dump -Fc` when it starts and every 24 hours, into `backups/` on the host, and deletes dumps older than 14 days.
- Check: `docker compose logs backup` shows `backup ok` or `BACKUP FAILED`.
- Restore: `pg_restore --clean --if-exists -d tradehub /backups/<file>.dump` from inside the `backup` container.
- Planned: weekly copy of Parquet snapshots to a second disk or cloud folder, and a monthly restore test.
