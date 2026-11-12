"""Hermes layer: number check, LLM clients, condenser validation, retry and traces.

No real model is ever called: clients are tested against a mock transport, and the condenser
runs with MockClient against the throwaway database. Headlines here are invented.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text

from app.config import Settings
from app.hermes import condenser
from app.hermes.client import (
    LlmError,
    MockClient,
    OllamaClient,
    OpenRouterClient,
    build_cheap_client,
)
from app.hermes.number_check import check_numbers, extract_figures
from app.hermes.schemas import CATALYST_TYPES, CondenserOutput
from app.ingest.base import NewsHeadline
from app.ingest.recorder import store_headlines
from app.scoring.pipeline import process_new_items

NOW = datetime(2026, 11, 12, 15, 0, tzinfo=UTC)

# --- number check ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "given", "passes"),
    [
        ("up 2.3%", "rose 2.31%", True),  # the plan's examples
        ("up 2.3%", "rose 2.34%", True),
        ("up 2.3%", "rose 2.36%", False),
        ("about 2%", "1.5 percent", True),
        ("about 2%", "2.49 percent", True),
        ("about 2%", "2.5 percent", False),
        ("fell 1.2%", "change of -1.2%", True),  # sign carried by the wording
        ("-1.2%", "change of 1.2%", False),  # a written sign must match
        ("revenue of $1.2B", "revenue of 1,234,000,000 dollars", True),
        ("revenue of $1.2B", "revenue of 1.3 billion", False),
        ("sold 35,000 cars", "deliveries of 35,000", True),
        ("three of 7 analysts", "analysts were split", True),  # small counts are free
        ("12 analysts", "analysts were split", False),
        ("its 25th year", "an anniversary", True),  # ordinals are free
        ("in 2027", "guidance for 2027", True),
        ("in 2027", "guidance for next year", False),  # a year must be in the input
        ("Q3 results and the F-150", "quarterly results", True),  # digits inside names
        ("no figures at all", "", True),
    ],
)
def test_number_check_follows_the_plan_rules(written, given, passes):
    assert (check_numbers(written, given) == []) is passes


def test_number_check_names_the_invented_figures():
    assert check_numbers("Revenue rose 14% to $3.1B", "Revenue rose to 3.1 billion") == ["14%"]


def test_numbers_given_as_values_count_as_input():
    assert check_numbers("VIX at 18.4", "", input_numbers=(18.42,)) == []
    assert check_numbers("VIX at 18.4", "", input_numbers=(18.47,)) == ["18.4"]


def test_units_scale_the_step_as_well_as_the_value():
    (figure,) = extract_figures("1.25 million")
    assert figure.value == 1_250_000 and figure.step == 10_000


# --- clients --------------------------------------------------------------------------------


def http_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ollama_client_sends_the_schema_and_reads_the_answer():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        body = {"message": {"content": '{"a": 1}'}, "prompt_eval_count": 120, "eval_count": 30}
        return httpx.Response(200, json=body)

    client = OllamaClient(http_client(handler), "http://ollama:11434/", "tiny")
    result = client.complete("system text", "user text", {"type": "object"})
    assert result.text == '{"a": 1}'
    assert (result.prompt_tokens, result.completion_tokens, result.cost_usd) == (120, 30, 0.0)
    assert seen[0]["format"] == {"type": "object"}
    assert seen[0]["stream"] is False and seen[0]["options"]["temperature"] == 0
    assert [m["role"] for m in seen[0]["messages"]] == ["system", "user"]


def test_openrouter_client_reads_reported_cost_and_never_guesses_one():
    answers = [
        {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 10, "cost": 0.0002},
        },
        {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 10}},
    ]
    client = OpenRouterClient(
        http_client(lambda request: httpx.Response(200, json=answers.pop(0))),
        "k" * 20,
        "m",
        "cheap",
    )
    assert client.complete("s", "u").cost_usd == 0.0002
    assert client.complete("s", "u").cost_usd is None


def test_a_failed_call_is_an_llm_error_with_the_real_reason_and_no_key():
    key = "sk-or-secret-key-123456"
    down = OpenRouterClient(
        http_client(lambda request: httpx.Response(401, text=f"bad key {key}")), key, "m", "cheap"
    )
    with pytest.raises(LlmError) as error:
        down.complete("s", "u")
    assert "HTTP 401" in str(error.value) and key not in str(error.value)

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LlmError, match="ConnectError"):
        OllamaClient(http_client(refuse), "http://ollama:11434", "tiny").complete("s", "u")
    empty = OllamaClient(
        http_client(lambda request: httpx.Response(200, json={"message": {"content": ""}})),
        "http://ollama:11434",
        "tiny",
    )
    with pytest.raises(LlmError, match="empty"):
        empty.complete("s", "u")


def test_cheap_tier_comes_from_settings_and_fails_loudly_when_unset():
    http = http_client(lambda request: httpx.Response(200))
    base = {"openrouter_api_key": "k" * 20, "_env_file": None}
    none = Settings(openrouter_model_cheap="", ollama_model="", **base)
    with pytest.raises(LlmError, match="No cheap-tier model"):
        build_cheap_client(none, http)
    local = build_cheap_client(
        Settings(openrouter_model_cheap="", ollama_model="tiny", **base), http
    )
    assert isinstance(local, OllamaClient) and local.model == "tiny" and local.tier == "cheap"
    hosted = build_cheap_client(
        Settings(openrouter_model_cheap="vendor/small", ollama_model="tiny", **base), http
    )
    assert isinstance(hosted, OpenRouterClient) and hosted.model == "vendor/small"


# --- answer validation (no database) --------------------------------------------------------

CANDIDATES = [
    condenser.Candidate("EQ:TSLA", "TSLA", "Tesla", True),
    condenser.Candidate("EQ:NVDA", "NVDA", "NVIDIA", True),
    condenser.Candidate("ETF:IWM", "IWM", "Russell 2000 ETF", False),
]
MONETARY = condenser.Driver("monetary", "Monetary", "Hawkish", "Dovish")
FLUSH = condenser.Driver("liquidity_flush", "Liquidity flush", "Flush under way", None)


def make_item(driver=None, **changes) -> condenser.Item:
    fields = {
        "id": 1,
        "title": "Alpha Motors recalls 35,000 cars",
        "excerpt": "The company said the recall covers 35,000 vehicles built in 2026.",
        "published_at": datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
        "ticker_ids": ("EQ:TSLA",),
        "driver": driver,
    }
    return condenser.Item(**{**fields, **changes})


def answer(**changes) -> str:
    fields = {
        "catalyst_type": "legal_regulatory",
        "sentiment_score": -0.4,
        "tickers": ["TSLA"],
        "driver_direction": 0,
        "key_facts": ["The recall covers 35,000 vehicles."],
        "one_sentence_reason": "A recall adds cost and reputational risk.",
    }
    return json.dumps({**fields, **changes})


def test_a_valid_answer_passes():
    outcome, output, reason = condenser.validate_answer(answer(), make_item(), CANDIDATES)
    assert (outcome, reason) == ("ok", None)
    assert output.catalyst_type == "legal_regulatory" and output.tickers == ["TSLA"]


@pytest.mark.parametrize(
    ("text_answer", "outcome", "reason_part"),
    [
        ("Sure! Here is the JSON: {}", "invalid_json", "not valid JSON"),
        ('```json\n{"catalyst_type": "other"}\n```', "invalid_json", "not valid JSON"),
        (answer(catalyst_type="rumour"), "schema_failed", "catalyst_type"),
        (answer(sentiment_score=1.4), "schema_failed", "sentiment_score"),
        (answer(driver_direction=2), "schema_failed", "driver_direction"),
        (answer(extra_field="x"), "schema_failed", "extra_field"),
        (json.dumps({"catalyst_type": "other"}), "schema_failed", "sentiment_score"),
        (answer(tickers=["TSLA", "AAPL"]), "schema_failed", "AAPL"),
        (answer(tickers=["IWM"]), "schema_failed", "IWM"),  # known ticker, not a candidate here
        (answer(driver_direction=1), "schema_failed", "no driver is given"),
        (answer(key_facts=["Recall of 50,000 cars."]), "number_check_failed", "50,000"),
        (
            answer(one_sentence_reason="Shares may fall 12% on the recall."),
            "number_check_failed",
            "12%",
        ),
    ],
)
def test_a_bad_answer_is_rejected_with_its_reason(text_answer, outcome, reason_part):
    got, output, reason = condenser.validate_answer(text_answer, make_item(), CANDIDATES)
    assert (got, output) == (outcome, None)
    assert reason_part in reason


def test_driver_direction_is_allowed_only_inside_the_given_driver_menu():
    ok, _, _ = condenser.validate_answer(
        answer(driver_direction=-1), make_item(MONETARY), CANDIDATES
    )
    assert ok == "ok"
    bad, _, reason = condenser.validate_answer(
        answer(driver_direction=-1), make_item(FLUSH), CANDIDATES
    )
    assert bad == "schema_failed" and "no -1" in reason


def test_a_candidate_off_the_watchlist_is_allowed_when_the_source_attached_it():
    item = make_item(ticker_ids=("ETF:IWM",))
    assert condenser.validate_answer(answer(tickers=["IWM"]), item, CANDIDATES)[0] == "ok"


def test_schema_menu_matches_the_database_menu():
    assert set(CATALYST_TYPES) == {
        "earnings",
        "guidance",
        "analyst_rating",
        "m_and_a",
        "product",
        "legal_regulatory",
        "management",
        "macro",
        "other",
    }
    assert "catalyst_type" in CondenserOutput.model_json_schema()["required"]


def test_prompt_file_loads_and_renders_every_field():
    prompt = condenser.load_prompt()
    assert prompt.version == condenser.PROMPT_VERSION
    assert "catalyst_type" in prompt.system
    user = condenser.render_user(prompt, make_item(MONETARY), CANDIDATES)
    assert "Alpha Motors recalls 35,000 cars" in user and "2026-11-10" in user
    assert "TSLA (Tesla), NVDA (NVIDIA)" in user and "IWM" not in user
    assert "Monetary. +1 means Hawkish, -1 means Dovish." in user
    assert "Driver: none" in condenser.render_user(prompt, make_item(), CANDIDATES)
    assert "-1 is not used" in condenser.render_user(prompt, make_item(FLUSH), CANDIDATES)


# --- condenser against the database ---------------------------------------------------------


EXCERPT = "Invented: the recall covers 35,000 vehicles."


def store(engine, *headlines: tuple[str, str, tuple[str, ...]], source="finnhub") -> None:
    items = [
        NewsHeadline(title, url, NOW - timedelta(hours=2), EXCERPT, tickers)
        for title, url, tickers in headlines
    ]
    store_headlines(engine, source, items, NOW - timedelta(hours=1))


def run(engine, client, **limits) -> dict:
    defaults = {"daily_cap": 100, "run_cap": 100, "monthly_budget": 5.0}
    return condenser.condense_pending(
        engine, client, lookback=timedelta(days=3), now=NOW, **{**defaults, **limits}
    )


def rows(engine, query: str) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row) for row in conn.execute(text(query)).mappings()]


TRACES = "SELECT attempt, outcome, error, news_item_id, model, tier FROM llm_traces ORDER BY id"
SENTIMENT = (
    "SELECT ticker_id, catalyst_type, sentiment_score, prompt_version, model, llm_trace_id "
    "FROM news_sentiment ORDER BY ticker_id"
)


def test_a_valid_answer_is_stored_per_ticker_with_its_trace(pipeline_engine):
    store(pipeline_engine, ("Alpha Motors recalls cars", "https://example.com/c1", ("EQ:TSLA",)))
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(lambda system, user: answer(tickers=["TSLA", "NVDA"]))
    result = run(pipeline_engine, client)

    assert result["ok"] and result["condensed"] == 1 and result["calls"] == 1
    stored = rows(pipeline_engine, SENTIMENT)
    assert [row["ticker_id"] for row in stored] == ["EQ:NVDA", "EQ:TSLA"]
    assert {row["prompt_version"] for row in stored} == {condenser.PROMPT_VERSION}
    (trace,) = rows(pipeline_engine, TRACES)
    assert (trace["attempt"], trace["outcome"], trace["model"], trace["tier"]) == (
        1,
        "ok",
        "mock",
        "cheap",
    )
    assert trace["news_item_id"] is not None
    assert run(pipeline_engine, client)["pending"] == 0  # condensed once, not again


def test_a_bad_answer_is_retried_once_with_the_reason(pipeline_engine):
    store(pipeline_engine, ("Alpha Motors recalls cars", "https://example.com/c2", ("EQ:TSLA",)))
    process_new_items(pipeline_engine, now=NOW)
    answers = ["not json", answer()]
    client = MockClient(lambda system, user: answers.pop(0))
    result = run(pipeline_engine, client)

    assert result["condensed"] == 1 and result["failed"] == 0 and result["calls"] == 2
    assert [(t["attempt"], t["outcome"]) for t in rows(pipeline_engine, TRACES)] == [
        (1, "invalid_json"),
        (2, "ok"),
    ]
    assert "rejected" not in client.calls[0][1]
    assert "Your previous answer was rejected: The answer is not valid JSON" in client.calls[1][1]
    assert len(rows(pipeline_engine, SENTIMENT)) == 1


def test_two_bad_answers_are_logged_and_nothing_is_stored(pipeline_engine):
    store(pipeline_engine, ("Alpha Motors recalls cars", "https://example.com/c3", ("EQ:TSLA",)))
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(lambda system, user: answer(catalyst_type="rumour"))
    result = run(pipeline_engine, client)

    assert result["ok"] is True  # a bad answer is a logged failure, not a broken job
    assert (result["condensed"], result["failed"], result["calls"]) == (0, 1, 2)
    assert rows(pipeline_engine, SENTIMENT) == []
    traces = rows(pipeline_engine, TRACES)
    assert [(t["attempt"], t["outcome"]) for t in traces] == [
        (1, "schema_failed"),
        (2, "schema_failed"),
    ]
    assert all("catalyst_type" in t["error"] for t in traces)
    assert run(pipeline_engine, client)["pending"] == 0  # not asked a third time


def test_an_invented_number_blocks_the_row(pipeline_engine):
    store(pipeline_engine, ("Alpha Motors recalls cars", "https://example.com/c4", ("EQ:TSLA",)))
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(lambda s, u: answer(key_facts=["Recall of 50,000 cars."]))
    result = run(pipeline_engine, client)
    assert result["failed"] == 1 and rows(pipeline_engine, SENTIMENT) == []
    assert {t["outcome"] for t in rows(pipeline_engine, TRACES)} == {"number_check_failed"}


def test_a_model_that_is_down_fails_the_run_and_leaves_items_pending(pipeline_engine):
    store(
        pipeline_engine,
        *[(f"Alpha story number {n}", f"https://example.com/d{n}", ("EQ:TSLA",)) for n in range(5)],
    )
    process_new_items(pipeline_engine, now=NOW)

    def down(system, user):
        raise LlmError("Ollama tiny: ConnectError: connection refused")

    result = run(pipeline_engine, MockClient(down))
    assert result["ok"] is False
    assert result["calls"] == condenser.MAX_TRANSPORT_ERRORS  # stopped early, not 5 items x 2
    assert "connection refused" in result["errors"][0] and "model is down" in result["errors"][-1]
    traces = rows(pipeline_engine, TRACES)
    assert {t["outcome"] for t in traces} == {"error"} and len(traces) == 3
    assert rows(pipeline_engine, SENTIMENT) == []

    recovered = run(pipeline_engine, MockClient(lambda s, u: answer()))
    assert recovered["pending"] == 5 and recovered["condensed"] == 5


def test_the_daily_cap_stops_the_run_and_is_logged(pipeline_engine):
    store(
        pipeline_engine,
        *[(f"Alpha story number {n}", f"https://example.com/e{n}", ("EQ:TSLA",)) for n in range(4)],
    )
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(lambda s, u: answer())
    result = run(pipeline_engine, client, daily_cap=3)

    assert result["ok"] and result["calls"] == 3 and result["condensed"] == 3
    assert result["capped"] == "daily cap of 3 calls reached" and result["left_pending"] == 1
    capped = [t for t in rows(pipeline_engine, TRACES) if t["outcome"] == "capped"]
    assert len(capped) == 1 and "1 items left pending" in capped[0]["error"]
    again = run(pipeline_engine, client, daily_cap=3)
    assert again["calls"] == 0 and again["capped"]  # the cap counts calls already made today


def test_the_run_cap_limits_one_run_only(pipeline_engine):
    store(
        pipeline_engine,
        *[(f"Alpha story number {n}", f"https://example.com/f{n}", ("EQ:TSLA",)) for n in range(3)],
    )
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(lambda s, u: answer())
    assert run(pipeline_engine, client, run_cap=2)["condensed"] == 2
    assert run(pipeline_engine, client, run_cap=2)["condensed"] == 1


def test_the_monthly_budget_stops_paid_calls(pipeline_engine):
    store(pipeline_engine, ("Alpha Motors recalls cars", "https://example.com/g1", ("EQ:TSLA",)))
    process_new_items(pipeline_engine, now=NOW)
    with pipeline_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO llm_traces (template, prompt_version, tier, model, outcome, cost_usd, "
                "created_at) VALUES ('narrator', 'v', 'better', 'm', 'ok', 5.01, :at)"
            ),
            {"at": NOW - timedelta(days=1)},
        )
    result = run(pipeline_engine, MockClient(lambda s, u: answer()))
    assert result["calls"] == 0 and "monthly budget reached" in result["capped"]


def test_duplicates_and_old_items_are_not_condensed(pipeline_engine):
    store(pipeline_engine, ("Alpha Motors recalls cars", "https://example.com/h1", ("EQ:TSLA",)))
    store(
        pipeline_engine,
        ("Alpha Motors recalls cars", "https://example.com/h2", ("EQ:NVDA",)),
        source="yahoo_rss",
    )
    old = NewsHeadline("An old story", "https://example.com/h3", NOW - timedelta(days=9), None, ())
    store_headlines(pipeline_engine, "finnhub", [old], NOW - timedelta(days=9))
    process_new_items(pipeline_engine, now=NOW)

    client = MockClient(lambda s, u: answer(tickers=[]))
    result = run(pipeline_engine, client)
    assert result["pending"] == 1 and result["calls"] == 1
    assert result["sentiment_rows"] == 0  # a valid answer naming no ticker stores no row
    assert rows(pipeline_engine, TRACES)[0]["outcome"] == "ok"


def test_hermes_fills_a_direction_only_where_the_code_left_zero(pipeline_engine):
    store(
        pipeline_engine,
        ("Powell speaks at conference", "https://example.com/i1", ("ETF:SPY",)),
        ("Fed delivers surprise rate hike", "https://example.com/i2", ("ETF:SPY",)),
    )
    process_new_items(pipeline_engine, now=NOW)
    client = MockClient(lambda s, u: answer(tickers=[], driver_direction=-1, key_facts=[]))
    assert run(pipeline_engine, client)["condensed"] == 2

    tags = {
        row["url"]: (row["direction"], row["tagged_by"])
        for row in rows(
            pipeline_engine,
            "SELECT n.url, e.direction, e.tagged_by FROM tagged_events e "
            "JOIN news_items n ON n.id = e.news_item_id",
        )
    }
    assert tags == {
        "https://example.com/i1": (-1, "hermes"),  # topic only: Hermes filled the direction
        "https://example.com/i2": (1, "code"),  # the code's direction is never overwritten
    }
