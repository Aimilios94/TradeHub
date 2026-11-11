"""The recorder: the jobs that call sources and store what came back.

- news headlines -> PostgreSQL (news_items)
- daily and intraday bars, COT -> Parquet under data/live/
- every call's outcome -> source health

One failing ticker or source never stops the rest, and never produces a row. Each job returns
what was stored and every error; `ok` is false if anything failed.
"""

import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.types import Text

from app.data.store import TABLE_KEYS, last_value, live_path, merge_rows
from app.ingest.base import (
    BarSource,
    NewsHeadline,
    NewsSource,
    PositioningSource,
    SourceAdapter,
    TickerRef,
)
from app.ingest.cftc import SOURCE_KEY as CFTC_KEY
from app.ingest.health import enabled_source_ids, record_error, record_success, safe_error_text
from app.ingest.yahoo import INTRADAY_INTERVALS
from app.ingest.yahoo import SOURCE_KEY as YAHOO_KEY

NEWS_SOURCES = ("finnhub", "yahoo_rss")  # both are recorded, to build history
NEWS_LOOKBACK = timedelta(days=3)
EXCERPT_LENGTH = 500
# After the first fetch only the recent days are requested; a longer gap triggers a full refetch.
INTRADAY_REFRESH_DAYS = 7
PAUSE_SECONDS = 0.25  # between calls to the same provider

_TRACKING_PARAMS = {".tsrc", "tsrc", "guccounter", "guce_referrer", "guce_referrer_sig", "fbclid"}

_TICKERS = text(
    """
    SELECT t.id, t.asset_class, t.symbol, t.source_symbols
    FROM tickers t
    WHERE t.is_active AND (NOT :watchlist_only OR t.id IN (SELECT ticker_id FROM watchlist))
    ORDER BY t.id
    """
)
_INSERT_NEWS = text(
    """
    INSERT INTO news_items
        (source_id, title, url, published_at, ingested_at, dedupe_hash, excerpt, source_tickers)
    VALUES (:source_id, :title, :url, :published_at, :ingested_at, :dedupe_hash, :excerpt,
            :source_tickers)
    ON CONFLICT (dedupe_hash) DO UPDATE SET source_tickers = (
        SELECT array_agg(DISTINCT ticker ORDER BY ticker)
        FROM unnest(news_items.source_tickers || EXCLUDED.source_tickers) AS ticker
    )
    RETURNING (xmax = 0) AS inserted
    """
).bindparams(bindparam("source_tickers", type_=ARRAY(Text)))


def load_tickers(engine: Engine, watchlist_only: bool = False) -> list[TickerRef]:
    with engine.connect() as conn:
        rows = conn.execute(_TICKERS, {"watchlist_only": watchlist_only}).mappings().all()
    return [
        TickerRef(row["id"], row["asset_class"], row["symbol"], dict(row["source_symbols"]))
        for row in rows
    ]


def canonical_url(url: str) -> str:
    """The URL with tracking parameters and fragment removed, so one article has one hash."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def dedupe_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def store_headlines(
    engine: Engine, source_id: str, headlines: list[NewsHeadline], ingested_at: datetime
) -> int:
    """Insert headlines; an already-stored URL only gains the new ticker. Returns new rows."""
    inserted = 0
    with engine.begin() as conn:
        for item in headlines:
            row = {
                "source_id": source_id,
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at,
                "ingested_at": ingested_at,
                "dedupe_hash": dedupe_hash(item.url),
                "excerpt": item.excerpt[:EXCERPT_LENGTH] if item.excerpt else None,
                "source_tickers": sorted(item.ticker_ids),
            }
            inserted += bool(conn.execute(_INSERT_NEWS, row).scalar())
    return inserted


class _Outcome:
    """Collects one source's results inside a job, then writes its health row once."""

    def __init__(self, engine: Engine, source_id: str):
        self.engine = engine
        self.source_id = source_id
        self.calls_ok = 0
        self.errors: list[str] = []
        self.stats: dict = {}

    def add(self, key: str, amount: int) -> None:
        self.stats[key] = self.stats.get(key, 0) + amount

    def failed(self, error: BaseException) -> None:
        self.errors.append(safe_error_text(error))

    def finish(self) -> dict:
        if self.calls_ok:
            record_success(self.engine, self.source_id)
        if self.errors:
            summary = self.errors[0]
            if len(self.errors) > 1:
                summary = f"{len(self.errors)} errors, first: {summary}"
            record_error(self.engine, self.source_id, summary)
        return {
            "ok": not self.errors,
            "calls_ok": self.calls_ok,
            **self.stats,
            "errors": self.errors,
        }


def _result(sources: dict[str, dict], skipped: list[str]) -> dict:
    # A source the owner switched off is skipped, which is not a failure.
    return {
        "ok": all(source["ok"] for source in sources.values()),
        "sources": sources,
        "skipped_disabled": skipped,
    }


