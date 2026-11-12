"""Ranking a day's headlines: importance first, then the newest, then the first stored.

Only shown items are ranked (duplicates are folded into the item they repeat). A day is the UTC
date an item was ingested.
"""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, text

_RANKED = text(
    """
    SELECT n.id, n.source_id, n.title, n.url, n.published_at, n.ingested_at,
           n.importance_score, n.importance_components,
           (SELECT count(*) FROM news_items d WHERE d.duplicate_of = n.id) AS duplicates,
           coalesce((SELECT json_agg(json_build_object(
                         'driver', e.driver_id, 'direction', e.direction,
                         'scheduled', e.scheduled, 'tagged_by', e.tagged_by)
                         ORDER BY e.driver_id)
                     FROM tagged_events e WHERE e.news_item_id = n.id), '[]'::json) AS tags,
           coalesce((SELECT json_agg(json_build_object(
                         'ticker', s.ticker_id, 'catalyst_type', s.catalyst_type,
                         'sentiment_score', s.sentiment_score, 'reason', s.one_sentence_reason,
                         'prompt_version', s.prompt_version)
                         ORDER BY s.ticker_id, s.prompt_version)
                     FROM news_sentiment s WHERE s.news_item_id = n.id), '[]'::json) AS sentiment
    FROM news_items n
    WHERE n.duplicate_of IS NULL AND n.processed_at IS NOT NULL
      AND n.ingested_at >= :start AND n.ingested_at < :end
    ORDER BY n.importance_score DESC NULLS LAST,
             coalesce(n.published_at, n.ingested_at) DESC,
             n.id
    LIMIT :limit
    """
)


def rank_day(engine: Engine, day: date, limit: int = 50) -> list[dict]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    params = {"start": start, "end": start + timedelta(days=1), "limit": limit}
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(_RANKED, params).mappings()]
