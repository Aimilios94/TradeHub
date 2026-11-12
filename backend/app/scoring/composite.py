"""Composite sentiment score per ticker per day: -100 to +100, computed by code.

Inputs, each between -1 and +1:
    news     mean condenser sentiment of the day's shown headlines for the ticker, scaled by
             news volume: a single headline counts for less than five agreeing ones
    driver   the day's driver tags that have a direction, read through the sensitivity matrix:
             mean of (tag direction x expected direction for this ticker), scaled by the number
             of tagged headlines the same way: one tag counts for less than five agreeing ones
    social   StockTwits (bullish - bearish) / labeled, only at or above a minimum labeled count

    core  = 0.6 * news + 0.4 * driver     when both exist; otherwise the one that exists
    score = 100 * core + 20 * social      limited to -100 .. +100

Social is additive only (D21): without it the score is the news-and-driver core. A ticker with
no input at all for a day gets no row. The weights are starting hypotheses (DECISIONS D47,
D56), checked against next-day and 5-day returns once 60 days are recorded.

v1 did not scale the driver input, so a ticker with no headlines got the full -100 or +100
from a single tag. v2 scales it (D56). Rows keep the version that computed them.

A day is a UTC date of `ingested_at`, the point-in-time the plan requires for backtests.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

VERSION = "composite-v2"
NEWS_WEIGHT = 0.6
DRIVER_WEIGHT = 0.4
SOCIAL_POINTS = 20.0
FULL_VOLUME_AT = 5  # headlines (news) or tagged headlines (driver) that give full weight
MIN_SOCIAL_LABELED = 10  # labeled messages below which the social sample is ignored

_NEWS = text(
    """
    SELECT s.ticker_id, avg(s.sentiment_score)::float AS mean, count(*) AS items
    FROM news_sentiment s
    JOIN news_items n ON n.id = s.news_item_id
    WHERE n.duplicate_of IS NULL AND NOT n.is_demo AND s.prompt_version = :prompt_version
      AND n.ingested_at >= :start AND n.ingested_at < :end
    GROUP BY s.ticker_id
    """
)
_DRIVERS = text(
    """
    SELECT d.ticker_id,
           avg(e.direction * d.expected_direction)::float AS mean,
           count(DISTINCT e.id) AS events
    FROM tagged_events e
    JOIN news_items n ON n.id = e.news_item_id
    JOIN driver_sensitivities d ON d.driver_id = e.driver_id
    WHERE n.duplicate_of IS NULL AND NOT n.is_demo
      AND e.direction <> 0 AND d.expected_direction <> 0 AND d.status <> 'rejected'
      AND n.ingested_at >= :start AND n.ingested_at < :end
    GROUP BY d.ticker_id
    """
)
_SOCIAL = text(
    "SELECT ticker_id, sum(bullish) AS bullish, sum(bearish) AS bearish "
    "FROM social_sentiment WHERE day = :day GROUP BY ticker_id"
)
_WATCHLIST = text("SELECT ticker_id FROM watchlist ORDER BY ticker_id")
_UPSERT = text(
    """
    INSERT INTO daily_scores
        (ticker_id, score_date, composite_score, components, news_count, code_version,
         computed_at)
    VALUES (:ticker_id, :score_date, :composite_score, :components, :news_count, :code_version,
            :computed_at)
    ON CONFLICT (ticker_id, score_date) DO UPDATE SET
        composite_score = EXCLUDED.composite_score,
        components = EXCLUDED.components,
        news_count = EXCLUDED.news_count,
        code_version = EXCLUDED.code_version,
        computed_at = EXCLUDED.computed_at
    """
).bindparams(bindparam("components", type_=JSONB))


@dataclass(frozen=True)
class Composite:
    score: float
    components: dict


def composite_score(
    *,
    news_mean: float | None,
    news_items: int,
    driver_mean: float | None,
    driver_events: int = 0,
    bullish: int = 0,
    bearish: int = 0,
) -> Composite | None:
    """The score from its inputs, or None when there is nothing to score."""
    news = None
    if news_mean is not None and news_items > 0:
        news = news_mean * min(news_items, FULL_VOLUME_AT) / FULL_VOLUME_AT
    driver = None
    if driver_mean is not None and driver_events > 0:
        driver = driver_mean * min(driver_events, FULL_VOLUME_AT) / FULL_VOLUME_AT
    labeled = bullish + bearish
    social = (bullish - bearish) / labeled if labeled >= MIN_SOCIAL_LABELED else None

    if news is not None and driver is not None:
        core = NEWS_WEIGHT * news + DRIVER_WEIGHT * driver
    elif news is not None:
        core = news
    elif driver is not None:
        core = driver
    elif social is None:
        return None
    else:
        core = 0.0

    raw = 100 * core + SOCIAL_POINTS * (social or 0.0)
    components = {
        "version": VERSION,
        "news": None if news is None else round(news, 4),
        "news_mean": None if news_mean is None else round(news_mean, 4),
        "news_items": news_items,
        "driver": None if driver is None else round(driver, 4),
        "driver_mean": None if driver_mean is None else round(driver_mean, 4),
        "driver_events": driver_events,
        "social": None if social is None else round(social, 4),
        "social_bullish": bullish,
        "social_bearish": bearish,
    }
    return Composite(round(min(max(raw, -100.0), 100.0), 2), components)


def score_day(engine: Engine, day: date, prompt_version: str, now: datetime | None = None) -> int:
    """Compute and store the score of every watchlist ticker for one UTC day. Returns rows."""
    now = now or datetime.now(UTC)
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    window = {"start": start, "end": start + timedelta(days=1)}
    with engine.begin() as conn:
        news = {
            row["ticker_id"]: row
            for row in conn.execute(_NEWS, {**window, "prompt_version": prompt_version}).mappings()
        }
        drivers = {row["ticker_id"]: row for row in conn.execute(_DRIVERS, window).mappings()}
        social = {row["ticker_id"]: row for row in conn.execute(_SOCIAL, {"day": day}).mappings()}
        rows = []
        for ticker_id in conn.execute(_WATCHLIST).scalars():
            n, d, s = news.get(ticker_id), drivers.get(ticker_id), social.get(ticker_id)
            result = composite_score(
                news_mean=n["mean"] if n else None,
                news_items=n["items"] if n else 0,
                driver_mean=d["mean"] if d else None,
                driver_events=d["events"] if d else 0,
                bullish=s["bullish"] if s else 0,
                bearish=s["bearish"] if s else 0,
            )
            if result is None:
                continue
            rows.append(
                {
                    "ticker_id": ticker_id,
                    "score_date": day,
                    "composite_score": result.score,
                    "components": result.components,
                    "news_count": n["items"] if n else 0,
                    "code_version": VERSION,
                    "computed_at": now,
                }
            )
        if rows:
            conn.execute(_UPSERT, rows)
    return len(rows)
