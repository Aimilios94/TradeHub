"""Phase 8 — hardening. Offline: no network, no Supabase, no real clock.

The tests that matter most here are the ones that would go quiet rather than loud if the code regressed:
a retry that silently repeats an order, a staleness check that passes because it could not measure
anything, a Guardian check that has lost its ability to block, an acceptance criterion that reports
green because the query returned nothing.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.guardian import gate as guardian
from app.ops import acceptance, drill, failures, preflight, resilience

ET = ZoneInfo("America/New_York")


# ============================================================ resilience: what gets retried

class Boom(Exception):
    def __init__(self, status=None, msg="boom"):
        self.status_code = status
        super().__init__(msg)


def test_transient_failures_are_the_only_ones_retried():
    """A 429 or a timeout can succeed next time. A 401 cannot — retrying it burns the rate limit and
    delays the alert that tells the owner the key is wrong."""
    for status in (408, 425, 429, 500, 502, 503, 504):
        assert resilience.is_transient(Boom(status)) is True, status
    for status in (400, 401, 403, 404, 409, 422):
        assert resilience.is_transient(Boom(status)) is False, status
    assert resilience.is_transient(TimeoutError("read timed out")) is True
    assert resilience.is_transient(ValueError("symbol NOTREAL is not valid")) is False


def test_a_permanent_failure_is_raised_immediately_not_after_three_attempts():
    calls, slept = [], []

    def fn():
        calls.append(1)
        raise Boom(401, "invalid key")

    with pytest.raises(Boom):
        resilience.call("finnhub", fn, sleeper=slept.append)
    assert len(calls) == 1 and not slept


def test_a_transient_failure_is_retried_then_reported_with_the_real_cause():
    calls, slept = [], []

    def fn():
        calls.append(1)
        raise Boom(503, "upstream down")

    with pytest.raises(resilience.RetriesExhausted) as e:
        resilience.call("finnhub", fn, max_attempts=3, sleeper=slept.append, jitter=lambda: 1.0)
    assert len(calls) == 3
    assert len(slept) == 2 and slept == [1.0, 2.0]          # exponential, jitter pinned at its ceiling
    assert "upstream down" in str(e.value) and e.value.last.status_code == 503


def test_a_retry_that_eventually_succeeds_returns_the_value():
    seq = [Boom(429), Boom(429), "ok"]

    def fn():
        v = seq.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    assert resilience.call("finnhub", fn, max_attempts=3, sleeper=lambda _: None) == "ok"


def test_the_providers_own_error_survives_a_retry_when_the_caller_translates_it():
    """`app/llm/openrouter.py` turns an httpx 402 into "credit exhausted". That message is what reaches
    the owner, so the retry wrapper must not replace it with a generic failure."""
    def fn():
        raise Boom(503, "gateway")

    with pytest.raises(Boom) as e:
        resilience.call_preserving_error("openrouter", fn, max_attempts=2, sleeper=lambda _: None)
    assert "gateway" in str(e.value)


def test_retry_after_header_beats_our_guess():
    class WithHeader(Exception):
        status_code = 429
        response = type("R", (), {"status_code": 429, "headers": {"Retry-After": "7"}})()

    assert resilience.retry_after_seconds(WithHeader()) == 7.0
    slept = []
    with pytest.raises(resilience.RetriesExhausted):
        resilience.call("telegram", lambda: (_ for _ in ()).throw(WithHeader()),
                        max_attempts=2, sleeper=slept.append)
    assert slept == [7.0]


def test_backoff_is_bounded_and_jittered():
    assert resilience.backoff_delay(1, 1.0, jitter=lambda: 1.0) == 1.0
    assert resilience.backoff_delay(4, 1.0, jitter=lambda: 1.0) == 8.0
    assert resilience.backoff_delay(20, 1.0, jitter=lambda: 1.0) == resilience.MAX_DELAY_S
    assert resilience.backoff_delay(4, 1.0, jitter=lambda: 0.0) == 0.0


def test_the_token_bucket_paces_calls_past_the_burst():
    b = resilience.TokenBucket(per_minute=60, burst=2)      # one token per second after the first two
    t = [0.0]
    assert b.take(now=lambda: t[0]) == 0.0
    assert b.take(now=lambda: t[0]) == 0.0
    third = b.take(now=lambda: t[0])
    assert third == pytest.approx(1.0)
    fourth = b.take(now=lambda: t[0])
    assert fourth == pytest.approx(2.0)                     # queued behind the third, not colliding with it


def test_an_order_submission_is_never_placed_behind_a_retry():
    """The single most dangerous thing Phase 8 could have added. A broker timeout cannot be told apart
    from a silent success, so a retry risks buying the position twice."""
    with pytest.raises(resilience.WriteNotRetryable) as e:
        resilience.guard_write("broker.orders.submit")
    assert "duplicate" in str(e.value) and "orders/sync" in str(e.value)


def test_the_submit_path_carries_no_retry_wrapper():
    """Read the source rather than trust the memory of having decided this."""
    import inspect

    from app.broker import orders as broker_orders

    src = inspect.getsource(broker_orders.submit)
    assert "resilience" not in src, "a retry around submit_order can duplicate a position"
    assert "not idempotent" in src


# ============================================================ failures: loud once, recorded always

def test_the_row_is_written_every_time_even_when_the_alert_is_suppressed(monkeypatch):
    rows, sent = [], []
    monkeypatch.setattr(guardian, "log_event", lambda **kw: rows.append(kw))
    monkeypatch.setattr("app.notify.send_telegram", lambda t: sent.append(t) or True)
    failures.reset_alert_state()

    for _ in range(5):
        failures.failure("data_stale", "SPY quote 412s old", key="SPY", symbol="SPY",
                         cooldown_minutes=30)
    assert len(rows) == 5, "every failure must leave evidence — the count is the signal"
    assert len(sent) == 1, "a broken feed fails on every symbol; one ping, not five"


def test_the_cooldown_expires():
    failures.reset_alert_state()
    t0 = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.UTC)
    assert failures.should_alert("failure", "k", now=t0, cooldown_minutes=30) is True
    assert failures.should_alert("failure", "k", now=t0 + dt.timedelta(minutes=29), cooldown_minutes=30) is False
    assert failures.should_alert("failure", "k", now=t0 + dt.timedelta(minutes=31), cooldown_minutes=30) is True


def test_different_keys_alert_separately():
    failures.reset_alert_state()
    t0 = dt.datetime(2026, 9, 11, 12, 0, tzinfo=dt.UTC)
    assert failures.should_alert("failure", "AAPL", now=t0) is True
    assert failures.should_alert("failure", "TSLA", now=t0) is True


def test_a_failure_in_the_reporter_does_not_replace_the_failure_being_reported(monkeypatch):
    def explode(**kw):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(guardian, "log_event", explode)
    monkeypatch.setattr("app.notify.send_telegram", lambda t: True)
    failures.reset_alert_state()
    out = failures.failure("failure", "the original problem")
    assert out["recorded"] is False and out["alerted"] is True


# ============================================================ preflight: §33 as code

def _check(name, status):
    return preflight.Check(name, status, "test")


def test_unknown_blocks_an_entry_exactly_as_red_does():
    """The failure §33 exists to prevent: "we could not reach the broker, assume it is fine"."""
    for status in (preflight.RED, preflight.UNKNOWN):
        p = preflight.Preflight(checks=[_check("broker", status)], computed_at="")
        assert p.can_enter is False, status
    for status in (preflight.GREEN, preflight.YELLOW, preflight.NA):
        p = preflight.Preflight(checks=[_check("broker", status)], computed_at="")
        assert p.can_enter is True, status


def test_the_overall_status_is_the_worst_check():
    p = preflight.Preflight(checks=[_check("a", preflight.GREEN), _check("b", preflight.YELLOW),
                                    _check("c", preflight.RED)], computed_at="")
    assert p.status == preflight.RED and p.blocking and p.warnings


def test_a_stale_quote_is_red_and_a_fresh_one_is_green(monkeypatch):
    now = dt.datetime(2026, 9, 11, 10, 0, tzinfo=ET)          # inside the session
    monkeypatch.setattr(preflight, "policy", lambda: {"data_stale_seconds": 60,
                                                      "monitor_heartbeat_seconds": 180})
    monkeypatch.setattr(preflight, "quote_age_seconds", lambda s, now=None: (412.0, "bid=1 ask=2"))
    assert preflight.check_data_feed("SPY", now=now).status == preflight.RED
    monkeypatch.setattr(preflight, "quote_age_seconds", lambda s, now=None: (3.0, "bid=1 ask=2"))
    assert preflight.check_data_feed("SPY", now=now).status == preflight.GREEN
    monkeypatch.setattr(preflight, "quote_age_seconds", lambda s, now=None: (45.0, "bid=1 ask=2"))
    assert preflight.check_data_feed("SPY", now=now).status == preflight.YELLOW


def test_an_unmeasurable_quote_is_unknown_not_green(monkeypatch):
    now = dt.datetime(2026, 9, 11, 10, 0, tzinfo=ET)
    monkeypatch.setattr(preflight, "policy", lambda: {"data_stale_seconds": 60})
    monkeypatch.setattr(preflight, "quote_age_seconds", lambda s, now=None: (None, "connection refused"))
    c = preflight.check_data_feed("SPY", now=now)
    assert c.status == preflight.UNKNOWN and c.blocks is True


def test_staleness_is_not_measured_when_the_tape_is_not_moving():
    """A quote is SUPPOSED to be hours old at 22:00. A check that goes red every night is a check the
    owner learns to ignore."""
    for when in (dt.datetime(2026, 9, 11, 22, 0, tzinfo=ET),      # after hours
                 dt.datetime(2026, 9, 11, 3, 0, tzinfo=ET),       # before pre-market
                 dt.datetime(2026, 9, 12, 11, 0, tzinfo=ET)):     # a Saturday
        c = preflight.check_data_feed("SPY", now=when)
        assert c.status == preflight.NA and c.blocks is False


def test_extended_session_window_covers_premarket_and_afterhours():
    assert preflight.in_extended_session(dt.datetime(2026, 9, 11, 4, 0, tzinfo=ET)) is True
    assert preflight.in_extended_session(dt.datetime(2026, 9, 11, 20, 0, tzinfo=ET)) is True
    assert preflight.in_extended_session(dt.datetime(2026, 9, 11, 3, 59, tzinfo=ET)) is False
    assert preflight.in_extended_session(dt.datetime(2026, 9, 13, 10, 0, tzinfo=ET)) is False   # Sunday


def test_pipeline_incomplete_reports_coverage_and_never_claims_success(monkeypatch):
    recorded = []
    monkeypatch.setattr(failures, "record", lambda f, **kw: recorded.append(f))
    out = preflight.pipeline_incomplete("scan_morning", expected=600, produced=40,
                                        errors=["alpaca 503"], notify=False)
    assert out["complete"] is False and out["coverage_pct"] == 6.7
    assert "INCOMPLETE" in recorded[0].reason and "must not be read as a full scan" in recorded[0].reason


def test_a_blocked_preflight_raises_with_the_reasons(monkeypatch):
    stale = preflight.Preflight(checks=[_check("data_feed", preflight.RED)], computed_at="")
    monkeypatch.setattr(preflight, "run", lambda **kw: stale)
    with pytest.raises(preflight.PreflightBlocked) as e:
        preflight.assert_can_enter(record=False)
    assert e.value.result is stale and "data_feed" in str(e.value)


# ============================================================ the Guardian fire drill

def test_every_guardian_check_can_be_forced_to_block(monkeypatch):
    """§17 criterion 3. A check that has never blocked anything is a check nobody has evidence for."""
    monkeypatch.setattr(drill, "_store", lambda res, notify: None)
    res = drill.run(store=False, now=dt.datetime(2026, 9, 11, 10, 0, tzinfo=ET))
    failed = [f"{c.check}: got {c.got} ({c.error or c.detail})" for c in res.failed]
    assert not failed, failed
    assert res.complete is True
    assert set(res.covered) == set(drill.gate_check_names())


def test_the_drill_covers_every_check_the_gate_actually_runs():
    """A check added to the gate without a drill scenario would be a permanent green nobody noticed.

    This reads the names out of `gate.evaluate` itself rather than trusting the list to stay in sync.
    """
    import inspect

    src = inspect.getsource(guardian.evaluate)
    called = {line.strip().split("(")[0] for line in src.splitlines() if line.strip().startswith("check_")}
    assert called, "could not read the gate's check list"
    missing = called - set(drill.CHECK_FUNCTIONS)
    assert not missing, f"the gate runs {missing} but the drill has no scenario for it"


def test_the_drill_submits_nothing(monkeypatch):
    from app.broker import orders as broker_orders

    monkeypatch.setattr(broker_orders, "submit",
                        lambda **kw: pytest.fail("the drill must never reach the broker"))
    monkeypatch.setattr(broker_orders, "open_orders",
                        lambda symbol=None: pytest.fail("the drill must not query live orders"))
    drill.run(store=False, now=dt.datetime(2026, 9, 11, 10, 0, tzinfo=ET))


def test_the_kill_switch_case_is_reported_unexercised_rather_than_passed_when_skipped():
    c = drill._kill_switch_case(live=False)
    assert c.passed is True and "forced argument" in c.scenario
    assert "live_kill_switch=true" in c.scenario, "the report must say the real switch was not flipped"


# ============================================================ acceptance: measured, never assumed

def test_the_scorecard_never_grants_permission_on_its_own():
    met = [acceptance.Criterion(i, f"c{i}", True, "") for i in range(1, 5)]
    owner = acceptance.Criterion(5, "owner", False, "", manual=True)
    assert acceptance.Scorecard(met + [owner], "", {}).ready_for_live is False
    owner_done = acceptance.Criterion(5, "owner", True, "", manual=True)
    assert acceptance.Scorecard(met + [owner_done], "", {}).ready_for_live is True


def test_an_unmeasurable_criterion_is_unknown_and_blocks():
    unknown = acceptance.Criterion(2, "paper month", None, "database unreachable")
    assert unknown.status == "unknown"
    card = acceptance.Scorecard([unknown, acceptance.Criterion(5, "owner", True, "", manual=True)], "", {})
    assert card.ready_for_live is False


def test_weeks_are_seven_day_blocks_from_day_zero():
    """Calendar weeks would punish a Wednesday start with a two-day first week that can never be
    complete — and §17 asks for four consecutive weeks of operation, not four Mondays."""
    blocks = acceptance._weeks(dt.date(2026, 9, 2), dt.date(2026, 9, 30))
    assert len(blocks) == 4
    assert blocks[0] == (dt.date(2026, 9, 2), dt.date(2026, 9, 8))
    assert acceptance._weeks(dt.date(2026, 9, 2), dt.date(2026, 9, 7)) == []


def test_latency_with_no_samples_is_unknown_not_met(monkeypatch):
    class FakeTable:
        def select(self, *a, **k): return self
        def gte(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return type("R", (), {"data": []})()

    monkeypatch.setattr("app.db.get_client", lambda: type("C", (), {"table": lambda s, n: FakeTable()})())
    c = acceptance.criterion_4_latency()
    assert c.met is None and "stays unknown" in c.detail


def test_a_stop_that_never_fired_does_not_count_as_a_verified_stop(monkeypatch):
    """"Nothing went wrong" is not a measurement of how fast the safety net reacts."""
    orders = [{"created_at": "2026-09-10T14:00:00Z", "raw_response": {"deploy_latency_ms": 900},
               "source": "signal"}]

    class FakeTable:
        def __init__(self, name): self.name = name
        def select(self, *a, **k): return self
        def gte(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self):
            return type("R", (), {"data": orders if self.name == "orders" else []})()

    monkeypatch.setattr("app.db.get_client",
                        lambda: type("C", (), {"table": lambda s, n: FakeTable(n)})())
    c = acceptance.criterion_4_latency()
    assert c.met is None
    assert "never fired" in c.detail and "unverified" in c.detail


def test_the_ledger_reconciles_only_when_every_fee_is_backed_or_labelled(monkeypatch):
    """A paper account is never charged SEC/TAF (decision #46). A fee row that is neither matched to a
    real Alpaca activity nor labelled an estimate is an invented charge."""
    rows = [
        {"kind": "fee_sec", "amount": 0.01, "alpaca_activity_id": "act-1", "detail": {}, "event_time": "x"},
        {"kind": "fee_taf", "amount": 0.02, "alpaca_activity_id": None, "detail": {"estimated": True},
         "event_time": "x"},
    ]

    class FakeTable:
        def select(self, *a, **k): return self
        def gte(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def execute(self): return type("R", (), {"data": rows})()

    monkeypatch.setattr("app.db.get_client", lambda: type("C", (), {"table": lambda s, n: FakeTable()})())
    assert acceptance.reconcile_ledger()["reconciled"] is True

    rows.append({"kind": "fee_sec", "amount": 9.99, "alpaca_activity_id": None, "detail": {},
                 "event_time": "x"})
    out = acceptance.reconcile_ledger()
    assert out["reconciled"] is False and "invented charge" in out["detail"]


def test_s2_is_excused_from_the_backtest_criterion_by_name():
    assert "S2" in acceptance.NOT_BACKTESTABLE


def test_a_zero_trade_run_is_not_a_baseline(monkeypatch):
    """The false green this phase exists to prevent, caught on the scorecard's first real run: S1's only
    completed backtest covered 12 days and produced no trades. "It ran" is not "it was measured" — a
    config that never fires looks exactly like a strategy that never triggers (decisions #63, #71)."""
    strategies = [{"id": "a", "code": "S1", "name": "Low-Float Gapper"},
                  {"id": "b", "code": "S2", "name": "Watchlist briefing"}]
    runs = [{"strategy_id": "a", "status": "done", "verdict": "inconclusive",
             "metrics": {"trades": 0}, "date_from": "2026-08-24", "date_to": "2026-09-05",
             "calibration": {}, "ab_against_backtest_id": "x"}]

    class FakeTable:
        def __init__(self, name): self.name = name
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def execute(self):
            return type("R", (), {"data": strategies if self.name == "strategy_configs" else runs})()

    monkeypatch.setattr("app.db.get_client",
                        lambda: type("C", (), {"table": lambda s, n: FakeTable(n)})())

    c = acceptance.criterion_1_backtests()
    assert c.met is False
    assert "S1" in c.evidence["zero_trade_runs_only"]
    assert "ZERO trades" in c.detail and "not a baseline" in c.detail

    # a real baseline over a real span does count
    runs[0].update({"metrics": {"trades": 156}, "date_from": "2026-03-02"})
    assert acceptance.criterion_1_backtests().met is True


def test_a_two_week_run_with_trades_is_still_not_a_baseline(monkeypatch):
    strategies = [{"id": "a", "code": "S1", "name": "x"}]
    runs = [{"strategy_id": "a", "status": "done", "verdict": "fail", "metrics": {"trades": 40},
             "date_from": "2026-08-24", "date_to": "2026-09-05", "calibration": {},
             "ab_against_backtest_id": "x"}]

    class FakeTable:
        def __init__(self, name): self.name = name
        def select(self, *a, **k): return self
        def eq(self, *a, **k): return self
        def execute(self):
            return type("R", (), {"data": strategies if self.name == "strategy_configs" else runs})()

    monkeypatch.setattr("app.db.get_client",
                        lambda: type("C", (), {"table": lambda s, n: FakeTable(n)})())
    assert acceptance.criterion_1_backtests().met is False


# ============================================================ the scanner says so when it is partial

def test_an_incomplete_scan_says_so_before_it_says_anything_else(monkeypatch):
    from app.scanner import run as scan_run

    monkeypatch.setattr(preflight, "pipeline_incomplete", lambda *a, **kw: {})
    res = scan_run.ScanResult(kind="morning", scan_date="2026-09-11", as_of="08:00")
    res.strategies = [{"code": "S1", "passed": 3, "evaluated": 40, "top": []}]
    scan_run.mark_incomplete(res, "premarket_bars", "pre-market bars are missing", notify=False)

    lines = res.summary().splitlines()
    assert "PIPELINE INCOMPLETE" in lines[1]
    assert "not as today's market" in lines[1]
    assert res.as_dict()["complete"] is False


# ============================================================ the coverage counters actually count

def test_the_coverage_report_has_no_field_that_always_answers_zero():
    """Found by the go-live scorecard on its first run: `symbol_days_screened` and `symbol_days_passed`
    were declared on `Coverage` and never incremented, so every backtest reported 0 symbol-days beside
    a funnel saying 156 trades happened. A field that always answers zero is worse than an absent one.
    """
    import inspect

    from app.backtest import replay, run as bt_run

    declared = {f for f in replay.Coverage.__dataclass_fields__ if f.startswith("symbol_days")}
    assert declared, "the counters were renamed — update this test with them"
    src = inspect.getsource(bt_run)
    for field_name in declared:
        assert f"cov.{field_name} =" in src or f"cov.{field_name} +=" in src, (
            f"Coverage.{field_name} is reported but never written")
