"""The hand-labeled set for the condenser and tagger evals: draw it, label it, export it.

Labels are the owner's own judgment. No model output is shown while labeling and none is ever
used as a label (D10, D42).

Run inside the api container:
    python -m scripts.label_set sample          # draw 100 items into data/eval/labels.json
    python -m scripts.label_set label           # label in the terminal; safe to stop and resume
    python -m scripts.label_set status          # how many are done
    python -m scripts.label_set export          # labels with IDs only, no news text (stdout)

From the host, `label` needs a terminal:  docker compose exec api python -m scripts.label_set label
To commit the labels:  docker compose exec -T api python -m scripts.label_set export
                           > backend/eval/labels/labels.json

The working file holds real headlines and stays in data/ (git-ignored). The export holds the
item id, its URL hash and the labels: nothing copyrighted.

The sample has two parts, recorded per item as `stratum`:
    random   drawn evenly from all shown headlines
    macro    drawn from headlines whose title or excerpt uses a macro word (a list wider than
             the tagger's rules), because plain company news almost never carries a driver tag
             and the tagger eval needs positives
Report eval results per stratum: the macro part is not representative of the news flow.
"""

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.hermes.schemas import CATALYST_TYPES
from app.tagger.rules import DRIVER_IDS

SEED = 20261112
RANDOM_PART = 60
MACRO_PART = 40
MACRO_WORDS = (
    "fed|fomc|powell|inflation|cpi|ppi|rate|rates|yield|yields|treasury|recession|shutdown|"
    "debt ceiling|tariff|tariffs|sanction|sanctions|war|ceasefire|opec|crude|inventories|"
    "dollar|dxy|gold|positioning|short interest|margin call|liquidation|bank run|contagion"
)

_POOL = text(
    """
    SELECT n.id, n.dedupe_hash, n.source_id, n.title, n.excerpt, n.url, n.published_at,
           (SELECT array_agg(DISTINCT ticker ORDER BY ticker)
            FROM news_items m, unnest(m.source_tickers) AS ticker
            WHERE m.id = n.id OR m.duplicate_of = n.id) AS source_tickers,
           (lower(n.title || ' ' || coalesce(n.excerpt, '')) ~ :macro) AS is_macro
    FROM news_items n
    WHERE n.duplicate_of IS NULL AND n.processed_at IS NOT NULL AND NOT n.is_demo
    ORDER BY n.id
    """
)


def labels_path() -> Path:
    return get_settings().data_dir / "eval" / "labels.json"


def load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"{path} does not exist. Run: python -m scripts.label_set sample")
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def draw(pool: list[dict], random_part: int = RANDOM_PART, macro_part: int = MACRO_PART) -> list:
    """A reproducible sample: the same pool and seed always give the same items."""
    rng = random.Random(SEED)
    macro = rng.sample([row for row in pool if row["is_macro"]], k=macro_part)
    taken = {row["id"] for row in macro}
    rest = rng.sample([row for row in pool if row["id"] not in taken], k=random_part)
    chosen = [(row, "macro") for row in macro] + [(row, "random") for row in rest]
    rng.shuffle(chosen)
    return chosen


def sample(path: Path, force: bool) -> None:
    if path.exists() and not force:
        sys.exit(f"{path} already exists. Labels in it would be lost; use --force to replace it.")
    with get_engine().connect() as conn:
        pool = [
            dict(row) for row in conn.execute(_POOL, {"macro": rf"\m({MACRO_WORDS})\M"}).mappings()
        ]
    macro_available = sum(row["is_macro"] for row in pool)
    if macro_available < MACRO_PART or len(pool) < RANDOM_PART + MACRO_PART:
        sys.exit(
            f"Not enough headlines yet: {len(pool)} shown, {macro_available} with a macro word."
        )
    items = [
        {
            "id": row["id"],
            "dedupe_hash": row["dedupe_hash"],
            "stratum": stratum,
            "source_id": row["source_id"],
            "published_at": row["published_at"].isoformat() if row["published_at"] else None,
            "title": row["title"],
            "excerpt": row["excerpt"],
            "url": row["url"],
            "source_tickers": row["source_tickers"] or [],
            "label": None,
        }
        for row, stratum in draw(pool)
    ]
    document = {
        "created_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "pool_size": len(pool),
        "pool_macro": macro_available,
        "items": items,
    }
    save(path, document)
    print(f"Drew {len(items)} items ({MACRO_PART} macro, {RANDOM_PART} random) into {path}")


def _ask(prompt: str, valid) -> str:
    while True:
        answer = input(prompt).strip()
        if answer in ("q", "s") or valid(answer):
            return answer
        print("   not understood, try again (s = skip this item, q = save and quit)")


