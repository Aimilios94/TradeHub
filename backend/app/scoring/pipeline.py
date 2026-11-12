"""The code half of the news pipeline: dedupe, driver tags, importance. No LLM here.

`process_new_items` takes every stored headline not yet processed, marks duplicates, tags the
ones that are shown, and refreshes the importance score of recent shown items (a story gains
importance as more copies of it arrive). It only reads and updates rows the recorder stored; it
never creates a headline.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text

from app.scoring import importance
from app.scoring.dedupe import WINDOW, DedupeItem, assign_duplicates
from app.tagger.rules import Tagger, default_tagger

RESCORE_WINDOW = timedelta(days=7)

_UNPROCESSED = text(
    """
    SELECT id, title, coalesce(published_at, ingested_at) AS at
    FROM news_items
    WHERE processed_at IS NULL
    ORDER BY id
    """
)
_SHOWN = text(
    """
    SELECT id, title, coalesce(published_at, ingested_at) AS at
    FROM news_items
    WHERE processed_at IS NOT NULL AND duplicate_of IS NULL
      AND coalesce(published_at, ingested_at) BETWEEN :start AND :end
    ORDER BY id
    """
)
_MARK = text(
    "UPDATE news_items SET duplicate_of = :duplicate_of, processed_at = :now WHERE id = :id"
)
_SENSITIVE_ASSETS = text(
    """
    SELECT driver_id, array_agg(DISTINCT ticker_id ORDER BY ticker_id) AS assets
    FROM driver_sensitivities
    WHERE status <> 'rejected'
    GROUP BY driver_id
    """
)
_UPSERT_TAG = text(
    """
    INSERT INTO tagged_events
        (news_item_id, driver_id, direction, scheduled, affected_assets, tagged_by, event_time,
         tagger_version, matched_rules)
    VALUES (:news_item_id, :driver_id, :direction, :scheduled, :affected_assets, 'code',
            :event_time, :tagger_version, :matched_rules)
    ON CONFLICT (news_item_id, driver_id) WHERE news_item_id IS NOT NULL DO UPDATE SET
        direction = EXCLUDED.direction,
        scheduled = EXCLUDED.scheduled,
        affected_assets = EXCLUDED.affected_assets,
        tagger_version = EXCLUDED.tagger_version,
        matched_rules = EXCLUDED.matched_rules
    WHERE tagged_events.tagged_by = 'code'
    """
).bindparams(
    bindparam("affected_assets", type_=ARRAY(Text)),
    bindparam("matched_rules", type_=ARRAY(Text)),
)
# One row per shown item: its copies, the tickers of the whole group, and its tag directions.
_TO_SCORE = text(
    """
    WITH grouped AS (
        SELECT coalesce(n.duplicate_of, n.id) AS shown_id,
               array_agg(DISTINCT ticker) FILTER (WHERE ticker IS NOT NULL) AS tickers
        FROM news_items n
        LEFT JOIN LATERAL unnest(n.source_tickers) AS ticker ON true
        WHERE n.processed_at IS NOT NULL
        GROUP BY 1
    ),
    copies AS (
        SELECT coalesce(duplicate_of, id) AS shown_id, count(*) AS copies
        FROM news_items WHERE processed_at IS NOT NULL GROUP BY 1
    )
    SELECT n.id, n.title, c.copies, coalesce(g.tickers, '{}') AS tickers,
           coalesce((SELECT array_agg(e.direction ORDER BY e.driver_id)
                     FROM tagged_events e WHERE e.news_item_id = n.id), '{}') AS tag_directions
    FROM news_items n
    JOIN grouped g ON g.shown_id = n.id
    JOIN copies c ON c.shown_id = n.id
    WHERE n.duplicate_of IS NULL AND n.processed_at IS NOT NULL
      AND coalesce(n.published_at, n.ingested_at) >= :since
    ORDER BY n.id
    """
)
_TICKER_NAMES = text(
    """
    SELECT t.id, t.symbol, t.aliases, (w.ticker_id IS NOT NULL) AS on_watchlist
    FROM tickers t LEFT JOIN watchlist w ON w.ticker_id = t.id
    WHERE t.asset_class IN ('EQ', 'ETF')
    """
)
_SET_SCORE = text(
    "UPDATE news_items SET importance_score = :score, importance_components = :components "
    "WHERE id = :id"
).bindparams(bindparam("components", type_=JSONB))


def _dedupe(conn: Connection, now: datetime) -> tuple[list[int], int]:
    """Mark duplicates among unprocessed items. Returns (ids now shown, duplicates found)."""
    rows = conn.execute(_UNPROCESSED).mappings().all()
    if not rows:
        return [], 0
    new = [DedupeItem(row["id"], row["title"], row["at"]) for row in rows]
    times = [item.at for item in new]
    shown_rows = conn.execute(
        _SHOWN, {"start": min(times) - WINDOW, "end": max(times) + WINDOW}
    ).mappings()
    shown = [DedupeItem(row["id"], row["title"], row["at"]) for row in shown_rows]
    assigned = assign_duplicates(new, shown)
    conn.execute(
        _MARK,
        [{"id": item_id, "duplicate_of": dup, "now": now} for item_id, dup in assigned.items()],
    )
    kept = [item_id for item_id, dup in assigned.items() if dup is None]
    return kept, len(assigned) - len(kept)


def _tag(conn: Connection, tagger: Tagger, item_ids: list[int]) -> int:
    """Tag the given shown items. Returns the number of tags written."""
    if not item_ids:
        return 0
    assets = {row["driver_id"]: row["assets"] for row in conn.execute(_SENSITIVE_ASSETS).mappings()}
    items = conn.execute(
        text(
            "SELECT id, title, coalesce(published_at, ingested_at) AS at FROM news_items "
            "WHERE id = ANY(:ids) ORDER BY id"
        ),
        {"ids": item_ids},
    ).mappings()
    rows = [
        {
            "news_item_id": item["id"],
            "driver_id": tag.driver,
            "direction": tag.direction,
            "scheduled": tag.scheduled,
            "affected_assets": assets.get(tag.driver, []),
            "event_time": item["at"],
            "tagger_version": tagger.version,
            "matched_rules": list(tag.rules),
        }
        for item in items
        for tag in tagger.tag(item["title"])
    ]
    if rows:
        conn.execute(_UPSERT_TAG, rows)
    return len(rows)


def _score(conn: Connection, since: datetime) -> int:
    """Refresh importance for shown items published since `since`. Returns how many."""
    names: dict[str, list[str]] = {}
    watchlist: set[str] = set()
    for row in conn.execute(_TICKER_NAMES).mappings():
        names[row["id"]] = [row["symbol"], *row["aliases"]]
        if row["on_watchlist"]:
            watchlist.add(row["id"])
    updates = []
    for row in conn.execute(_TO_SCORE, {"since": since}).mappings():
        tickers = row["tickers"]
        result = importance.score_item(
            row["title"],
            copies=row["copies"],
            watchlist_tickers=len(watchlist.intersection(tickers)),
            ticker_names=[name for ticker in tickers for name in names.get(ticker, [])],
            tag_directions=list(row["tag_directions"]),
        )
        updates.append({"id": row["id"], "score": result.score, "components": result.components})
    if updates:
        conn.execute(_SET_SCORE, updates)
    return len(updates)


def process_new_items(
    engine: Engine, tagger: Tagger | None = None, now: datetime | None = None
) -> dict:
    """Dedupe, tag and score. One transaction: a failure leaves every item unprocessed."""
    now = now or datetime.now(UTC)
    tagger = tagger or default_tagger()
    with engine.begin() as conn:
        kept, duplicates = _dedupe(conn, now)
        tags = _tag(conn, tagger, kept)
        scored = _score(conn, now - RESCORE_WINDOW)
    new_items = len(kept) + duplicates
    return {
        "new_items": new_items,
        "duplicates": duplicates,
        "duplicate_rate": round(duplicates / new_items, 4) if new_items else None,
        "tags_written": tags,
        "items_scored": scored,
        "tagger_version": tagger.version,
        "importance_version": importance.VERSION,
    }
