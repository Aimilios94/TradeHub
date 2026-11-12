"""The news pipeline job: everything that happens to headlines after the recorder stored them.

    process   dedupe, driver tags, importance            (code)
    condense  catalyst, sentiment, tickers, reason       (Hermes cheap tier, validated)
    social    StockTwits bullish/bearish counts          (additive, optional)
    score     composite sentiment per ticker per day     (code)

Each step runs even if an earlier one failed, and reports its own result with the real error.
The job is not ok when a required step failed. The social step is additive (D21): its failure
is reported and written to source health, but it does not fail the job.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from app.config import Settings
from app.hermes import condenser
from app.hermes.client import LlmClient
from app.ingest import recorder
from app.ingest.base import SourceAdapter
from app.ingest.health import safe_error_text
from app.scoring import composite
from app.scoring.pipeline import process_new_items

REQUIRED_STEPS = ("process", "condense", "score")


def _step(run: Callable[[], dict]) -> dict:
    try:
        return {"ok": True, **run()}
    except Exception as exc:
        return {"ok": False, "error": safe_error_text(exc)}


def _score(engine: Engine, now: datetime) -> dict:
    # Yesterday is scored again: items ingested late in the day are condensed on the next run.
    days = [now.date() - timedelta(days=1), now.date()]
    return {
        "rows": {
            day.isoformat(): composite.score_day(engine, day, condenser.PROMPT_VERSION, now)
            for day in days
        },
        "version": composite.VERSION,
    }


def process_news(
    engine: Engine,
    settings: Settings,
    adapters: dict[str, SourceAdapter],
    build_llm: Callable[[], LlmClient],
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    steps = {
        "process": _step(lambda: process_new_items(engine, now=now)),
        "condense": _step(
            lambda: condenser.condense_pending(
                engine,
                build_llm(),
                daily_cap=settings.condenser_daily_cap,
                run_cap=settings.condenser_run_cap,
                monthly_budget=settings.llm_monthly_budget_eur,
                lookback=timedelta(days=settings.condenser_lookback_days),
                now=now,
            )
        ),
        "social": _step(lambda: recorder.record_social(engine, adapters)),
        "score": _step(lambda: _score(engine, now)),
    }
    return {"ok": all(steps[name]["ok"] for name in REQUIRED_STEPS), "steps": steps}
