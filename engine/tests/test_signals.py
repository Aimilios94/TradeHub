"""Phase 4 decision node: context assembly, the in-play gate, plan validation, briefing, endpoints.

Offline throughout — no network, no OpenRouter, no Supabase. The property under test everywhere is that
judgment can propose but not bypass: a plan that breaks a rule is rejected with a reason, a gate failure
is never an implicit pass, and nothing in this phase can execute anything.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.broker.account import AccountSnapshot
from app.config import get_settings
from app.llm import errors as llm_errors
from app.llm import schema_contract
from app.llm.prompts import PromptSpec
from app.llm.validate import validate
from app.main import app
from app.signals import briefing as briefing_mod
from app.signals import context as ctx
from app.signals import gate as gate_mod
from app.signals import plan as plan_mod
from app.signals import run as signals_run

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_settings().engine_api_token}"}
DAY = dt.date(2026, 9, 10)

ACCT = AccountSnapshot(ok=True, equity=100_000.0, buying_power=400_000.0, cash=100_000.0, positions=[])
RISK = {"risk_per_trade_pct": 0.5, "max_concurrent": 2, "spread_ceiling_pct": 2.0, "order_types": ["limit"]}

CANDIDATE = {
    "symbol": "TNON",
    "metrics": {"price": 3.70, "atr": 1.0653, "gap_pct": 51.6, "spread_pct": 0.4},
    "levels": {
        "premarket_high": 4.22, "premarket_low": 3.07, "prior_high": 3.67, "prior_low": 2.40,
        "prior_close": 2.44, "high_20d": 17.84, "low_20d": 2.40, "high_60d": 17.84, "low_60d": 0.125,
        "round_numbers": [3.6, 3.7, 3.8], "vwap": None, "poc": None, "vah": None, "val": None,
        "atr_extensions": {"plus_1_atr": 4.77, "plus_2_atr": 5.83, "minus_1_atr": 2.63},
    },
    "catalysts": [{"source": "yahoo", "catalyst_type": "contract", "sentiment_score": 0.8,
                   "one_sentence_reason": "It repaid its senior secured debt early.", "freshness": "fresh"}],
    "max_size_by_risk": {"shares": 469, "risk_dollars": 500.0, "assumed_stop_distance": 1.0653},
}

GOOD_PLAN = {"symbol": "TNON", "decision": "trade", "direction": "long", "entry_type": "limit",
             "entry": 4.22, "stop": 3.07, "target": 4.77, "size": 400, "confidence": 70,
             "in_play_sentence": "TNON is in play because it repaid debt early.", "reasoning": "..."}


# ---------- level and sizing maths ----------

def test_round_numbers_adapt_to_price_and_never_come_back_empty_for_low_priced_names():
    assert ctx.round_numbers(3.70)          # a 0.50 grid would return nothing here
    assert all(3.5 < v < 3.9 for v in ctx.round_numbers(3.70))
    assert 250.0 in ctx.round_numbers(250.0)
    assert ctx.round_numbers(None) == [] and ctx.round_numbers(0) == []


def test_atr_extensions_sit_either_side_and_skip_negative_prices():
    ext = ctx.atr_extensions(10.0, 2.0)
    assert ext["plus_1_atr"] == 12.0 and ext["minus_1_atr"] == 8.0
    assert ext["plus_0_5_atr"] == 11.0 and ext["minus_0_5_atr"] == 9.0
    cheap = ctx.atr_extensions(1.0, 2.0)
    assert not any(k.startswith("minus_1") for k in cheap)     # would be negative
    assert ctx.atr_extensions(None, 2.0) == {} and ctx.atr_extensions(10.0, 0) == {}


def test_stop_options_pair_each_stop_with_a_size_that_respects_the_budget():
    rows = ctx.stop_options(3.70, CANDIDATE["levels"], 100_000.0, 0.5, "long")
    assert rows and rows == sorted(rows, key=lambda r: r["stop_distance"])
    for r in rows:
        assert r["stop"] < 3.70                                # long stops sit below price
        assert r["risk_at_max_shares"] <= 500.0 + 1e-6         # never over the budget
        assert r["max_shares"] * r["stop_distance"] <= 500.0 + 1e-6
    assert {"premarket_low", "prior_low", "low_20d"} & {r["basis"] for r in rows}


def test_stop_options_for_a_short_sit_above_price():
    rows = ctx.stop_options(3.70, CANDIDATE["levels"], 100_000.0, 0.5, "short")
    assert rows and all(r["stop"] > 3.70 for r in rows)


def test_max_size_by_risk_reports_unknown_rather_than_guessing():
    out = ctx.max_size_by_risk(None, 0.5, 3.7, 1.0)
    assert out["shares"] is None and "unknown" in out["basis"]


# ---------- advisory pre-check ----------

def test_advisory_precheck_is_advisory_and_flags_the_kill_switch():
    out = ctx.advisory_precheck(symbol="TNON", direction_hint="long", acct=ACCT, stats={},
                                spread=0.4, risk=RISK, kill_switch=True)
    assert out["advisory"] is True and out["verdict"] == "red"
    assert any(c["check"] == "kill_switch" and c["verdict"] == "red" for c in out["checks"])


def test_advisory_precheck_reports_unknown_when_the_broker_is_down():
    down = AccountSnapshot(ok=False, error="ConnectError")
    out = ctx.advisory_precheck(symbol="TNON", direction_hint="long", acct=down, stats={},
                                spread=None, risk=RISK, kill_switch=False)
    assert out["verdict"] == "yellow"                      # unknown, never silently green
    verdicts = {c["check"]: c["verdict"] for c in out["checks"]}
    assert verdicts["max_concurrent"] == "unknown" and verdicts["spread"] == "unknown"


def test_advisory_precheck_blocks_a_short_that_is_not_easy_to_borrow():
    out = ctx.advisory_precheck(symbol="TNON", direction_hint="short", acct=ACCT,
                                stats={"shortable": True, "easy_to_borrow": False},
                                spread=0.4, risk=RISK, kill_switch=False)
    assert any(c["check"] == "shortable" and c["verdict"] == "red" for c in out["checks"])


def test_advisory_precheck_flags_a_spread_over_the_ceiling():
    out = ctx.advisory_precheck(symbol="TNON", direction_hint="long", acct=ACCT, stats={},
                                spread=5.0, risk=RISK, kill_switch=False)
    assert any(c["check"] == "spread" and c["verdict"] == "red" for c in out["checks"])


# ---------- plan validation: the guard that matters ----------

def test_a_clean_plan_passes():
    assert plan_mod.validate_plan(GOOD_PLAN, CANDIDATE, risk=RISK) == []


def test_watch_and_skip_carry_no_prices_to_check():
    for decision in ("watch", "skip"):
        assert plan_mod.validate_plan({"symbol": "TNON", "decision": decision}, CANDIDATE, risk=RISK) == []


@pytest.mark.parametrize("override,expect", [
    ({"target": 99.0}, "not within"),                       # hallucinated level
    ({"entry": 99.0}, "not within"),
    ({"stop": 4.90}, "not below entry"),                    # wrong side for a long
    ({"direction": "short", "stop": 3.07}, "not above entry"),
    ({"size": 100_000}, "budget"),                          # over the risk budget
    ({"size": 0}, "not positive"),
    ({"entry_type": "buy_stop"}, "allowed order types"),    # S1-style limit-only strategy
    ({"confidence": 95}, "exceeds the 85"),
    ({"direction": None}, "without a valid direction"),
    ({"stop": None}, "without stop"),
])
def test_bad_plans_are_rejected_with_a_reason(override, expect):
    problems = plan_mod.validate_plan({**GOOD_PLAN, **override}, CANDIDATE, risk=RISK)
    assert problems and any(expect in p for p in problems), problems


def test_size_is_checked_against_the_chosen_stop_not_the_one_atr_estimate():
    """A tighter stop legitimately allows MORE shares than the 1-ATR cap suggests."""
    tight = {**GOOD_PLAN, "stop": 3.61, "size": 800}        # 0.61 x 800 = 488 <= 500 budget
    cand = {**CANDIDATE, "levels": {**CANDIDATE["levels"], "round_numbers": [3.6, 3.61, 3.7]}}
    assert plan_mod.validate_plan(tight, cand, risk=RISK) == []
    too_big = {**tight, "size": 1200}                       # 0.61 x 1200 = 732 > 500
    problems = plan_mod.validate_plan(too_big, cand, risk=RISK)
    assert any("budget" in p and "max" in p for p in problems)


def test_a_candidate_with_no_levels_cannot_be_planned():
    bare = {**CANDIDATE, "levels": {}, "metrics": {}}
    problems = plan_mod.validate_plan(GOOD_PLAN, bare, risk=RISK)
    assert any("no levels" in p for p in problems)


def test_strategy_rules_state_the_rules_plans_break_most():
    text = plan_mod.strategy_rules_text({"code": "S1", "notes": "Limit orders ONLY.",
                                         "risk": RISK, "entry_conditions": {"all": []}})
    assert "ALLOWED ORDER TYPES" in text and "limit" in text
    assert "stop_options" in text and "COMPLETE PLAN" in text


def test_signal_row_marks_a_failed_plan_rejected_and_keeps_the_reasons():
    row = plan_mod.signal_row(plan={**GOOD_PLAN, "target": 99.0}, candidate=CANDIDATE, strategy_id="s1",
                              day=DAY, scan_id=None, template_id=None,
                              problems=["target 99.0 is not within 3.0% of any supplied level"],
                              meta={"model": "m"}, raw={})
    assert row["status"] == "rejected"
    assert row["reject_reason"].startswith("llm_validation_failed:")
    assert row["evidence"]["validation_problems"]


def test_signal_row_for_a_skip_is_rejected_with_hermes_own_reason():
    row = plan_mod.signal_row(plan={"symbol": "TNON", "decision": "skip"}, candidate=CANDIDATE,
                              strategy_id="s1", day=DAY, scan_id=None, template_id=None, problems=[],
                              meta={}, raw={})
    assert row["status"] == "rejected" and "skip" in row["reject_reason"]


# ---------- the in-play gate ----------

def test_candidates_without_a_catalyst_are_rejected_by_code_without_a_model_call(monkeypatch):
    monkeypatch.setattr(gate_mod, "complete_json",
                        lambda *a, **kw: pytest.fail("must not call the model when nothing has a catalyst"))
    verdicts, meta = gate_mod.run_gate([{"symbol": "SKUU", "metrics": {}, "catalysts": []}], DAY)
    assert verdicts["SKUU"]["verdict"] == "reject" and verdicts["SKUU"]["decided_by"] == "code"
    assert meta["auto_rejected"] == 1


def test_gate_keeps_code_rejections_alongside_model_verdicts(monkeypatch):
    monkeypatch.setattr(gate_mod, "load_prompt", lambda slug: _spec({"type": "object"}))
    monkeypatch.setattr(gate_mod, "complete_json", lambda spec, v: (
        {"results": [{"symbol": "TNON", "verdict": "pass", "sentence": "in play", "strength": "strong"}]},
        {"model": "m", "attempts": 1}))
    cands = [CANDIDATE, {"symbol": "SKUU", "metrics": {}, "catalysts": []}]
    verdicts, _meta = gate_mod.run_gate(cands, DAY)
    assert verdicts["TNON"]["verdict"] == "pass" and verdicts["TNON"]["decided_by"] == "hermes"
    assert verdicts["SKUU"]["verdict"] == "reject" and verdicts["SKUU"]["decided_by"] == "code"
    assert [c["symbol"] for c in gate_mod.survivors(cands, verdicts)] == ["TNON"]


def test_a_symbol_the_model_silently_drops_is_not_treated_as_a_pass(monkeypatch):
    monkeypatch.setattr(gate_mod, "load_prompt", lambda slug: _spec({"type": "object"}))
    monkeypatch.setattr(gate_mod, "complete_json", lambda spec, v: ({"results": []}, {"attempts": 1}))
    verdicts, _ = gate_mod.run_gate([CANDIDATE], DAY)
    assert verdicts["TNON"]["verdict"] == "reject"
    assert "no verdict" in verdicts["TNON"]["reject_reason"]


def test_a_gate_failure_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(gate_mod, "load_prompt", lambda slug: _spec({"type": "object"}))
    monkeypatch.setattr(gate_mod, "complete_json",
                        lambda spec, v: (_ for _ in ()).throw(llm_errors.LLMInvalidJSON("bad")))
    verdicts, _meta, err = gate_mod.safe_run_gate([CANDIDATE], DAY)
    assert verdicts == {} and err and "gate failed" in err


def test_gate_write_back_records_the_verdict_and_never_flips_passed(monkeypatch):
    rows = [{"strategy_id": "s1", "scan_date": DAY.isoformat(), "run_kind": "morning", "symbol": "TNON",
             "run_at": "t", "passed": True, "rank": 1, "reject_reason": None,
             "metrics": {"score": 10.0, "pending_gates": ["one_sentence_in_play_test"]}}]
    sink: list[dict] = []
    monkeypatch.setattr(gate_mod, "get_client",
                        lambda: type("C", (), {"table": lambda self, n: _FakeTable(rows, sink)})())
    gate_mod.write_verdicts(DAY, {"TNON": {"verdict": "reject", "sentence": "", "strength": "weak",
                                           "reject_reason": "stale news", "decided_by": "hermes"}}, "s1")
    assert sink[0]["passed"] is True                                     # decision 29
    assert sink[0]["metrics"]["in_play"]["verdict"] == "reject"
    assert sink[0]["metrics"]["pending_gates"] == ["one_sentence_in_play_test"]


# ---------- briefing ----------

def test_briefing_payload_carries_no_sizing_or_guardian_block():
    rows = briefing_mod.watchlist_payload([CANDIDATE])
    assert rows[0]["symbol"] == "TNON"
    assert "max_size_by_risk" not in rows[0] and "guardian_precheck" not in rows[0]
    assert rows[0]["premarket"]["price"] == 3.70


def test_market_context_reports_missing_index_data_instead_of_inventing_it(monkeypatch):
    monkeypatch.setattr(briefing_mod.alpaca, "get_snapshots",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("feed down")))
    out = briefing_mod.market_context_inputs(DAY, [])
    assert "indices_error" in out and out["vix"] is None


def test_market_context_reads_the_real_snapshot_shape(monkeypatch):
    monkeypatch.setattr(briefing_mod.alpaca, "get_snapshots", lambda *a, **kw: {
        "SPY": {"trade": {"price": 110.0}, "daily": {"o": 108.0, "h": 111.0, "l": 107.0, "c": 110.0},
                "prev_daily": {"c": 100.0}}})
    out = briefing_mod.market_context_inputs(DAY, [])
    assert out["indices"]["SPY"]["change_pct"] == pytest.approx(10.0)


# ---------- LLM layer ----------

def test_schema_contract_shows_the_model_the_schema_and_the_omission_rule():
    text = schema_contract({"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}})
    assert "required" in text and "OMITTED" in text
    assert '"type":"object"' in text.replace(" ", "")


def test_optional_key_sent_as_null_counts_as_omitted_but_required_null_does_not():
    schema = {"type": "object", "required": ["decision"],
              "properties": {"decision": {"type": "string"}, "size": {"type": "integer"}}}
    assert validate({"decision": "skip", "size": None}, schema) is None
    assert validate({"decision": None, "size": 5}, schema) is not None


def test_unknown_tier_is_refused():
    with pytest.raises(llm_errors.LLMError):
        from app.llm import provider_for
        provider_for("magic")


# ---------- orchestrator + endpoints ----------

def test_run_aborts_when_the_hosted_tier_is_unavailable(monkeypatch):
    monkeypatch.setattr(signals_run.openrouter, "available", lambda: (False, "credit exhausted"))
    sent: list[str] = []
    monkeypatch.setattr(signals_run, "send_telegram", lambda t: sent.append(t) or True)
    res = signals_run.run_signals(notify=True)
    assert res.errors and "credit exhausted" in res.errors[0]
    assert res.signals_written == 0 and sent and "ABORTED" in sent[0]


def test_summary_always_says_nothing_was_executed():
    res = signals_run.SignalsResult(trade_date="2026-09-10", as_of="08:15")
    assert "nothing is executed" in res.summary().lower()


def test_signals_endpoints_require_auth_and_validate_input(monkeypatch):
    assert client.post("/signals/plan").status_code == 401
    assert client.get("/signals/status").status_code == 401
    assert client.post("/signals/plan?cap=99", headers=AUTH).status_code == 422
    monkeypatch.setattr(signals_run, "run_signals",
                        lambda **kw: signals_run.SignalsResult(trade_date="2026-09-10", as_of="08:15"))
    r = client.post("/signals/plan?dry_run=true&notify=false", headers=AUTH)
    assert r.status_code == 200 and r.json()["trade_date"] == "2026-09-10"


# ---------- helpers ----------

def _spec(schema: dict) -> PromptSpec:
    return PromptSpec(slug="t", name="t", tier="hosted", model="m", version=1, system_prompt="s",
                      user_template="{{trade_date}} {{candidates_json}}", output_schema=schema,
                      origin="markdown")


class _FakeTable:
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink

    def select(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def in_(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()

    def upsert(self, rows, **kw):
        self._sink.extend(rows)
        return self
