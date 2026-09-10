"""Phase 3 catalyst & sentiment: source parsers, schema validation, retry policy, merge and write-back.

Everything here is offline — no network, no live Ollama, no Supabase. The rule under test throughout is
plan §33: fail safe, never fake. A source that cannot answer is `skipped` or `failed`, never a silent
"no catalyst", and a model that will not produce valid JSON writes no row at all.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.llm import errors as llm_errors
from app.llm.prompts import PromptSpec, render
from app.llm.validate import validate
from app.main import app
from app.sentiment import run as enrich_run
from app.sentiment import sources as news_sources

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_settings().engine_api_token}"}
DAY = dt.date(2026, 9, 10)

CONDENSER_SCHEMA = {
    "type": "object",
    "required": ["catalyst_type", "sentiment_score", "key_facts", "one_sentence_reason", "freshness"],
    "properties": {
        "catalyst_type": {"type": "string", "enum": ["earnings", "guidance", "fda", "m_and_a", "contract",
                                                     "offering_dilution", "analyst_rating", "legal", "macro",
                                                     "sector_sympathy", "technical_level", "meme_social", "none"]},
        "sentiment_score": {"type": "number", "minimum": -1, "maximum": 1},
        "key_facts": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "one_sentence_reason": {"type": "string"},
        "freshness": {"type": "string", "enum": ["fresh", "stale", "unknown"]},
    },
    "additionalProperties": False,
}

GOOD = {"catalyst_type": "earnings", "sentiment_score": 0.6, "key_facts": ["beat and raised"],
        "one_sentence_reason": "It beat and raised.", "freshness": "fresh"}

YAHOO_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>AMD News</title>
  <item>
    <title>AMD beats and raises</title>
    <description>Datacenter revenue up 40%.</description>
    <link>https://example.test/a</link>
    <pubDate>Wed, 10 Sep 2026 11:05:00 GMT</pubDate>
  </item>
  <item>
    <title>Analysts lift AMD targets</title>
    <description>Several firms raise price targets.</description>
    <link>https://example.test/b</link>
    <pubDate>Tue, 09 Sep 2026 20:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Undated filler piece</title>
    <description>No date on this one.</description>
    <link>https://example.test/c</link>
  </item>
</channel></rss>
"""


# ---------- source parsing ----------

def test_yahoo_rss_parses_and_sorts_newest_first():
    items = news_sources.parse_yahoo_rss(YAHOO_RSS)
    assert len(items) == 3
    trimmed = news_sources._trim(items)
    assert [i.title for i in trimmed][:2] == ["AMD beats and raises", "Analysts lift AMD targets"]
    assert trimmed[-1].title == "Undated filler piece"      # undated sorts last, never dropped silently
    assert trimmed[0].ts.year == 2026 and trimmed[0].url == "https://example.test/a"


def test_yahoo_malformed_xml_is_a_failure_not_an_empty_result(monkeypatch):
    class R:
        text = "<rss><channel><item>truncated"
        status_code = 200

        def raise_for_status(self):
            return None

    monkeypatch.setattr(news_sources.httpx, "get", lambda *a, **kw: R())
    res = news_sources.fetch_yahoo("AMD")
    assert res.status == "failed" and "parse error" in res.error.lower()
    assert res.items == []


def test_finnhub_parses_items_and_skips_headless_entries(monkeypatch):
    payload = [
        {"headline": "AMD wins contract", "summary": "Big order.", "url": "u1",
         "datetime": int(dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC).timestamp())},
        {"headline": "   ", "summary": "blank headline is dropped", "datetime": 1},
        {"headline": "Undated note", "summary": "", "url": "u2"},
    ]

    class R:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(news_sources.httpx, "get", lambda *a, **kw: R())
    res = news_sources.fetch_finnhub("AMD", DAY)
    assert res.status == "ok" and len(res.items) == 2
    assert res.items[0].title == "AMD wins contract"


