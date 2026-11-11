"""Source registry and health, for the Settings page."""

from fastapi import APIRouter
from sqlalchemy import text

from app.db import get_engine

router = APIRouter(tags=["sources"])

_QUERY = text(
    """
    SELECT id, name, kinds, role, status, enabled, priority, version_introduced,
           credentials_ref IS NOT NULL AS needs_key,
           last_success_at, last_error_at, last_error, last_verified_on, notes
    FROM sources
    ORDER BY version_introduced, id
    """
)


@router.get("/sources")
def list_sources() -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(row) for row in conn.execute(_QUERY).mappings()]
