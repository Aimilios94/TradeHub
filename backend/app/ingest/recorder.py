"""The recorder: the jobs that call sources and store what came back.

- news headlines -> PostgreSQL (news_items)
- daily and intraday bars, COT -> Parquet under data/live/
- every call's outcome -> source health

One failing ticker or source never stops the rest, and never produces a row. Each job returns
what was stored and every error; `ok` is false if anything failed.
"""

import hashlib
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text

from app.data.store import TABLE_KEYS, last_value, live_path, merge_rows
from app.ingest.base import (
    BarSource,
    NewsHeadline,
    NewsSource,
    PositioningSource,
    SocialSource,
    SourceAdapter,
    SourceNetworkError,
    TickerRef,
)
from app.ingest.cftc import SOURCE_KEY as CFTC_KEY
from app.ingest.health import enabled_source_ids, record_error, record_success, safe_error_text
from app.ingest.yahoo import INTRADAY_INTERVALS
from app.ingest.yahoo import SOURCE_KEY as YAHOO_KEY

NEWS_SOURCES = ("finnhub", "yahoo_rss", "massive")  # all are recorded, to build history
NEWS_LOOKBACK = timedelta(days=3)
EXCERPT_LENGTH = 500
# After the first fetch only the recent days are requested; a longer gap triggers a full refetch.
INTRADAY_REFRESH_DAYS = 7
PAUSE_SECONDS = 0.25  # between calls to the same provider
SOCIAL_PAUSE_SECONDS = 1.0  # StockTwits is an unofficial endpoint: stay slow

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
        (source_id, title, url, published_at, ingested_at, dedupe_hash, excerpt, source_tickers,
         source_meta)
    VALUES (:source_id, :title, :url, :published_at, :ingested_at, :dedupe_hash, :excerpt,
            :source_tickers, :source_meta)
    ON CONFLICT (dedupe_hash) DO UPDATE SET source_tickers = (
        SELECT array_agg(DISTINCT ticker ORDER BY ticker)
        FROM unnest(news_items.source_tickers || EXCLUDED.source_tickers) AS ticker
    )
    RETURNING (xmax = 0) AS inserted
    """
).bindparams(bindparam("source_tickers", type_=ARRAY(Text)), bindparam("source_meta", type_=JSONB))


_SOCIAL_DONE = text(
    "SELECT ticker_id FROM social_sentiment WHERE day = :day AND source_id = :source_id"
)
_INSERT_SOCIAL = text(
    """
    INSERT INTO social_sentiment (ticker_id, day, source_id, bullish, bearish, messages, fetched_at)
    VALUES (:ticker_id, :day, :source_id, :bullish, :bearish, :messages, :fetched_at)
    ON CONFLICT (ticker_id, day, source_id) DO NOTHING
    """
)


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
                "source_meta": item.meta,
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
    engine: Engine,
    adapters: dict[str, SourceAdapter],
    pause: float = PAUSE_SECONDS,
    network_tries: int = 1,
    network_wait: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Headlines for every watchlist ticker from each news source.

    A call that got no answer because the network was down (SourceNetworkError) is tried again
    after `network_wait` seconds, up to `network_tries` tries in all (D60). A retry round stops
    at its first call that still gets no answer. What is still failing after the last try is
    reported with its real error, as before. Any other failure is never retried.
    """
    now = datetime.now(UTC)
    tickers = load_tickers(engine, watchlist_only=True)
    active, skipped = _split_enabled(engine, NEWS_SOURCES)
    outcomes = {source_id: _Outcome(engine, source_id) for source_id in active}

    def fetch(source_id: str, ticker: TickerRef, at: datetime) -> BaseException | None:
        """One call. Returns the error when the network gave no answer, else None."""
        adapter = adapters[source_id]
        assert isinstance(adapter, NewsSource)
        outcome = outcomes[source_id]
        try:
            headlines = adapter.fetch_news(ticker, since=at - NEWS_LOOKBACK)
            outcome.add("new_items", store_headlines(engine, source_id, headlines, at))
            outcome.add("items_seen", len(headlines))
            outcome.calls_ok += 1
        except SourceNetworkError as exc:
            return exc
        except Exception as exc:
            outcome.failed(exc)
        return None

    # No answer from the network so far: (source, ticker, the last error).
    waiting: list[tuple[str, TickerRef, BaseException]] = []
    for source_id in active:
        for ticker in tickers:
            error = fetch(source_id, ticker, now)
            if error is not None:
                waiting.append((source_id, ticker, error))
            time.sleep(pause)

    network_waits = 0
    for _ in range(max(network_tries, 1) - 1):
        if not waiting:
            break
        network_waits += 1
        sleep(network_wait)
        retried_at = datetime.now(UTC)  # a headline stored late carries the time it was stored
        still_waiting: list[tuple[str, TickerRef, BaseException]] = []
        for position, (source_id, ticker, _error) in enumerate(waiting):
            error = fetch(source_id, ticker, retried_at)
            if error is not None:
                # Still no network: keep this call and the rest for the next round.
                still_waiting = [(source_id, ticker, error), *waiting[position + 1 :]]
                break
            time.sleep(pause)
        waiting = still_waiting

    for source_id, _ticker, error in waiting:
        outcomes[source_id].failed(error)
    result = _result({source_id: outcomes[source_id].finish() for source_id in active}, skipped)
    result["network_waits"] = network_waits
    return result


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


def record_social(
    engine: Engine, adapters: dict[str, SourceAdapter], pause: float | None = None
) -> dict:
    """One StockTwits sample per watchlist ticker per UTC day: bullish and bearish counts.

    A ticker that already has today's sample is not fetched again. A failed fetch stores
    nothing, so that ticker's score stays news-only.
    """
    source_id = "stocktwits"
    active, skipped = _split_enabled(engine, (source_id,))
    if not active:
        return _result({}, skipped)
    adapter = adapters[source_id]
    assert isinstance(adapter, SocialSource)
    outcome = _Outcome(engine, source_id)
    pause = SOCIAL_PAUSE_SECONDS if pause is None else pause
    now = datetime.now(UTC)
    with engine.connect() as conn:
        done = set(
            conn.execute(_SOCIAL_DONE, {"day": now.date(), "source_id": source_id}).scalars()
        )
    for ticker in load_tickers(engine, watchlist_only=True):
        if ticker.id in done:
            outcome.add("already_sampled", 1)
            continue
        try:
            sample = adapter.fetch_sentiment(ticker)
            if sample is not None:
                with engine.begin() as conn:
                    conn.execute(
                        _INSERT_SOCIAL,
                        {
                            "ticker_id": sample.ticker_id,
                            "day": now.date(),
                            "source_id": source_id,
                            "bullish": sample.bullish,
                            "bearish": sample.bearish,
                            "messages": sample.messages,
                            "fetched_at": now,
                        },
                    )
                outcome.add("samples_stored", 1)
                outcome.add("labeled_messages", sample.bullish + sample.bearish)
                outcome.calls_ok += 1
        except Exception as exc:
            outcome.failed(exc)
        time.sleep(pause)
    return _result({source_id: outcome.finish()}, skipped)