def test_finnhub_without_key_is_skipped_not_failed(monkeypatch):
    monkeypatch.setattr(news_sources.get_settings(), "finnhub_api_key", "", raising=False)
    res = news_sources.fetch_finnhub("AMD", DAY)
    assert res.status == "skipped" and "FINNHUB_API_KEY" in res.error


def test_reddit_without_credentials_is_skipped_and_says_why(monkeypatch):
    s = news_sources.get_settings()
    monkeypatch.setattr(s, "reddit_client_id", "", raising=False)
    monkeypatch.setattr(s, "reddit_client_secret", "", raising=False)
    res = news_sources.fetch_reddit("AMD")
    assert res.status == "skipped"
    assert "REDDIT_CLIENT_ID" in res.error and res.items == []


def test_unknown_source_is_reported_not_guessed():
    res = news_sources.fetch("bloomberg", "AMD", DAY)
    assert res.status == "failed" and "unknown source" in res.error


def test_format_items_respects_the_char_budget():
    items = [news_sources.NewsItem(ts=None, title="x" * 200) for _ in range(50)]
    out = news_sources.format_items(items, max_chars=500)
    assert len(out) <= 600 and out.count("\n") < 5
    assert news_sources.format_items([]) == "(no items)"


# ---------- schema validation ----------

def test_validator_accepts_a_good_payload():
    assert validate(GOOD, CONDENSER_SCHEMA) is None


@pytest.mark.parametrize("payload,expect", [
    ({**GOOD, "catalyst_type": "rumor"}, "not one of"),
    ({**GOOD, "sentiment_score": 1.4}, "above maximum"),
    ({**GOOD, "sentiment_score": -2}, "below minimum"),
    ({**GOOD, "key_facts": ["a", "b", "c", "d", "e", "f"]}, "maximum is 5"),
    ({k: v for k, v in GOOD.items() if k != "freshness"}, "missing required key"),
    ({**GOOD, "extra": 1}, "unexpected key"),
    ({**GOOD, "sentiment_score": True}, "expected number"),
    ({**GOOD, "key_facts": [1, 2]}, "expected string"),
])
def test_validator_rejects_bad_payloads(payload, expect):
    failure = validate(payload, CONDENSER_SCHEMA)
    assert failure is not None and expect in str(failure)


def test_render_requires_every_placeholder():
    with pytest.raises(KeyError):
        render("{{symbol}} on {{trade_date}}", {"symbol": "AMD"})
    assert render("{{symbol}} up", {"symbol": "AMD"}) == "AMD up"


# ---------- retry policy ----------

def _spec() -> PromptSpec:
    return PromptSpec(slug="sentiment_condenser", name="Sentiment condenser", tier="local", model="hermes3:8b",
                      version=1, system_prompt="sys", user_template="{{symbol}} {{source}} {{trade_date}} {{raw_items}}",
                      output_schema=CONDENSER_SCHEMA, origin="markdown")


def _vars() -> dict:
    return {"symbol": "AMD", "source": "finnhub", "trade_date": DAY.isoformat(), "raw_items": "- something"}


def test_invalid_then_valid_succeeds_on_the_retry(monkeypatch):
    from app import llm

    calls = []

    def fake_chat(system, user, *, model=None, timeout=120.0):
        calls.append(user)
        return {**GOOD, "catalyst_type": "rumor"} if len(calls) == 1 else GOOD

    monkeypatch.setattr(llm.ollama, "chat_json", fake_chat)
    payload, meta = llm.complete_json(_spec(), _vars())
    assert payload == GOOD and meta["attempts"] == 2
    assert "rejected by the schema validator" in calls[1]      # the complaint is fed back to the model


def test_two_invalid_answers_raise_and_write_nothing(monkeypatch):
    from app import llm

    monkeypatch.setattr(llm.ollama, "chat_json",
                        lambda system, user, **kw: {**GOOD, "sentiment_score": 9})
    with pytest.raises(llm_errors.LLMInvalidJSON):
        llm.complete_json(_spec(), _vars())


