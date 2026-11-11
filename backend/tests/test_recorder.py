"""Recorder jobs and the source-health writer, with fake adapters and a real PostgreSQL.

Includes the failure-injection cases: a source that is down gives the real error in source
health and zero fabricated rows.
"""

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api import jobs
from app.config import get_settings
from app.data.store import live_path, merge_rows
from app.ingest import health, recorder
from app.ingest.base import (
    COT_SCHEMA,
    DAILY_SCHEMA,
    INTRADAY_SCHEMA,
    NewsHeadline,
    SourceAdapter,
    SourceError,
)
from app.main import app
from app.seeds import load_seeds

NOW = datetime.now(UTC)


@pytest.fixture
def engine(migrated_engine, seeds_dir):
    load_seeds(migrated_engine, seeds_dir)
    with migrated_engine.begin() as conn:
        conn.execute(text("DELETE FROM news_items"))
        conn.execute(
            text(
                "UPDATE sources SET last_success_at = NULL, last_error_at = NULL, last_error = NULL"
            )
        )
        conn.execute(text("UPDATE sources SET enabled = true"))
    return migrated_engine


def source_row(engine, source_id: str) -> dict:
    query = text("SELECT last_success_at, last_error_at, last_error FROM sources WHERE id = :id")
    with engine.connect() as conn:
        return dict(conn.execute(query, {"id": source_id}).mappings().one())


def news_rows(engine) -> list[dict]:
    query = text(
        "SELECT source_id, title, url, source_tickers, excerpt FROM news_items ORDER BY id"
    )
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(query).mappings()]


class FakeNews(SourceAdapter):
    def __init__(self, source_id, items_by_ticker=None, fail_for=()):
        self.source_id = source_id
        self.items_by_ticker = items_by_ticker or {}
        self.fail_for = fail_for

    def check(self) -> str:
        return "fake ok"

    def fetch_news(self, ticker, since):
        if self.fail_for == "all" or ticker.id in self.fail_for:
            raise SourceError(f"{self.source_id} {ticker.symbol}: HTTP 503: upstream down")
        return [
            NewsHeadline(title, url, NOW, "excerpt " * 200, (ticker.id,))
            for title, url in self.items_by_ticker.get(ticker.id, [])
        ]


class FakeBars(SourceAdapter):
    source_id = "yahoo_finance"

    def __init__(self, fail_for=()):
        self.fail_for = fail_for
        self.intraday_calls = []

    def check(self) -> str:
        return "fake ok"

    def fetch_daily_bars(self, ticker, start=None):
        if self.fail_for == "all" or ticker.id in self.fail_for:
            raise SourceError(f"Yahoo {ticker.symbol}: no rows returned")
        rows = [
            {
                "ticker_id": ticker.id,
                "date": date(2026, 11, day),
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "adj_close": 1.4,
                "volume": 100,
                "dividends": 0.0,
                "stock_splits": 0.0,
                "source_id": self.source_id,
            }
            for day in (9, 10)
        ]
        return pl.DataFrame(rows, schema=DAILY_SCHEMA)

    def fetch_intraday_bars(self, ticker, interval, lookback_days):
        self.intraday_calls.append((ticker.id, interval, lookback_days))
        if self.fail_for == "all":
            raise SourceError(f"Yahoo {ticker.symbol}: no rows returned")
        row = {
            "ticker_id": ticker.id,
            "interval": interval,
            "ts": NOW.replace(microsecond=0) - timedelta(hours=2),
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 100,
            "source_id": self.source_id,
        }
        return pl.DataFrame([row], schema=INTRADAY_SCHEMA)


class FakeCot(SourceAdapter):
    source_id = "cftc_cot"

    def check(self) -> str:
        return "fake ok"

    def fetch_cot(self, ticker):
        row = dict.fromkeys(COT_SCHEMA, 1) | {
            "ticker_id": ticker.id,
            "market_code": "088691",
            "report_date": date(2026, 11, 3),
            "market_name": "GOLD",
            "source_id": self.source_id,
        }
        return pl.DataFrame([row], schema=COT_SCHEMA)


# --- health writer --------------------------------------------------------------------------


