# Evals

Results and baselines for the Hermes evals and the per-strategy backtest write-ups, added from Phase 1.

- `RESULTS.md`, `MODELS.md` — condenser accuracy and the model comparison.
- `tagger/`, `numbers/` — tagger precision and recall, invented-number rate.
- `strategies/<name>/RESULTS.md` — one per strategy run, failures included.

Each eval has a committed baseline. `check_regression.py` compares a new run with it: real numbers locally, a deterministic mock in CI, so CI never calls an LLM. No LLM-as-judge.
