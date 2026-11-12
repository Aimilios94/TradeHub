"""The news pipeline against a real PostgreSQL: dedupe, tags, importance, social samples, daily
scores, the process_news job and the golden day.

Headlines are invented. No source and no model is called: adapters and the LLM are fakes.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import pipeline
from app.api import jobs
from app.config import get_settings
from app.hermes import condenser
from app.hermes.client import LlmError, MockClient
from app.ingest import recorder
from app.ingest.base import (
    NewsHeadline,
    SocialSample,
    SocialSource,
    SourceAdapter,
    SourceError,
    TickerRef,
)
from app.ingest.recorder import store_headlines
from app.ingest.stocktwits import StockTwitsAdapter, parse_stream
from app.main import app
from app.scoring import composite
from app.scoring.pipeline import process_new_items
from app.scoring.ranking import rank_day
from scripts.load_sample_data import GOLDEN_DIR, load_golden_day, observed, read_golden

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)
DAY = NOW.date()


def store(engine, source, *headlines, at=NOW, published=None) -> None:
    items = [
        NewsHeadline(title, url, published or at - timedelta(hours=1), "Invented excerpt.", tickers)
        for title, url, tickers in headlines
    ]
    store_headlines(engine, source, items, at)


def rows(engine, query: str, **params) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(query), params).mappings()]


def by_url(engine) -> dict[str, dict]:
    query = (
        "SELECT n.url, n.duplicate_of, s.url AS shown_url, n.processed_at, n.importance_score, "
        "n.importance_components FROM news_items n LEFT JOIN news_items s ON s.id = n.duplicate_of"
    )
    return {row["url"]: row for row in rows(engine, query)}


def valid_answer(**changes) -> str:
    fields = {
        "catalyst_type": "other",
        "sentiment_score": 0.5,
        "tickers": ["TSLA"],
        "driver_direction": 0,
        "key_facts": [],
        "one_sentence_reason": "Invented reason.",
    }
    return json.dumps({**fields, **changes})


# --- dedupe, tags and importance ------------------------------------------------------------


def test_copies_from_other_feeds_point_to_the_first_stored_item(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha beats earnings estimates", "https://example.com/a1", ("EQ:TSLA",)),
    )
    store(
        pipeline_engine,
        "yahoo_rss",
        ("Alpha Beats Earnings Estimates!", "https://example.com/a2", ("EQ:TSLA",)),
    )
    store(
        pipeline_engine,
        "massive",
        ("Beta misses earnings estimates", "https://example.com/a3", ("EQ:NVDA",)),
    )
    result = process_new_items(pipeline_engine, now=NOW)

    assert (result["new_items"], result["duplicates"]) == (3, 1)
    assert result["duplicate_rate"] == round(1 / 3, 4)
    items = by_url(pipeline_engine)
    assert items["https://example.com/a2"]["shown_url"] == "https://example.com/a1"
    assert items["https://example.com/a1"]["duplicate_of"] is None
    assert items["https://example.com/a3"]["duplicate_of"] is None
    assert all(item["processed_at"] is not None for item in items.values())
    assert items["https://example.com/a2"]["importance_score"] is None  # only shown items rank


def test_a_second_run_changes_nothing_and_a_late_copy_raises_the_story(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha beats earnings estimates", "https://example.com/b1", ("EQ:TSLA",)),
    )
    process_new_items(pipeline_engine, now=NOW)
    before = by_url(pipeline_engine)["https://example.com/b1"]
    again = process_new_items(pipeline_engine, now=NOW)
    assert (again["new_items"], again["duplicates"], again["duplicate_rate"]) == (0, 0, None)
    assert by_url(pipeline_engine)["https://example.com/b1"] == before

    later = NOW + timedelta(hours=6)
    store(
        pipeline_engine,
        "massive",
        ("Alpha beats earnings estimates", "https://example.com/b2", ("EQ:NVDA",)),
        at=later,
    )
    process_new_items(pipeline_engine, now=later)
    after = by_url(pipeline_engine)
    assert after["https://example.com/b2"]["shown_url"] == "https://example.com/b1"
    shown = after["https://example.com/b1"]
    assert shown["importance_score"] > before["importance_score"]
    assert shown["importance_components"]["copies"] == 2
    assert shown["importance_components"]["watchlist_tickers"] == 2  # tickers of both copies


def test_shown_items_are_tagged_with_the_assets_the_matrix_names(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Fed delivers surprise rate hike", "https://example.com/t1", ("ETF:SPY",)),
        ("Futures flat ahead of CPI report", "https://example.com/t2", ("ETF:SPY",)),
        ("Alpha opens a new store", "https://example.com/t3", ("EQ:TSLA",)),
    )
    store(
        pipeline_engine,
        "yahoo_rss",
        ("Fed delivers surprise rate hike", "https://example.com/t4", ("ETF:QQQ",)),
    )
    result = process_new_items(pipeline_engine, now=NOW)
    assert result["tags_written"] == 2 and result["tagger_version"] == "tagger-v1"

    tags = rows(
        pipeline_engine,
        "SELECT n.url, e.driver_id, e.direction, e.scheduled, e.tagged_by, e.affected_assets, "
        "e.tagger_version, e.matched_rules, e.event_time FROM tagged_events e "
        "JOIN news_items n ON n.id = e.news_item_id ORDER BY n.url",
    )
    assert [(t["url"][-2:], t["driver_id"], t["direction"], t["scheduled"]) for t in tags] == [
        ("t1", "monetary", 1, False),
        ("t2", "monetary", 0, True),
    ]
    hike = tags[0]
    assert hike["tagged_by"] == "code" and "monetary_hawkish" in hike["matched_rules"]
    assert {"ETF:SPY", "ETF:QQQ", "ETF:GLD"} <= set(hike["affected_assets"])
    assert hike["event_time"] == NOW - timedelta(hours=1)


def test_a_failure_leaves_every_item_unprocessed(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha beats earnings estimates", "https://example.com/x1", ("EQ:TSLA",)),
    )

    class Broken:
        version = "broken"

        def tag(self, title):
            raise RuntimeError("tagger exploded")

    with pytest.raises(RuntimeError, match="tagger exploded"):
        process_new_items(pipeline_engine, tagger=Broken(), now=NOW)
    assert by_url(pipeline_engine)["https://example.com/x1"]["processed_at"] is None
    assert process_new_items(pipeline_engine, now=NOW)["new_items"] == 1


def test_ranking_is_importance_then_newest_then_first_stored(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha shares little changed", "https://example.com/r1", ("EQ:TSLA",)),
    )
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha beats earnings estimates", "https://example.com/r2", ("EQ:TSLA",)),
    )
    store(
        pipeline_engine,
        "finnhub",
        ("Beta shares little changed", "https://example.com/r3", ("EQ:NVDA",)),
        published=NOW - timedelta(minutes=5),
    )
    store(
        pipeline_engine,
        "yahoo_rss",
        ("Alpha beats earnings estimates", "https://example.com/r4", ("EQ:TSLA",)),
    )
    process_new_items(pipeline_engine, now=NOW)
    ranked = rank_day(pipeline_engine, DAY)
    assert [row["url"][-2:] for row in ranked] == ["r2", "r3", "r1"]
    assert ranked[0]["duplicates"] == 1 and ranked[0]["tags"] == []
    assert rank_day(pipeline_engine, DAY - timedelta(days=1)) == []


# --- StockTwits ------------------------------------------------------------------------------


def test_stocktwits_fixture_counts_labels_and_keeps_no_text():
    payload = json.loads((FIXTURES / "stocktwits_stream.json").read_text())
    assert parse_stream(payload, "EQ:TSLA", "TSLA") == SocialSample("EQ:TSLA", 2, 1, 4)


def test_stocktwits_adapter_is_a_social_source_and_fails_honestly():
    body = (FIXTURES / "stocktwits_stream.json").read_bytes()
    ok = StockTwitsAdapter(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))
    )
    assert isinstance(ok, SocialSource)
    assert ok.check() == "SPY: 4 messages, 2 bullish, 1 bearish"
    assert ok.fetch_sentiment(TickerRef("FUT:GC", "FUT", "GC")) is None  # stocks and ETFs only

    blocked = StockTwitsAdapter(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(403, text="<html>Just a moment</html>")
            )
        )
    )
    with pytest.raises(SourceError, match="HTTP 403"):
        blocked.fetch_sentiment(TickerRef("EQ:TSLA", "EQ", "TSLA"))
    with pytest.raises(SourceError, match="no 'messages' list"):
        parse_stream({"response": {"status": 200}}, "EQ:TSLA", "TSLA")


class FakeSocial(SourceAdapter):
    source_id = "stocktwits"

    def __init__(self, fail_for=()):
        self.fail_for = fail_for
        self.calls = []

    def check(self) -> str:
        return "fake ok"

    def fetch_sentiment(self, ticker):
        self.calls.append(ticker.id)
        if self.fail_for == "all" or ticker.id in self.fail_for:
            raise SourceError(f"StockTwits {ticker.symbol}: HTTP 403: <html>")
        return SocialSample(ticker.id, 9, 3, 30)


def test_social_samples_are_stored_once_a_day_and_failures_store_nothing(pipeline_engine):
    first = recorder.record_social(
        pipeline_engine, {"stocktwits": FakeSocial(("EQ:TSLA",))}, pause=0
    )
    source = first["sources"]["stocktwits"]
    assert first["ok"] is False and source["samples_stored"] == 15
    assert source["errors"] == ["StockTwits TSLA: HTTP 403: <html>"]
    stored = rows(
        pipeline_engine, "SELECT ticker_id, bullish, bearish, messages FROM social_sentiment"
    )
    assert len(stored) == 15 and "EQ:TSLA" not in {row["ticker_id"] for row in stored}

    retry = FakeSocial()
    second = recorder.record_social(pipeline_engine, {"stocktwits": retry}, pause=0)
    assert retry.calls == ["EQ:TSLA"]  # only the ticker still missing today
    assert second["ok"] and second["sources"]["stocktwits"]["already_sampled"] == 15
    assert len(rows(pipeline_engine, "SELECT 1 AS x FROM social_sentiment")) == 16


# --- daily score ----------------------------------------------------------------------------


def test_daily_scores_come_from_sentiment_tags_and_social(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha opens a new store", "https://example.com/s1", ("EQ:TSLA",)),
        ("Fed delivers surprise rate hike", "https://example.com/s2", ("ETF:SPY",)),
    )
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(
        lambda s, u: valid_answer(tickers=["TSLA"] if "Alpha" in u.split("\n")[0] else [])
    )
    condenser.condense_pending(
        pipeline_engine,
        client,
        daily_cap=10,
        run_cap=10,
        monthly_budget=5,
        lookback=timedelta(days=3),
        now=NOW,
    )
    with pipeline_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO social_sentiment "
                "(ticker_id, day, source_id, bullish, bearish, messages) VALUES "
                "('EQ:TSLA', :day, 'stocktwits', 9, 3, 30), "
                "('EQ:NVDA', :day, 'stocktwits', 2, 1, 30)"
            ),
            {"day": DAY},
        )
    assert composite.score_day(pipeline_engine, DAY, condenser.PROMPT_VERSION, NOW) == 4

    scores = {
        row["ticker_id"]: row
        for row in rows(
            pipeline_engine, "SELECT * FROM daily_scores WHERE score_date = :day", day=DAY
        )
    }
    # TSLA: one headline at +0.5 -> news 0.1 -> 10 points, plus social 20 * (9 - 3) / 12 = 10.
    assert float(scores["EQ:TSLA"]["composite_score"]) == 20.0
    assert scores["EQ:TSLA"]["news_count"] == 1
    assert scores["EQ:TSLA"]["components"]["social"] == 0.5
    # A hawkish tag reaches SPY, QQQ and GLD through the sensitivity matrix: expected -1 each.
    # One tagged headline is a fifth of full weight: -20, not -100.
    for ticker in ("ETF:SPY", "ETF:QQQ", "ETF:GLD"):
        assert float(scores[ticker]["composite_score"]) == -20.0
        assert scores[ticker]["components"]["news"] is None
        assert scores[ticker]["components"]["driver"] == -0.2
        assert scores[ticker]["components"]["driver_events"] == 1
        assert scores[ticker]["code_version"] == "composite-v2"
    assert "EQ:NVDA" not in scores  # too few labeled messages and nothing else: no row
    assert (
        composite.score_day(pipeline_engine, DAY - timedelta(days=5), condenser.PROMPT_VERSION, NOW)
        == 0
    )


# --- the process_news job -------------------------------------------------------------------


def test_job_runs_every_step_and_social_failure_does_not_fail_it(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha opens a new store", "https://example.com/j1", ("EQ:TSLA",)),
    )
    result = pipeline.process_news(
        pipeline_engine,
        get_settings(),
        {"stocktwits": FakeSocial("all")},
        lambda: MockClient(lambda s, u: valid_answer()),
        now=NOW,
    )
    steps = result["steps"]
    assert result["ok"] is True
    assert steps["process"]["new_items"] == 1 and steps["condense"]["condensed"] == 1
    assert steps["social"]["ok"] is False  # reported, with the real error
    assert "HTTP 403" in steps["social"]["sources"]["stocktwits"]["errors"][0]
    assert steps["score"]["rows"] == {"2026-11-11": 0, "2026-11-12": 1}
    assert rows(pipeline_engine, "SELECT last_error FROM sources WHERE id = 'stocktwits'")[0][
        "last_error"
    ]
    assert rows(pipeline_engine, "SELECT 1 AS x FROM social_sentiment") == []  # nothing invented


def test_job_fails_with_the_real_error_when_no_model_is_configured(pipeline_engine):
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha opens a new store", "https://example.com/j2", ("EQ:TSLA",)),
    )

    def no_model():
        raise LlmError(
            "No cheap-tier model is configured: set OLLAMA_MODEL or OPENROUTER_MODEL_CHEAP"
        )

    result = pipeline.process_news(
        pipeline_engine, get_settings(), {"stocktwits": FakeSocial()}, no_model, now=NOW
    )
    assert result["ok"] is False
    assert "No cheap-tier model is configured" in result["steps"]["condense"]["error"]
    assert result["steps"]["process"]["ok"] and result["steps"]["score"]["ok"]  # the rest still ran
    assert rows(pipeline_engine, "SELECT 1 AS x FROM news_sentiment") == []


def test_process_news_endpoint_and_read_views(pipeline_engine, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(jobs, "get_engine", lambda: pipeline_engine)
    monkeypatch.setattr("app.api.news.get_engine", lambda: pipeline_engine)
    monkeypatch.setattr(jobs, "_adapters", lambda: {"stocktwits": FakeSocial()})
    monkeypatch.setattr(
        jobs, "build_cheap_client", lambda *args: MockClient(lambda s, u: valid_answer())
    )
    monkeypatch.setattr(settings, "n8n_trigger_secret", "test-secret")
    store(
        pipeline_engine,
        "finnhub",
        ("Alpha opens a new store", "https://example.com/k1", ("EQ:TSLA",)),
        at=datetime.now(UTC),
    )

    client = TestClient(app)
    response = client.post("/jobs/process_news", headers={"X-Trigger-Secret": "test-secret"})
    assert response.status_code == 200 and response.json()["result"]["ok"] is True

    top = client.get("/api/news/top").json()
    assert [item["url"] for item in top["items"]] == ["https://example.com/k1"]
    assert top["items"][0]["sentiment"][0]["ticker"] == "EQ:TSLA"
    scores = client.get("/api/scores").json()["scores"]
    assert "EQ:TSLA" in {row["ticker_id"] for row in scores}
    assert client.get("/api/news/top", params={"day": "2020-01-01"}).json()["items"] == []


# --- golden day -----------------------------------------------------------------------------


def test_golden_day_gives_the_same_duplicates_tags_and_ranking(pipeline_engine):
    """The committed synthetic day must always come out the same. If this fails after a change
    to dedupe, the tagger rules or the importance score, the change altered what the owner sees:
    review the difference, then regenerate expected.json on purpose."""
    golden = read_golden()
    result = load_golden_day(pipeline_engine, golden)
    expected = json.loads((GOLDEN_DIR / "expected.json").read_text(encoding="utf-8"))

    assert result["new_items"] == len(golden["items"])
    got = observed(pipeline_engine, golden)
    assert got["duplicates"] == expected["duplicates"]
    assert got["tags"] == expected["tags"]
    assert [row["url"] for row in got["ranking"]] == [row["url"] for row in expected["ranking"]]
    assert got["ranking"] == expected["ranking"]
    assert date.fromisoformat(golden["day"]) == datetime.fromisoformat(golden["ingested_at"]).date()


def test_golden_day_rows_are_marked_demo(pipeline_engine):
    load_golden_day(pipeline_engine)
    flags = rows(pipeline_engine, "SELECT DISTINCT is_demo FROM news_items")
    assert flags == [{"is_demo": True}]