def test_health_writer_records_success_and_error_separately(engine):
    health.record_success(engine, "fred")
    row = source_row(engine, "fred")
    assert row["last_success_at"] is not None and row["last_error"] is None
    stored = health.record_error(engine, "fred", SourceError("FRED: HTTP 500: boom"))
    row = source_row(engine, "fred")
    assert stored == row["last_error"] == "FRED: HTTP 500: boom"
    assert row["last_error_at"] >= row["last_success_at"]


def test_health_writer_never_stores_a_key(engine, monkeypatch):
    monkeypatch.setattr(get_settings(), "finnhub_api_key", "sk-very-secret-key")
    error = RuntimeError("GET /news?token=sk-very-secret-key failed, also sk-very-secret-key")
    health.record_error(engine, "finnhub", error)
    stored = source_row(engine, "finnhub")["last_error"]
    assert "sk-very-secret-key" not in stored
    assert stored.startswith("RuntimeError: GET /news?token=***")


def test_health_writer_rejects_an_unknown_source(engine):
    with pytest.raises(LookupError, match="no_such_source"):
        health.record_success(engine, "no_such_source")


def test_track_records_the_error_and_lets_it_propagate(engine):
    with pytest.raises(SourceError), health.track(engine, "bls"):
        raise SourceError("BLS: HTTP 500")
    assert source_row(engine, "bls")["last_error"] == "BLS: HTTP 500"
    with health.track(engine, "bls"):
        pass
    assert source_row(engine, "bls")["last_success_at"] is not None


# --- news -----------------------------------------------------------------------------------


def test_record_news_stores_headlines_with_the_ticker_they_came_from(engine):
    finnhub = FakeNews(
        "finnhub",
        {
            "EQ:TSLA": [
                ("Shared story", "https://example.com/a?utm_source=x"),
                ("T", "https://e.com/t"),
            ],
            "EQ:NVDA": [("Shared story", "https://example.com/a")],
        },
    )
    adapters = {
        "finnhub": finnhub,
        "yahoo_rss": FakeNews("yahoo_rss"),
        "massive": FakeNews("massive"),
    }
    result = recorder.record_news(engine, adapters, pause=0)
    assert result["ok"] is True
    assert result["sources"]["finnhub"]["new_items"] == 2
    rows = news_rows(engine)
    assert len(rows) == 2
    shared = next(row for row in rows if row["title"] == "Shared story")
    # One article reached through two tickers is one row carrying both.
    assert shared["source_tickers"] == ["EQ:NVDA", "EQ:TSLA"]
    assert len(shared["excerpt"]) == recorder.EXCERPT_LENGTH
    assert source_row(engine, "finnhub")["last_success_at"] is not None
    assert source_row(engine, "finnhub")["last_error"] is None


def test_record_news_twice_adds_nothing_new(engine):
    adapters = {
        "finnhub": FakeNews("finnhub", {"EQ:TSLA": [("A", "https://e.com/a")]}),
        "yahoo_rss": FakeNews("yahoo_rss"),
        "massive": FakeNews("massive"),
    }
    recorder.record_news(engine, adapters, pause=0)
    with engine.connect() as conn:
        first = conn.execute(text("SELECT ingested_at FROM news_items")).scalar()
    result = recorder.record_news(engine, adapters, pause=0)
    assert result["sources"]["finnhub"]["new_items"] == 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT ingested_at FROM news_items")).scalars().all() == [first]


def test_a_news_source_that_is_down_gives_the_real_error_and_no_rows(engine):
    adapters = {
        "finnhub": FakeNews("finnhub", fail_for="all"),
        "yahoo_rss": FakeNews("yahoo_rss", {"EQ:TSLA": [("From RSS", "https://e.com/rss")]}),
        "massive": FakeNews("massive"),
    }
    result = recorder.record_news(engine, adapters, pause=0)
    assert result["ok"] is False
    assert result["sources"]["finnhub"]["calls_ok"] == 0
    assert result["sources"]["yahoo_rss"]["ok"] is True
    # Only the working source produced rows; nothing was invented for the failed one.
    assert [row["source_id"] for row in news_rows(engine)] == ["yahoo_rss"]
    finnhub = source_row(engine, "finnhub")
    assert finnhub["last_success_at"] is None
    assert "HTTP 503: upstream down" in finnhub["last_error"]
    assert finnhub["last_error"].startswith("16 errors, first:")


