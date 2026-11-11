"""Massive adapter contract tests: futures backup and news, in the provider's recorded shape."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from app.ingest.base import NewsSource, SourceError, TickerRef
from app.ingest.massive import MassiveAdapter

FIXTURES = Path(__file__).parent / "fixtures"
TSLA = TickerRef("EQ:TSLA", "EQ", "TSLA", {"yahoo": "TSLA"})
GOLD = TickerRef("FUT:GC", "FUT", "GC", {"yahoo": "GC=F", "massive": "GC"})
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def massive_client(seen: list) -> httpx.Client:
    """Answers each endpoint with its fixture. GCZ6 is the heavily traded contract."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/futures/v1/contracts":
            return httpx.Response(200, content=fixture("massive_contracts.json"))
        if path.startswith("/futures/v1/aggs/"):
            contract = path.rsplit("/", 1)[1]
            body = json.loads(fixture("massive_sessions.json"))
            for row in body["results"]:
                row["ticker"] = contract
                if contract == "GCZ6":
                    row["volume"] = 180000
            if contract == "GCF7":
                body["results"] = []  # a listed contract that has not traded yet
            return httpx.Response(200, json=body)
        return httpx.Response(200, content=fixture("massive_news.json"))

    return httpx.Client(transport=httpx.MockTransport(handler))


def fixed_client(body: str, status: int = 200) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body)))


def adapter(seen: list, **kwargs) -> MassiveAdapter:
    return MassiveAdapter(massive_client(seen), "massive-test-key", min_interval=0, **kwargs)


def test_picks_the_most_active_contract_not_the_nearest():
    seen = []
    massive = adapter(seen, today=lambda: date(2026, 11, 11))
    sessions = massive.fetch_futures_sessions(GOLD)
    # GCX6 expires first but barely trades; the December contract is the market.
    assert [request.url.path.rsplit("/", 1)[1] for request in seen[1:]] == ["GCX6", "GCZ6", "GCF7"]
    assert sessions[0].contract == "GCZ6" and sessions[0].volume == 180000
    assert sessions[0].session_date == date(2026, 11, 11)
    assert (sessions[0].close, sessions[0].settlement) == (3996.3, 3992.0)
    assert sessions[0].ticker_id == "FUT:GC"
    # The key travels in a header and never in the URL.
    assert seen[0].headers["Authorization"] == "Bearer massive-test-key"
    assert "massive-test-key" not in str(seen[0].url)

    # Later the same day the choice is reused: one call instead of four.
    seen.clear()
    assert massive.fetch_futures_sessions(GOLD)[0].contract == "GCZ6"
    assert len(seen) == 1


def test_calls_are_paced_to_the_free_limit():
    slept, clock = [], [100.0]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    massive = MassiveAdapter(
        massive_client([]), "k" * 12, min_interval=12.5, sleep=sleep, clock=lambda: clock[0]
    )
    for _ in range(3):
        massive.check()
    assert slept == [12.5, 12.5]


def test_check_reports_the_nearest_contract():
    assert adapter([]).check() == "ES: 3 active contracts, nearest GCX6"


def test_news_shape_keeps_the_provider_sentiment_as_reference_only():
    massive = adapter([])
    assert isinstance(massive, NewsSource)
    items = massive.fetch_news(TSLA, since=LONG_AGO)
    assert [item.title for item in items] == [
        "Synthetic Massive headline one",
        "Synthetic Massive headline two",
    ]
    assert items[0].published_at == datetime(2026, 11, 11, 13, 2, tzinfo=UTC)
    assert items[0].ticker_ids == ("EQ:TSLA",)
    assert items[0].meta == {
        "publisher": "Example Publisher",
        "source_tickers_raw": ["GOOG", "TSLA"],
        "massive_insights": [
            {"ticker": "GOOG", "sentiment": "neutral"},
            {"ticker": "TSLA", "sentiment": "negative"},
        ],
    }
    assert items[1].excerpt is None and items[1].meta["massive_insights"] == []


def test_news_is_not_requested_for_futures():
    seen = []
    assert adapter(seen).fetch_news(GOLD, since=LONG_AGO) == []
    assert seen == []


def test_failures_are_errors():
    with pytest.raises(SourceError, match="MASSIVE_API_KEY is not set"):
        MassiveAdapter(fixed_client("{}"), "", min_interval=0).check()
    limited = MassiveAdapter(fixed_client('{"status":"ERROR"}', 429), "k" * 12, min_interval=0)
    with pytest.raises(SourceError, match="HTTP 429"):
        limited.fetch_news(TSLA, since=LONG_AGO)
    odd = MassiveAdapter(fixed_client('{"status":"OK"}'), "k" * 12, min_interval=0)
    with pytest.raises(SourceError, match="no 'results' list"):
        odd.fetch_futures_sessions(GOLD)
    with pytest.raises(SourceError, match="no Massive product code"):
        odd.fetch_futures_sessions(TSLA)
