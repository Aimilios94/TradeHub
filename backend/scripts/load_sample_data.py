"""The golden day: a committed, synthetic day of headlines that must always rank and tag the same.

Usage (inside the api container, against a database you can afford to fill with demo rows):
    python -m scripts.load_sample_data            # store the golden day and run the pipeline
    python -m scripts.load_sample_data --expected # print what expected.json should hold

The headlines are stored through the recorder's own code and processed by the real pipeline.
They are marked is_demo, so they can never be mistaken for recorded news. The golden-day test
does the same against the throwaway test database and compares with expected.json.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, text

from app.config import REPO_ROOT
from app.ingest.base import NewsHeadline
from app.ingest.recorder import store_headlines
from app.scoring.pipeline import process_new_items
from app.scoring.ranking import rank_day

GOLDEN_DIR = REPO_ROOT / "sample-data" / "golden_day"


def read_golden(path: Path = GOLDEN_DIR / "news_items.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_golden_day(engine: Engine, golden: dict | None = None) -> dict:
    """Store the golden headlines in file order, mark them demo, and run the pipeline."""
    golden = golden or read_golden()
    ingested_at = datetime.fromisoformat(golden["ingested_at"])
    urls = []
    for item in golden["items"]:
        headline = NewsHeadline(
            title=item["title"],
            url=item["url"],
            published_at=datetime.fromisoformat(item["published_at"]),
            excerpt=item["excerpt"],
            ticker_ids=tuple(item["source_tickers"]),
        )
        store_headlines(engine, item["source_id"], [headline], ingested_at)
        urls.append(item["url"])
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE news_items SET is_demo = true WHERE url = ANY(:urls)"), {"urls": urls}
        )
    return process_new_items(engine, now=ingested_at)


def observed(engine: Engine, golden: dict | None = None) -> dict:
    """What the pipeline made of the golden day, keyed by URL so it does not depend on row ids."""
    golden = golden or read_golden()
    urls = [item["url"] for item in golden["items"]]
    with engine.connect() as conn:
        duplicates = {
            row["url"]: row["shown_url"]
            for row in conn.execute(
                text(
                    "SELECT d.url, s.url AS shown_url FROM news_items d "
                    "JOIN news_items s ON s.id = d.duplicate_of WHERE d.url = ANY(:urls) "
                    "ORDER BY d.url"
                ),
                {"urls": urls},
            ).mappings()
        }
    day = datetime.fromisoformat(golden["ingested_at"]).date()
    ranked = [row for row in rank_day(engine, day, limit=1000) if row["url"] in urls]
    return {
        "duplicates": duplicates,
        "tags": {
            row["url"]: [[tag["driver"], tag["direction"], tag["scheduled"]] for tag in row["tags"]]
            for row in sorted(ranked, key=lambda r: r["url"])
            if row["tags"]
        },
        "ranking": [
            {"url": row["url"], "score": float(row["importance_score"]), "title": row["title"]}
            for row in ranked
        ],
    }


if __name__ == "__main__":
    from app.db import get_engine

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expected", action="store_true", help="print the observed result")
    args = parser.parse_args()
    result = load_golden_day(get_engine())
    print(json.dumps(observed(get_engine()) if args.expected else result, indent=2))
