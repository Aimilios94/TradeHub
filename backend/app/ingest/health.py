"""Source-health writer: keeps sources.last_success_at / last_error_at / last_error current.

Every job that calls a source reports the outcome here. A source is healthy when its last
success is newer than its last error; the UI shows both, with the real error text.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from app.config import get_settings
from app.ingest.base import SourceError, scrub

MAX_ERROR_LENGTH = 500

_SUCCESS = text("UPDATE sources SET last_success_at = :at WHERE id = :id")
_ERROR = text("UPDATE sources SET last_error_at = :at, last_error = :error WHERE id = :id")
_ENABLED = text("SELECT id FROM sources WHERE enabled")


def _configured_secrets() -> tuple[str, ...]:
    values = get_settings().model_dump()
    return tuple(
        str(value)
        for name, value in values.items()
        if value and name.endswith(("_api_key", "_secret", "_password"))
    )


def safe_error_text(error: BaseException | str) -> str:
    """The real error, with keys removed and cut to a storable length."""
    if isinstance(error, SourceError | str):
        message = str(error)
    else:
        message = f"{type(error).__name__}: {error}"
    return scrub(message, _configured_secrets())[:MAX_ERROR_LENGTH]


def _update(engine: Engine, statement, params: dict) -> None:
    with engine.begin() as conn:
        if conn.execute(statement, params).rowcount != 1:
            raise LookupError(f"Unknown source '{params['id']}': no row in the sources table")


def record_success(engine: Engine, source_id: str, at: datetime | None = None) -> None:
    _update(engine, _SUCCESS, {"id": source_id, "at": at or datetime.now(UTC)})


def record_error(
    engine: Engine, source_id: str, error: BaseException | str, at: datetime | None = None
) -> str:
    """Store the error and return the text that was stored."""
    message = safe_error_text(error)
    _update(engine, _ERROR, {"id": source_id, "at": at or datetime.now(UTC), "error": message})
    return message


@contextmanager
def track(engine: Engine, source_id: str) -> Iterator[None]:
    """Record success if the block finishes, the error if it raises. The error still propagates."""
    try:
        yield
    except Exception as exc:
        record_error(engine, source_id, exc)
        raise
    record_success(engine, source_id)


def enabled_source_ids(engine: Engine) -> set[str]:
    """Sources the owner has not switched off."""
    with engine.connect() as conn:
        return set(conn.execute(_ENABLED).scalars())
