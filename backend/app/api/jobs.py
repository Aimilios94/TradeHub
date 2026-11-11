"""Job triggers for n8n. n8n only calls these endpoints; all logic lives in Python."""

import secrets
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _selfcheck() -> dict:
    """Proves the n8n -> API -> database path works. Writes nothing."""
    with get_engine().connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            for table in ("sources", "tickers", "driver_taxonomy", "watchlist")
        }
    return {"row_counts": counts}


JOBS: dict[str, Callable[[], dict]] = {"selfcheck": _selfcheck}


@router.post("/{job_name}")
def run_job(job_name: str, x_trigger_secret: str = Header(default="")) -> dict:
    expected = get_settings().n8n_trigger_secret
    if not expected or not secrets.compare_digest(x_trigger_secret, expected):
        raise HTTPException(status_code=401, detail="Missing or wrong X-Trigger-Secret header")
    job = JOBS.get(job_name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_name}'")
    return {"job": job_name, "result": job()}
