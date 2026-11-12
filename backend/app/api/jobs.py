"""Job triggers for n8n. n8n only calls these endpoints; all logic lives in Python.

A job that had any failure answers 502 with the full result, so the n8n execution is marked
failed and the real errors are visible there as well as in source health.
"""

import secrets
import threading
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import pipeline
from app.config import get_settings
from app.db import get_engine
from app.hermes.client import build_cheap_client
from app.ingest import recorder
from app.ingest.base import new_http_client
from app.ingest.registry import build_adapters

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _selfcheck() -> dict:
    """Proves the n8n -> API -> database path works. Writes nothing."""
    with get_engine().connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            for table in ("sources", "tickers", "driver_taxonomy", "watchlist")
        }
    return {"row_counts": counts}


def _adapters():
    return build_adapters(get_settings())


def _check_sources() -> dict:
    return recorder.check_sources(get_engine(), _adapters())


def _record_news() -> dict:
    return recorder.record_news(get_engine(), _adapters())


def _record_bars() -> dict:
    return recorder.record_bars(get_engine(), _adapters(), get_settings().data_dir)


def _record_cot() -> dict:
    return recorder.record_cot(get_engine(), _adapters(), get_settings().data_dir)


def _process_news() -> dict:
    settings = get_settings()
    return pipeline.process_news(
        get_engine(),
        settings,
        _adapters(),
        lambda: build_cheap_client(settings, new_http_client()),
    )


JOBS: dict[str, Callable[[], dict]] = {
    "selfcheck": _selfcheck,
    "check_sources": _check_sources,
    "record_news": _record_news,
    "record_bars": _record_bars,
    "record_cot": _record_cot,
    "process_news": _process_news,
}
# One run of a job at a time: a second trigger while it is running is refused, not queued.
_LOCKS: dict[str, threading.Lock] = {name: threading.Lock() for name in JOBS}


@router.post("/{job_name}")
def run_job(job_name: str, x_trigger_secret: str = Header(default="")):
    expected = get_settings().n8n_trigger_secret
    if not expected or not secrets.compare_digest(x_trigger_secret, expected):
        raise HTTPException(status_code=401, detail="Missing or wrong X-Trigger-Secret header")
    job = JOBS.get(job_name)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job '{job_name}'")
    lock = _LOCKS[job_name]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=f"Job '{job_name}' is already running")
    try:
        result = job()
    finally:
        lock.release()
    body = {"job": job_name, "result": result}
    if result.get("ok") is False:
        return JSONResponse(status_code=502, content=body)
    return body
