"""Read-only views of the news pipeline's output: a day's ranked headlines and daily scores."""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.db import get_engine
from app.scoring.ranking import rank_day

router = APIRouter(tags=["news"])

_SCORES = text(
    """
    SELECT ticker_id, score_date, composite_score, components, news_count, code_version,
           computed_at
    FROM daily_scores
    WHERE score_date = :day
    ORDER BY composite_score DESC, ticker_id
    """
)


def _today() -> date:
    return datetime.now(UTC).date()


@router.get("/news/top")
def top_news(day: date | None = None, limit: int = Query(default=20, ge=1, le=200)) -> dict:
    """Shown headlines ingested on a UTC day, most important first."""
    day = day or _today()
    return {"day": day, "items": rank_day(get_engine(), day, limit)}


@router.get("/scores")
def daily_scores(day: date | None = None) -> dict:
    """Composite sentiment score per ticker for a UTC day. A ticker with no input has no row."""
    day = day or _today()
    with get_engine().connect() as conn:
        rows = [dict(row) for row in conn.execute(_SCORES, {"day": day}).mappings()]
    return {"day": day, "scores": rows}
