# Privacy and what stays local

TradeHub is a single-user app on the owner's own machine. It holds no personal data about other people and no brokerage or account data.

## Never committed

- `.env` and any file holding keys or secrets. `.env.example` holds names only.
- `data/` — Parquet snapshots.
- `backups/` — database dumps.
- Real news text and labels from copyrighted sources. The repo commits label files with IDs and a small synthetic set only.

## What leaves the machine

- Requests to data providers, carrying that provider's API key.
- News headlines and short excerpts sent to the LLM tier in use (OpenRouter, or local Ollama, which sends nothing out).
- Search queries sent by the self-hosted SearXNG to public search engines.

## Network exposure

- PostgreSQL publishes no port; only containers in the Compose network can reach it.
- The API, n8n and SearXNG are bound to 127.0.0.1.
- Job endpoints need the `X-Trigger-Secret` header and refuse every call when no secret is configured.

## Storage

- PostgreSQL keeps headlines, links and short excerpts. Raw article text is pruned after 30 days.

## Sentry (optional, not enabled)

If enabled later, a scrubber removes API keys, request headers and news text before an event is sent.
