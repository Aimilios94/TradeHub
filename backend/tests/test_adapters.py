"""Source contract tests: each adapter against a response in the provider's recorded shape.

No network and no database. A provider changing its shape shows up here as a failing test,
and a broken response must become a SourceError, never a row.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from app.ingest.base import (
    COT_SCHEMA,
    DAILY_SCHEMA,
    INTRADAY_SCHEMA,
    BarSource,
    NewsSource,
    PositioningSource,
    SearchSource,
    SourceError,
    TickerRef,
    scrub,
)
from app.ingest.cftc import CftcCotAdapter
from app.ingest.finnhub import FinnhubAdapter
from app.ingest.searxng import SearxngAdapter
from app.ingest.yahoo import YahooFinanceAdapter
from app.ingest.yahoo_rss import YahooRssAdapter

FIXTURES = Path(__file__).parent / "fixtures"
TSLA = TickerRef("EQ:TSLA", "EQ", "TSLA", {"yahoo": "TSLA"})
GOLD = TickerRef("FUT:GC", "FUT", "GC", {"yahoo": "GC=F", "cftc": "088691"})
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def client_returning(body: bytes | str, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- Yahoo Finance (yfinance) ---------------------------------------------------------------


def yahoo_daily_frame() -> pd.DataFrame:
    """The frame yfinance returns for interval=1d, auto_adjust=False, actions=True."""
    index = pd.DatetimeIndex(
        ["2026-11-09", "2026-11-10", "2026-11-11"], tz="America/New_York", name="Date"
    )
    return pd.DataFrame(
        {
            "Open": [741.95, 747.94, float("nan")],
            "High": [755.42, 752.14, float("nan")],
            "Low": [741.03, 744.86, float("nan")],
            "Close": [752.69, 749.92, float("nan")],
            "Adj Close": [750.10, 749.92, float("nan")],
            "Volume": [50281200, 40698600, 0],
            "Dividends": [0.0, 1.85, 0.0],
            "Stock Splits": [0.0, 0.0, 0.0],
            "Capital Gains": [0.0, 0.0, 0.0],
        },
        index=index,
    )


def yahoo_intraday_frame() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        ["2026-11-11 15:58", "2026-11-11 15:59", "2026-11-11 16:00"],
        tz="America/New_York",
        name="Datetime",
    )
    return pd.DataFrame(
        {
            "Open": [760.1, 760.2, 760.3],
            "High": [760.5, 760.6, 760.7],
            "Low": [759.9, 760.0, 760.1],
            "Close": [760.2, 760.3, 760.4],
            "Adj Close": [760.2, 760.3, 760.4],
            "Volume": [1000, 2000, 3000],
            "Dividends": [0.0, 0.0, 0.0],
            "Stock Splits": [0.0, 0.0, 0.0],
        },
        index=index,
    )


def test_yahoo_daily_bars_keep_raw_and_adjusted_close_apart():
    calls = []

    def history(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return yahoo_daily_frame()

    bars = YahooFinanceAdapter(history=history).fetch_daily_bars(TSLA)
    assert calls == [("TSLA", {"interval": "1d", "period": "max"})]
    assert bars.schema == DAILY_SCHEMA
    # The third row has no prices: it is dropped, not filled.
    assert bars["date"].to_list() == [date(2026, 11, 9), date(2026, 11, 10)]
    assert bars["close"].to_list() == [752.69, 749.92]
    assert bars["adj_close"].to_list() == [750.10, 749.92]
    assert bars["dividends"].to_list() == [0.0, 1.85]
    assert set(bars["ticker_id"]) == {"EQ:TSLA"}
    assert set(bars["source_id"]) == {"yahoo_finance"}


def test_yahoo_intraday_bars_are_utc_and_skip_the_bar_still_forming():
    now = datetime(2026, 11, 11, 21, 0, 30, tzinfo=UTC)  # 16:00:30 New York
    adapter = YahooFinanceAdapter(
        history=lambda symbol, **kw: yahoo_intraday_frame(), now=lambda: now
    )
    bars = adapter.fetch_intraday_bars(TSLA, "1m", lookback_days=7)
    assert bars.schema == INTRADAY_SCHEMA
    assert bars["ts"].to_list() == [
        datetime(2026, 11, 11, 20, 58, tzinfo=UTC),
        datetime(2026, 11, 11, 20, 59, tzinfo=UTC),
    ]


def test_yahoo_lookback_is_capped_at_what_the_interval_allows():
    seen = {}

    def history(symbol, **kwargs):
        seen.update(kwargs)
        return yahoo_intraday_frame()

    YahooFinanceAdapter(history=history).fetch_intraday_bars(TSLA, "1m", lookback_days=500)
    assert seen["period"] == "7d"


@pytest.mark.parametrize(
    "history",
    [
        lambda symbol, **kw: pd.DataFrame(),  # empty answer
        lambda symbol, **kw: yahoo_daily_frame().drop(columns=["Close"]),  # shape changed
        lambda symbol, **kw: (_ for _ in ()).throw(RuntimeError("Too Many Requests")),
    ],
)
def test_yahoo_failures_are_errors_not_rows(history):
    with pytest.raises(SourceError, match="Yahoo TSLA"):
        YahooFinanceAdapter(history=history).fetch_daily_bars(TSLA)


def test_yahoo_refuses_a_ticker_without_a_yahoo_symbol():
    spot = TickerRef("SPOT:XAUUSD", "SPOT", "XAUUSD", {"twelvedata": "XAU/USD"})
    with pytest.raises(SourceError, match="no Yahoo symbol"):
        YahooFinanceAdapter(history=lambda *a, **k: yahoo_daily_frame()).fetch_daily_bars(spot)


# --- Yahoo RSS ------------------------------------------------------------------------------


def test_yahoo_rss_feed_shape():
    adapter = YahooRssAdapter(client_returning(fixture("yahoo_rss.xml")))
    items = adapter.fetch_news(TSLA, since=LONG_AGO)
    assert [item.title for item in items] == [
        "Synthetic headline one & an ampersand",
        "Synthetic headline two",
        "Synthetic headline three, no excerpt",
    ]
    assert items[0].published_at == datetime(2026, 11, 11, 20, 9, 5, tzinfo=UTC)
    assert items[0].url.startswith("https://finance.yahoo.com/news/synthetic-article-one")
    assert items[0].ticker_ids == ("EQ:TSLA",)
    assert items[2].excerpt is None


def test_yahoo_rss_trims_items_older_than_since():
    adapter = YahooRssAdapter(client_returning(fixture("yahoo_rss.xml")))
    items = adapter.fetch_news(TSLA, since=datetime(2026, 11, 10, tzinfo=UTC))
    assert len(items) == 2


@pytest.mark.parametrize("body", ["<html><body>Blocked</body></html>", "not xml at all <"])
def test_yahoo_rss_rejects_anything_that_is_not_the_feed(body):
    with pytest.raises(SourceError, match="Yahoo RSS"):
        YahooRssAdapter(client_returning(body)).fetch_news(TSLA, since=LONG_AGO)


def test_yahoo_rss_http_error_carries_the_status():
    with pytest.raises(SourceError, match="HTTP 429"):
        YahooRssAdapter(client_returning("slow down", status=429)).fetch_news(TSLA, since=LONG_AGO)


# --- Finnhub --------------------------------------------------------------------------------


def test_finnhub_company_news_shape():
    seen = []
    client = client_returning(fixture("finnhub_company_news.json"), seen=seen)
    items = FinnhubAdapter(client, "test-key-123456").fetch_news(TSLA, since=LONG_AGO)
    assert [item.title for item in items] == [
        "Synthetic Finnhub headline one",
        "Synthetic Finnhub headline two",
    ]
    assert items[0].published_at == datetime.fromtimestamp(1794427745, UTC)
    assert items[1].excerpt is None
    # The key travels in a header and never in the URL.
    assert seen[0].headers["X-Finnhub-Token"] == "test-key-123456"
    assert "test-key-123456" not in str(seen[0].url)
    assert seen[0].url.params["symbol"] == "TSLA"


def test_finnhub_without_a_key_fails_before_calling():
    seen = []
    adapter = FinnhubAdapter(client_returning("[]", seen=seen), "")
    with pytest.raises(SourceError, match="FINNHUB_API_KEY is not set"):
        adapter.fetch_news(TSLA, since=LONG_AGO)
    assert seen == []


def test_finnhub_error_object_instead_of_a_list_is_an_error():
    client = client_returning('{"error": "API limit reached."}')
    with pytest.raises(SourceError, match="expected a list"):
        FinnhubAdapter(client, "k" * 12).fetch_news(TSLA, since=LONG_AGO)


def test_finnhub_skips_tickers_it_has_no_company_news_for():
    seen = []
    adapter = FinnhubAdapter(client_returning("[]", seen=seen), "k" * 12)
    assert adapter.fetch_news(GOLD, since=LONG_AGO) == []
    assert seen == []


# --- SearXNG --------------------------------------------------------------------------------


def test_searxng_search_shape():
    seen = []
    client = client_returning(fixture("searxng_search.json"), seen=seen)
    adapter = SearxngAdapter(client, "http://searxng:8080/")
    results = adapter.search("S&P 500 stock market")
    assert [result.title for result in results] == [
        "Synthetic search result one",
        "Synthetic search result two",
    ]
    assert results[0].engines == ("bing news", "duckduckgo news")
    assert results[1].snippet is None
    assert seen[0].url.path == "/search"
    assert seen[0].url.params["format"] == "json"


def test_searxng_check_reports_engines_that_did_not_answer():
    note = SearxngAdapter(client_returning(fixture("searxng_search.json")), "http://s").check()
    assert "2 news results" in note
    assert "google news: access denied" in note


def test_searxng_no_results_is_an_error_naming_the_engines():
    body = json.dumps({"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})
    with pytest.raises(SourceError, match="duckduckgo: CAPTCHA"):
        SearxngAdapter(client_returning(body), "http://s").search("anything")


# --- CFTC COT -------------------------------------------------------------------------------


def test_cftc_cot_shape():
    reports = CftcCotAdapter(client_returning(fixture("cftc_cot.json"))).fetch_cot(GOLD)
    assert reports.schema == COT_SCHEMA
    row = reports.row(0, named=True)
    assert row["report_date"] == date(2026, 11, 3)
    assert row["market_code"] == "088691"
    assert (row["noncomm_long"], row["noncomm_short"], row["noncomm_spread"]) == (
        220530,
        40982,
        68289,
    )
    assert (row["comm_long"], row["comm_short"], row["open_interest"]) == (64326, 269228, 399169)


def test_cftc_missing_position_field_stays_empty_not_zero():
    rows = json.loads(fixture("cftc_cot.json"))
    del rows[0]["nonrept_positions_long_all"]
    reports = CftcCotAdapter(client_returning(json.dumps(rows))).fetch_cot(GOLD)
    assert reports["nonrept_long"].to_list() == [None]


def test_cftc_changed_shape_is_an_error():
    rows = json.loads(fixture("cftc_cot.json"))
    del rows[0]["report_date_as_yyyy_mm_dd"]
    with pytest.raises(SourceError, match="unexpected row shape"):
        CftcCotAdapter(client_returning(json.dumps(rows))).fetch_cot(GOLD)


def test_cftc_empty_history_is_an_error():
    with pytest.raises(SourceError, match="no reports returned"):
        CftcCotAdapter(client_returning("[]")).fetch_cot(GOLD)


# --- shared ---------------------------------------------------------------------------------


def test_adapters_implement_the_capability_they_are_used_for():
    client = client_returning("[]")
    assert isinstance(YahooFinanceAdapter(), BarSource)
    assert isinstance(YahooRssAdapter(client), NewsSource)
    assert isinstance(FinnhubAdapter(client, ""), NewsSource)
    assert isinstance(SearxngAdapter(client, "http://s"), SearchSource)
    assert isinstance(CftcCotAdapter(client), PositioningSource)


def test_scrub_removes_keys_from_error_text():
    message = "GET https://api.example.com/x?symbol=SPY&token=abc123secret&apikey=zzz999 failed"
    cleaned = scrub(message)
    assert "abc123secret" not in cleaned and "zzz999" not in cleaned
    assert "symbol=SPY" in cleaned
    assert "hunter2-value" not in scrub("boom hunter2-value boom", secrets=("hunter2-value",))
