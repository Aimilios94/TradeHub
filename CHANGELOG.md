# Changelog

One entry per version. Phases of the MVP are listed under "Unreleased" until v1.0 ships.

## Unreleased (towards v1.0)

### Phase 0 — Foundations (2026-11-11)

- Repository scaffold: backend, frontend, db, n8n, strategies, sample-data, docs.
- Docker Compose stack: PostgreSQL 16 (no published port), FastAPI, SearXNG, n8n, and a backup container.
- Alembic migration `0001` with the 16 v1.0 tables; fixed menus enforced by CHECK constraints.
- Config seeds: 7 driver tags, 28 tickers with canonical IDs, 12 sources, 19 sensitivity hypotheses, 16 watchlist symbols.
- API: `/health`, `/health/deps`, `/api/sources`, and `/jobs/{name}` protected by a shared secret.
- Frontend: a status page showing service health and the source registry.
- CI: ruff, migrations on a PostgreSQL service container, pytest, frontend lint and build.
- Docs: data sources with verification results, backtesting method with promotion thresholds, monitoring, privacy.
