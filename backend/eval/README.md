# Evals

Hand labels, results and baselines for the Hermes evals, and the per-strategy backtest write-ups. Nothing has been measured yet: the eval scripts and baselines are Phase 4.

## Labels

The condenser and tagger evals use 100 headlines labeled by hand by the owner. No model output is shown while labeling and none is ever a label.

| Where | What | In git |
|---|---|---|
| `data/eval/labels.json` | The working file: the 100 headlines with their text, and the labels as they are made | No (`data/` is ignored) |
| `backend/eval/labels/labels.json` | The export: item id, URL hash, stratum and label. No news text | Yes, once labels exist |

```sh
docker compose exec api python -m scripts.label_set status
docker compose exec api python -m scripts.label_set label      # in a terminal; stop and resume freely
docker compose exec -T api python -m scripts.label_set export > backend/eval/labels/labels.json
```

Each item is labeled with: sentiment direction for the tickers it is about (positive, negative, neutral), catalyst type (the 9-item menu), the tickers it is really about, the driver tags with a direction each, and whether the event was known in advance.

The set has two parts, recorded on every item as `stratum`: 60 headlines drawn at random and 40 drawn from headlines containing a macro word, because plain company news almost never carries a driver tag. Results are reported per stratum. The macro part is not representative of the news flow, so the two must not be merged into one accuracy figure (D54).

## To come (Phase 4)

- `RESULTS.md`, `MODELS.md`: condenser accuracy and the model comparison.
- `tagger/`, `numbers/`: tagger precision and recall, invented-number rate.
- `strategies/<name>/RESULTS.md`: one per strategy run, failures included.

Each eval gets a committed baseline. `check_regression.py` compares a new run with it: real numbers locally, a deterministic mock in CI, so CI never calls an LLM. No LLM-as-judge.
