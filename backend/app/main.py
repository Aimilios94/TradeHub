"""TradeHub API. Read-only for the UI; job triggers are separate and need a shared secret."""

from fastapi import FastAPI

from app.api import health, jobs, sources

app = FastAPI(title="TradeHub API", version="0.1.0")
app.include_router(health.router)
app.include_router(sources.router, prefix="/api")
app.include_router(jobs.router)
