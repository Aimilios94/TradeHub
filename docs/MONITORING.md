# Monitoring

What is watched and where to look. Items marked "planned" are designed but not built yet.

## Service health (built)

- `GET /health` — API and database. Returns 503 with the real error when the database is unreachable.
- `GET /health/deps` — database, SearXNG and Ollama, each with its own state and error.
- `docker compose ps` — container state. The `api` container has a health check.

## Source health (table built, writers planned for Phase 0.5)

- The `sources` table holds `last_success_at`, `last_error_at` and `last_error` per source, shown by `GET /api/sources` and on the status page.
- Each ingest job will update its own row. A failed source keeps its last data with a stale label; no rows are invented.

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
