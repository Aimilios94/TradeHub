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
