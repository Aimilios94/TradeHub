"""llm_traces writer and the counters read from it (calls today, cost this month).

Every Hermes call gets a row, valid or not. Nothing else in the app counts LLM usage: the daily
cap and the monthly budget are both read from this table.
"""

from datetime import UTC, datetime

from sqlalchemy import Connection, text

MAX_ERROR_LENGTH = 500
OUTCOMES = ("ok", "invalid_json", "schema_failed", "number_check_failed", "error", "capped")

_INSERT = text(
    """
    INSERT INTO llm_traces
        (template, prompt_version, tier, model, attempt, latency_ms, prompt_tokens,
         completion_tokens, cost_usd, outcome, error, news_item_id)
    VALUES (:template, :prompt_version, :tier, :model, :attempt, :latency_ms, :prompt_tokens,
            :completion_tokens, :cost_usd, :outcome, :error, :news_item_id)
    RETURNING id
    """
)
_CALLS_SINCE = text(
    "SELECT count(*) FROM llm_traces "
    "WHERE template = :template AND outcome <> 'capped' AND created_at >= :since"
)
_COST_SINCE = text("SELECT coalesce(sum(cost_usd), 0) FROM llm_traces WHERE created_at >= :since")


def write_trace(
    conn: Connection,
    *,
    template: str,
    prompt_version: str,
    tier: str,
    model: str,
    outcome: str,
    attempt: int = 1,
    latency_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_usd: float | None = None,
    error: str | None = None,
    news_item_id: int | None = None,
) -> int:
    row = {
        "template": template,
        "prompt_version": prompt_version,
        "tier": tier,
        "model": model,
        "attempt": attempt,
        "latency_ms": latency_ms,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": cost_usd,
        "outcome": outcome,
        "error": error[:MAX_ERROR_LENGTH] if error else None,
        "news_item_id": news_item_id,
    }
    return conn.execute(_INSERT, row).scalar_one()


def calls_today(conn: Connection, template: str, now: datetime | None = None) -> int:
    """Calls made for a template since 00:00 UTC. A 'capped' row is not a call."""
    now = now or datetime.now(UTC)
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return conn.execute(_CALLS_SINCE, {"template": template, "since": since}).scalar_one()


def cost_this_month_usd(conn: Connection, now: datetime | None = None) -> float:
    """Reported cost of every call since the 1st of the month, UTC, all templates."""
    now = now or datetime.now(UTC)
    since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(conn.execute(_COST_SINCE, {"since": since}).scalar_one())