def test_condense_marks_source_failed_when_the_model_will_not_comply(monkeypatch):
    monkeypatch.setattr(news_sources, "fetch",
                        lambda *a, **kw: news_sources.SourceResult(
                            "finnhub", "ok", [news_sources.NewsItem(ts=None, title="headline")]))
    monkeypatch.setattr(enrich_run, "load_prompt", lambda slug: _spec())
    monkeypatch.setattr(enrich_run, "complete_json",
                        lambda spec, v: (_ for _ in ()).throw(llm_errors.LLMInvalidJSON("bad json twice")))
    cfg = {"lookback_days": 2, "reddit_subs": (), "max_items_per_source": 20}
    outcome, _items = enrich_run.condense_symbol_source("AMD", "finnhub", DAY, cfg)
    assert outcome.status == "failed" and "bad json" in outcome.error
    assert enrich_run.merge_catalyst([outcome]) is None       # nothing invented from a failure


# ---------- merging ----------

def test_merge_prefers_the_agreed_non_none_catalyst_and_averages_the_score():
    a = enrich_run.SourceOutcome("finnhub", "ok", {**GOOD, "sentiment_score": 0.8})
    b = enrich_run.SourceOutcome("yahoo", "ok", {**GOOD, "sentiment_score": 0.4,
                                                 "one_sentence_reason": "weaker take"})
    c = enrich_run.SourceOutcome("reddit", "skipped", error="no credentials")
    block = enrich_run.merge_catalyst([a, b, c])
    assert block["catalyst_type"] == "earnings"
    assert block["sentiment_score"] == pytest.approx(0.6)
    assert block["one_sentence_reason"] == "It beat and raised."     # from the strongest source
    assert block["sources"] == ["finnhub", "yahoo"]
    assert block["freshness"] == "fresh"


def test_merge_ignores_none_when_a_real_catalyst_exists():
    a = enrich_run.SourceOutcome("finnhub", "ok", {**GOOD, "catalyst_type": "fda", "sentiment_score": 0.9})
    b = enrich_run.SourceOutcome("yahoo", "ok", {**GOOD, "catalyst_type": "none", "sentiment_score": 0.0})
    block = enrich_run.merge_catalyst([a, b])
    assert block["catalyst_type"] == "fda" and block["sentiment_score"] == pytest.approx(0.9)


def test_merge_returns_none_when_every_source_failed_or_skipped():
    outcomes = [enrich_run.SourceOutcome("finnhub", "failed", error="HTTP 500"),
                enrich_run.SourceOutcome("reddit", "skipped", error="no credentials")]
    assert enrich_run.merge_catalyst(outcomes) is None


def test_catalyst_bonus_uses_magnitude_and_requires_freshness():
    fresh_neg = {"catalyst_type": "offering_dilution", "sentiment_score": -0.8, "freshness": "fresh"}
    fresh_pos = {"catalyst_type": "earnings", "sentiment_score": 0.8, "freshness": "fresh"}
    assert enrich_run.catalyst_bonus(fresh_neg) == enrich_run.catalyst_bonus(fresh_pos) == pytest.approx(1.6)
    assert enrich_run.catalyst_bonus({**fresh_pos, "freshness": "stale"}) == 0.0
    assert enrich_run.catalyst_bonus({"catalyst_type": "none", "sentiment_score": 0.9, "freshness": "fresh"}) == 0.0
    assert enrich_run.catalyst_bonus(None) == 0.0
    assert enrich_run.catalyst_bonus(fresh_pos, weight=1.0) == pytest.approx(0.8)


# ---------- write-back ----------