def test_one_failing_ticker_does_not_stop_the_others(engine):
    finnhub = FakeNews("finnhub", {"EQ:NVDA": [("N", "https://e.com/n")]}, fail_for=("EQ:TSLA",))
    result = recorder.record_news(
        engine,
        {"finnhub": finnhub, "yahoo_rss": FakeNews("yahoo_rss"), "massive": FakeNews("massive")},
        pause=0,
    )
    assert result["ok"] is False
    assert result["sources"]["finnhub"]["calls_ok"] == 15
    assert len(news_rows(engine)) == 1
    row = source_row(engine, "finnhub")
    assert row["last_success_at"] is not None and "TSLA" in row["last_error"]


def test_a_source_the_owner_switched_off_is_skipped(engine):
    with engine.begin() as conn:
        conn.execute(text("UPDATE sources SET enabled = false WHERE id = 'finnhub'"))
    adapters = {
        "finnhub": FakeNews("finnhub", fail_for="all"),
        "yahoo_rss": FakeNews("yahoo_rss"),
        "massive": FakeNews("massive"),
    }
    result = recorder.record_news(engine, adapters, pause=0)
    assert result["ok"] is True
    assert result["skipped_disabled"] == ["finnhub"]
    assert source_row(engine, "finnhub")["last_error"] is None


def test_canonical_url_drops_tracking_and_keeps_real_parameters():
    url = "HTTPS://Finance.Yahoo.com/news/a-1.html/?.tsrc=rss&utm_medium=x&id=7#top"
    assert recorder.canonical_url(url) == "https://finance.yahoo.com/news/a-1.html?id=7"
    assert recorder.dedupe_hash(url) == recorder.dedupe_hash(
        "https://finance.yahoo.com/news/a-1.html?id=7"
    )
    assert recorder.dedupe_hash(url) != recorder.dedupe_hash(
        "https://finance.yahoo.com/news/a-1.html?id=8"
    )


# --- bars -----------------------------------------------------------------------------------


def test_record_bars_writes_daily_for_all_and_intraday_for_the_watchlist(engine, tmp_path):
    adapter = FakeBars()
    result = recorder.record_bars(engine, {"yahoo_finance": adapter}, tmp_path, pause=0)
    stats = result["sources"]["yahoo_finance"]
    assert result["ok"] is True
    yahoo_tickers = [t for t in recorder.load_tickers(engine) if t.symbol_for("yahoo")]
    assert stats["daily_symbols"] == len(yahoo_tickers)
    assert stats["daily_rows_added"] == 2 * len(yahoo_tickers)
    assert {call[0] for call in adapter.intraday_calls} == {
        t.id for t in recorder.load_tickers(engine, watchlist_only=True)
    }
    # Nothing recorded yet, so the first fetch asks for the longest window each interval allows.
    assert {(interval, days) for _, interval, days in adapter.intraday_calls} == {
        ("1m", 7),
        ("5m", 59),
        ("1h", 729),
    }
    daily = pl.read_parquet(live_path(tmp_path, "bars_daily", "ETF:SPY"))
    assert daily.schema == DAILY_SCHEMA and daily.height == 2
    assert live_path(tmp_path, "bars_intraday", "ETF:SPY", "5m").exists()
    assert not live_path(tmp_path, "bars_intraday", "FUT:ES", "5m").exists()

    # A second run adds nothing and asks only for the recent days.
    adapter.intraday_calls.clear()
    again = recorder.record_bars(engine, {"yahoo_finance": adapter}, tmp_path, pause=0)
    assert again["sources"]["yahoo_finance"]["daily_rows_added"] == 0
    assert {days for _, _, days in adapter.intraday_calls} == {7}


def test_bars_source_down_writes_no_files_and_records_the_error(engine, tmp_path):
    result = recorder.record_bars(engine, {"yahoo_finance": FakeBars("all")}, tmp_path, pause=0)
    assert result["ok"] is False
    assert list(tmp_path.rglob("*.parquet")) == []
    row = source_row(engine, "yahoo_finance")
    assert row["last_success_at"] is None and "no rows returned" in row["last_error"]