def _split_enabled(engine: Engine, source_ids: tuple[str, ...]) -> tuple[list[str], list[str]]:
    enabled = enabled_source_ids(engine)
    return (
        [source for source in source_ids if source in enabled],
        [source for source in source_ids if source not in enabled],
    )


def check_sources(engine: Engine, adapters: dict[str, SourceAdapter]) -> dict:
    """One real call per adapter; writes source health. Used to verify a source end to end."""
    active, skipped = _split_enabled(engine, tuple(adapters))
    sources = {}
    for source_id in active:
        outcome = _Outcome(engine, source_id)
        try:
            outcome.stats["detail"] = adapters[source_id].check()
            outcome.calls_ok += 1
        except Exception as exc:
            outcome.failed(exc)
        sources[source_id] = outcome.finish()
    return _result(sources, skipped)


def record_news(
    engine: Engine, adapters: dict[str, SourceAdapter], pause: float = PAUSE_SECONDS
) -> dict:
    """Headlines for every watchlist ticker from each news source."""
    now = datetime.now(UTC)
    tickers = load_tickers(engine, watchlist_only=True)
    active, skipped = _split_enabled(engine, NEWS_SOURCES)
    sources = {}
    for source_id in active:
        adapter = adapters[source_id]
        assert isinstance(adapter, NewsSource)
        outcome = _Outcome(engine, source_id)
        for ticker in tickers:
            try:
                headlines = adapter.fetch_news(ticker, since=now - NEWS_LOOKBACK)
                outcome.add("new_items", store_headlines(engine, source_id, headlines, now))
                outcome.add("items_seen", len(headlines))
                outcome.calls_ok += 1
            except Exception as exc:
                outcome.failed(exc)
            time.sleep(pause)
        sources[source_id] = outcome.finish()
    return _result(sources, skipped)


def _intraday_lookback(path: Path, interval: str, now: datetime) -> int:
    newest = last_value(path, "ts")
    max_days = INTRADAY_INTERVALS[interval][1]
    if newest is None or now - newest > timedelta(days=INTRADAY_REFRESH_DAYS - 2):
        return max_days
    return min(INTRADAY_REFRESH_DAYS, max_days)


def record_bars(
    engine: Engine,
    adapters: dict[str, SourceAdapter],
    data_dir: Path,
    intraday: bool = True,
    pause: float = PAUSE_SECONDS,
) -> dict:
    """Daily bars for every ticker with a Yahoo symbol; intraday bars for the watchlist."""
    source_id = "yahoo_finance"
    active, skipped = _split_enabled(engine, (source_id,))
    if not active:
        return _result({}, skipped)
    adapter = adapters[source_id]
    assert isinstance(adapter, BarSource)
    outcome = _Outcome(engine, source_id)
    now = datetime.now(UTC)

    for ticker in load_tickers(engine):
        if not ticker.symbol_for(YAHOO_KEY):
            continue
        try:
            bars = adapter.fetch_daily_bars(ticker)
            path = live_path(data_dir, "bars_daily", ticker.id)
            outcome.add(
                "daily_rows_added", merge_rows(path, bars, TABLE_KEYS["bars_daily"])["added"]
            )
            outcome.add("daily_symbols", 1)
            outcome.calls_ok += 1
        except Exception as exc:
            outcome.failed(exc)
        time.sleep(pause)

    watchlist = load_tickers(engine, watchlist_only=True) if intraday else []
    for ticker in watchlist:
        if not ticker.symbol_for(YAHOO_KEY):
            continue
        for interval in INTRADAY_INTERVALS:
            path = live_path(data_dir, "bars_intraday", ticker.id, interval)
            try:
                lookback = _intraday_lookback(path, interval, now)
                bars = adapter.fetch_intraday_bars(ticker, interval, lookback)
                merged = merge_rows(path, bars, TABLE_KEYS["bars_intraday"])
                outcome.add(f"intraday_{interval}_rows_added", merged["added"])
                outcome.calls_ok += 1
            except Exception as exc:
                outcome.failed(exc)
            time.sleep(pause)
    return _result({source_id: outcome.finish()}, skipped)


def record_cot(engine: Engine, adapters: dict[str, SourceAdapter], data_dir: Path) -> dict:
    """Full COT history for every ticker that has a CFTC market code."""
    source_id = "cftc_cot"
    active, skipped = _split_enabled(engine, (source_id,))
    if not active:
        return _result({}, skipped)
    adapter = adapters[source_id]
    assert isinstance(adapter, PositioningSource)
    outcome = _Outcome(engine, source_id)
    for ticker in load_tickers(engine):
        if not ticker.symbol_for(CFTC_KEY):
            continue
        try:
            reports = adapter.fetch_cot(ticker)
            path = live_path(data_dir, "cot", ticker.id)
            outcome.add("rows_added", merge_rows(path, reports, TABLE_KEYS["cot"])["added"])
            outcome.add("markets", 1)
            outcome.calls_ok += 1
        except Exception as exc:
            outcome.failed(exc)
    return _result({source_id: outcome.finish()}, skipped)