class _FakeTable:
    def __init__(self, rows, sink):
        self._rows = rows
        self._sink = sink

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def in_(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()

    def upsert(self, rows, **kw):
        self._sink.extend(rows)
        return self


def test_write_back_fills_catalyst_and_leaves_passed_and_gates_alone(monkeypatch):
    """Decisions 21 and 22: enrichment adds information; it satisfies no gate and flips no verdict."""
    gates = ["float_shares", "one_sentence_in_play_test"]
    rows = [
        {"strategy_id": "s1", "scan_date": DAY.isoformat(), "run_kind": "morning", "symbol": "AMD",
         "run_at": "t", "passed": True, "rank": 2, "reject_reason": None,
         "metrics": {"score": 10.0, "pending_gates": list(gates)}},
        {"strategy_id": "s1", "scan_date": DAY.isoformat(), "run_kind": "morning", "symbol": "PLUG",
         "run_at": "t", "passed": False, "rank": None, "reject_reason": "gap too small",
         "metrics": {"score": 1.0, "pending_gates": list(gates)}},
    ]
    sink: list[dict] = []
    monkeypatch.setattr(enrich_run, "get_client",
                        lambda: type("C", (), {"table": lambda self, name: _FakeTable(rows, sink)})())

    blocks = {"AMD": {"catalyst_type": "earnings", "sentiment_score": 0.5, "freshness": "fresh"},
              "PLUG": {"catalyst_type": "none", "sentiment_score": 0.0, "freshness": "unknown"}}
    updated, _ = enrich_run.write_back(DAY, blocks)
    assert updated == 2
    by = {r["symbol"]: r for r in sink}

    amd = by["AMD"]
    assert amd["passed"] is True                                    # untouched
    assert amd["metrics"]["catalyst"]["catalyst_type"] == "earnings"
    assert amd["metrics"]["pending_gates"] == gates                  # every gate survives enrichment
    assert amd["metrics"]["catalyst_bonus"] == pytest.approx(1.0)
    assert amd["metrics"]["score"] == pytest.approx(11.0)
    assert amd["rank"] == 1                                          # re-ranked on the adjusted score

    plug = by["PLUG"]
    assert plug["passed"] is False and plug["reject_reason"] == "gap too small"
    assert plug["metrics"]["pending_gates"] == gates
    assert plug["metrics"]["catalyst_bonus"] == 0.0                  # 'none' earns nothing
    assert plug["metrics"]["score"] == pytest.approx(1.0)
    assert plug["rank"] is None                                      # rejected rows stay unranked


def test_write_back_is_idempotent_on_the_score(monkeypatch):
    """Running enrichment twice must not double-count the bonus."""
    rows = [{"strategy_id": "s1", "scan_date": DAY.isoformat(), "run_kind": "morning", "symbol": "AMD",
             "run_at": "t", "passed": True, "rank": 1, "reject_reason": None,
             "metrics": {"score": 11.0, "catalyst_bonus": 1.0, "pending_gates": []}}]
    sink: list[dict] = []
    monkeypatch.setattr(enrich_run, "get_client",
                        lambda: type("C", (), {"table": lambda self, name: _FakeTable(rows, sink)})())
    blocks = {"AMD": {"catalyst_type": "earnings", "sentiment_score": 0.5, "freshness": "fresh"}}
    enrich_run.write_back(DAY, blocks)
    assert sink[0]["metrics"]["score"] == pytest.approx(11.0)        # 10.0 base + 1.0, not 12.0


def test_write_back_with_no_blocks_touches_nothing(monkeypatch):
    monkeypatch.setattr(enrich_run, "get_client", lambda: pytest.fail("must not query with no blocks"))
    assert enrich_run.write_back(DAY, {}) == (0, 0)


# ---------- candidate selection ----------

def test_candidates_take_top_n_per_strategy_and_dedupe(monkeypatch):
    scans = [{"strategy_id": "a", "symbol": s, "rank": i, "metrics": {}}
             for i, s in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"], start=1)]
    scans += [{"strategy_id": "b", "symbol": s, "rank": i, "metrics": {}}
              for i, s in enumerate(["CCC", "GGG"], start=1)]
    strategies = [{"id": "a", "code": "S1"}, {"id": "b", "code": "S3"}]

    class C:
        def table(self, name):
            return _FakeTable(strategies if name == "strategy_configs" else scans, [])

    monkeypatch.setattr(enrich_run, "get_client", lambda: C())
    union, per_strategy = enrich_run.candidates_from_scans(DAY, cap=5)
    assert per_strategy["S1"] == ["AAA", "BBB", "CCC", "DDD", "EEE"]   # capped at 5, FFF dropped
    assert per_strategy["S3"] == ["CCC", "GGG"]
    assert union == ["AAA", "BBB", "CCC", "DDD", "EEE", "GGG"]          # CCC deduped across strategies


# ---------- job + endpoints ----------

def _offline_config(monkeypatch, **overrides):
    cfg = {"sources": ("finnhub",), "candidates_per_strategy": 5, "lookback_days": 2,
           "reddit_subs": (), "max_items_per_source": 20, "catalyst_weight": 2.0} | overrides
    monkeypatch.setattr(enrich_run, "config", lambda: cfg)
    return cfg


def test_run_aborts_loudly_when_ollama_is_down(monkeypatch):
    _offline_config(monkeypatch)
    monkeypatch.setattr(enrich_run.ollama_client, "available", lambda: (False, "Ollama unreachable"))
    sent: list[str] = []
    monkeypatch.setattr(enrich_run, "send_telegram", lambda t: sent.append(t) or True)
    res = enrich_run.run_enrich(symbols=["AMD"], notify=True)
    assert res.errors and "Ollama unreachable" in res.errors[0]
    assert res.news_rows_written == 0 and res.scan_rows_updated == 0
    assert sent and "ABORTED" in sent[0]                      # the owner is told, not left with a silent no-op


def test_run_dry_run_writes_nothing_but_reports_the_catalyst(monkeypatch):
    monkeypatch.setattr(enrich_run.ollama_client, "available", lambda: (True, "ready"))
    _offline_config(monkeypatch)
    monkeypatch.setattr(enrich_run, "load_prompt", lambda slug: _spec())
    monkeypatch.setattr(news_sources, "fetch",
                        lambda *a, **kw: news_sources.SourceResult(
                            "finnhub", "ok", [news_sources.NewsItem(ts=None, title="AMD beats")]))
    monkeypatch.setattr(enrich_run, "complete_json", lambda spec, v: (GOOD, {"attempts": 1}))
    monkeypatch.setattr(enrich_run, "get_client", lambda: pytest.fail("dry_run must not write"))
    sent: list[str] = []
    monkeypatch.setattr(enrich_run, "send_telegram", lambda t: sent.append(t) or True)

    res = enrich_run.run_enrich(symbols=["AMD"], dry_run=True, notify=True)
    assert not res.errors, res.errors
    assert res.news_rows_written == 1 and res.scan_rows_updated == 0
    assert "dry_run" in " ".join(res.notes)
    assert res.detail[0]["catalyst"]["catalyst_type"] == "earnings"
    assert "earnings" in sent[0]


def test_enrich_endpoints_require_auth_and_validate_input(monkeypatch):
    assert client.post("/enrich/run").status_code == 401
    assert client.get("/enrich/status").status_code == 401
    assert client.post("/enrich/run?sources=bloomberg", headers=AUTH).status_code == 422
    assert client.post("/enrich/run?cap=99", headers=AUTH).status_code == 422

    monkeypatch.setattr(enrich_run, "run_enrich",
                        lambda **kw: enrich_run.EnrichResult(trade_date="2026-09-10", as_of="08:05"))
    r = client.post("/enrich/run?symbols=AMD&dry_run=true&notify=false", headers=AUTH)
    assert r.status_code == 200 and r.json()["trade_date"] == "2026-09-10"