def test_record_cot_covers_tickers_with_a_cftc_code(engine, tmp_path):
    result = recorder.record_cot(engine, {"cftc_cot": FakeCot()}, tmp_path)
    assert result["ok"] is True and result["sources"]["cftc_cot"]["markets"] >= 1
    assert pl.read_parquet(live_path(tmp_path, "cot", "FUT:GC")).schema == COT_SCHEMA


def test_merge_rows_newer_row_wins_and_old_rows_are_kept(tmp_path):
    path = tmp_path / "x.parquet"
    schema = {"ticker_id": pl.String(), "date": pl.Date(), "close": pl.Float64()}
    first = pl.DataFrame(
        {
            "ticker_id": ["A", "A"],
            "date": [date(2026, 1, 1), date(2026, 1, 2)],
            "close": [1.0, 2.0],
        },
        schema=schema,
    )
    second = pl.DataFrame(
        {
            "ticker_id": ["A", "A"],
            "date": [date(2026, 1, 2), date(2026, 1, 3)],
            "close": [2.5, 3.0],
        },
        schema=schema,
    )
    assert merge_rows(path, first, ("ticker_id", "date")) == {"rows": 2, "added": 2}
    assert merge_rows(path, second, ("ticker_id", "date")) == {"rows": 3, "added": 1}
    assert pl.read_parquet(path)["close"].to_list() == [1.0, 2.5, 3.0]
    assert not path.with_suffix(".parquet.tmp").exists()
    with pytest.raises(ValueError, match="columns differ"):
        merge_rows(path, second.with_columns(pl.col("close").cast(pl.Int64)), ("ticker_id", "date"))


# --- job endpoints --------------------------------------------------------------------------


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setattr(jobs, "get_engine", lambda: engine)
    monkeypatch.setattr(get_settings(), "n8n_trigger_secret", "right-secret")
    return TestClient(app, headers={"X-Trigger-Secret": "right-secret"})


def test_check_sources_job_writes_health_and_answers_502_on_any_failure(
    client, engine, monkeypatch
):
    class Broken(SourceAdapter):
        source_id = "searxng"

        def check(self) -> str:
            raise SourceError("SearXNG 'x': no results (duckduckgo: CAPTCHA)")

    adapters = {"cftc_cot": FakeCot(), "searxng": Broken()}
    monkeypatch.setattr(jobs, "_adapters", lambda: adapters)
    response = client.post("/jobs/check_sources")
    assert response.status_code == 502
    sources = response.json()["result"]["sources"]
    assert sources["cftc_cot"] == {"ok": True, "calls_ok": 1, "detail": "fake ok", "errors": []}
    assert sources["searxng"]["errors"] == ["SearXNG 'x': no results (duckduckgo: CAPTCHA)"]
    assert source_row(engine, "cftc_cot")["last_success_at"] is not None
    assert "CAPTCHA" in source_row(engine, "searxng")["last_error"]

    monkeypatch.setattr(jobs, "_adapters", lambda: {"cftc_cot": FakeCot()})
    assert client.post("/jobs/check_sources").status_code == 200


def test_a_job_already_running_is_refused(client):
    assert jobs._LOCKS["record_news"].acquire(blocking=False)
    try:
        assert client.post("/jobs/record_news").status_code == 409
    finally:
        jobs._LOCKS["record_news"].release()


def test_source_meta_is_stored_with_the_headline(engine):
    class WithMeta(FakeNews):
        def fetch_news(self, ticker, since):
            if ticker.id != "EQ:TSLA":
                return []
            meta = {"massive_insights": [{"ticker": "TSLA", "sentiment": "negative"}]}
            return [NewsHeadline("M", "https://e.com/m", NOW, None, (ticker.id,), meta)]

    adapters = {
        "finnhub": FakeNews("finnhub"),
        "yahoo_rss": FakeNews("yahoo_rss"),
        "massive": WithMeta("massive"),
    }
    assert recorder.record_news(engine, adapters, pause=0)["ok"] is True
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT source_id, source_meta FROM news_items")).all()
    expected = {"massive_insights": [{"ticker": "TSLA", "sentiment": "negative"}]}
    assert [tuple(row) for row in rows] == [("massive", expected)]
    # Nothing was written to TradeHub's own sentiment table.
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM news_sentiment")).scalar() == 0
