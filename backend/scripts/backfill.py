"""Backfill the live recording area: full daily history, recent intraday bars and COT.

Usage (inside the api container):
    python -m scripts.backfill              # daily + intraday + COT
    python -m scripts.backfill --daily-only

Runs the same code as the scheduled recorder jobs, so it is safe to repeat.
"""

import argparse
import json
import sys

from app.config import get_settings
from app.db import get_engine
from app.ingest import recorder
from app.ingest.registry import build_adapters

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daily-only", action="store_true", help="skip intraday bars and COT")
    args = parser.parse_args()

    settings, engine = get_settings(), get_engine()
    adapters = build_adapters(settings)
    results = {
        "bars": recorder.record_bars(
            engine, adapters, settings.data_dir, intraday=not args.daily_only
        )
    }
    if not args.daily_only:
        results["cot"] = recorder.record_cot(engine, adapters, settings.data_dir)
    print(json.dumps(results, indent=2))
    sys.exit(0 if all(result["ok"] for result in results.values()) else 1)