def _parse_drivers(answer: str) -> list[dict] | None:
    """'2+ 6-' -> monetary +1, dollar -1. '0' or empty -> no driver."""
    if answer in ("", "0"):
        return []
    drivers = []
    for part in answer.split():
        if len(part) < 2 or not part[:-1].isdigit() or part[-1] not in "+-0":
            return None
        number = int(part[:-1])
        if not 1 <= number <= len(DRIVER_IDS):
            return None
        direction = {"+": 1, "-": -1, "0": 0}[part[-1]]
        if DRIVER_IDS[number - 1] == "liquidity_flush" and direction == -1:
            return None
        drivers.append({"driver": DRIVER_IDS[number - 1], "direction": direction})
    return drivers


def label(path: Path) -> None:
    document = load(path)
    with get_engine().connect() as conn:
        known = {
            row[0]
            for row in conn.execute(
                text("SELECT symbol FROM tickers WHERE asset_class IN ('EQ', 'ETF')")
            )
        }
    catalysts = "  ".join(f"{n}={name}" for n, name in enumerate(CATALYST_TYPES, 1))
    drivers = "  ".join(f"{n}={name}" for n, name in enumerate(DRIVER_IDS, 1))
    todo = [item for item in document["items"] if item["label"] is None]
    print(f"{len(document['items']) - len(todo)} labeled, {len(todo)} to go. s = skip, q = quit.\n")
    for item in todo:
        symbols = [ticker.split(":")[1] for ticker in item["source_tickers"]]
        print("=" * 100)
        print(item["title"])
        print(f"\n{item['excerpt'] or '(no excerpt)'}")
        print(f"\nsource: {item['source_id']}   published: {item['published_at']}")
        print(f"attached tickers: {', '.join(symbols) or 'none'}\n")

        sentiment = _ask(
            "sentiment for the tickers it is about  [p]ositive / [n]egative / [0] neutral: ",
            lambda a: a in ("p", "n", "0"),
        )
        if sentiment == "q":
            break
        if sentiment == "s":
            continue
        print(f"   {catalysts}")
        catalyst = _ask("catalyst type (number): ", lambda a: a.isdigit() and 1 <= int(a) <= 9)
        if catalyst == "q":
            break
        if catalyst == "s":
            continue
        default = " ".join(symbols) or "none"
        tickers = _ask(
            f"tickers it is really about, with spaces [Enter = {default}, - = none]: ",
            lambda a: a in ("", "-") or all(part.upper() in known for part in a.split()),
        )
        if tickers == "q":
            break
        if tickers == "s":
            continue
        print(f"   {drivers}")
        tagged = _ask(
            "drivers, number then + - or 0, e.g. '2+ 6-'  [Enter = none]: ",
            lambda a: _parse_drivers(a) is not None,
        )
        if tagged == "q":
            break
        if tagged == "s":
            continue
        chosen = _parse_drivers(tagged)
        scheduled = False
        if chosen:
            known_ahead = _ask(
                "known in advance (scheduled event)? [y/n]: ", lambda a: a in ("y", "n")
            )
            if known_ahead == "q":
                break
            if known_ahead == "s":
                continue
            scheduled = known_ahead == "y"

        item["label"] = {
            "sentiment_direction": {"p": 1, "n": -1, "0": 0}[sentiment],
            "catalyst_type": CATALYST_TYPES[int(catalyst) - 1],
            "tickers": (
                []
                if tickers == "-"
                else sorted(part.upper() for part in tickers.split())
                if tickers
                else sorted(symbols)
            ),
            "drivers": chosen,
            "scheduled": scheduled,
            "labeled_at": datetime.now(UTC).isoformat(),
        }
        save(path, document)
        print("   saved\n")
    status(path)


def status(path: Path) -> None:
    items = load(path)["items"]
    done = [item for item in items if item["label"] is not None]
    by_stratum = {}
    for stratum in ("random", "macro"):
        labeled = sum(item["stratum"] == stratum for item in done)
        by_stratum[stratum] = f"{labeled}/{sum(item['stratum'] == stratum for item in items)}"
    print(f"{len(done)} of {len(items)} labeled  {by_stratum}")


def export(path: Path) -> None:
    document = load(path)
    out = {
        "note": "Hand labels by the owner. IDs and URL hashes only: no news text.",
        "seed": document["seed"],
        "items": [
            {
                "id": item["id"],
                "dedupe_hash": item["dedupe_hash"],
                "stratum": item["stratum"],
                "label": item["label"],
            }
            for item in document["items"]
            if item["label"] is not None
        ],
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("sample", "label", "status", "export"))
    parser.add_argument("--force", action="store_true", help="sample: replace an existing file")
    args = parser.parse_args()
    target = labels_path()
    if args.command == "sample":
        sample(target, args.force)
    elif args.command == "label":
        label(target)
    elif args.command == "status":
        status(target)
    else:
        export(target)
