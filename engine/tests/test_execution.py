"""Phase 5: order-shape rules, the Guardian Layer-1 gate, the deploy path, and the synthetic stop.

Offline throughout — no broker, no network, no database. These tests are the ones that matter most in
the project, because this is the phase where a mistake costs money rather than a wrong answer. The
properties under test are all refusals: what must NOT happen, and that every refusal says why.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.broker import orders as broker_orders
from app.broker.account import AccountSnapshot
from app.config import get_settings
from app.execution import deploy as deploy_mod
from app.execution import telegram_deploy as tg
from app.guardian import gate as guardian
from app.main import app
from app.marketdata.alpaca import ET
from app.monitor import synthetic_stop as monitor
from app.ops import preflight as ops_preflight

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_settings().engine_api_token}"}

ACCT = AccountSnapshot(ok=True, equity=100_000.0, buying_power=400_000.0, cash=100_000.0, positions=[])
DOWN = AccountSnapshot(ok=False, error="ConnectError")
RISK = {"risk_per_trade_pct": 0.5, "max_concurrent": 2, "spread_ceiling_pct": 2.0, "order_types": ["limit"]}
DEFAULTS = {"spread_ceiling_pct": 2.0, "risk_per_trade_pct_default": 0.5, "daily_loss_limit_r": 2.0,
            "max_concurrent_positions": 3, "buying_power_buffer_pct": 10.0,
            "require_etb_for_shorts": True, "wash_trade_guard": True}


def at(hh: int, mm: int, day: dt.date = dt.date(2026, 9, 10)) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), ET)


# ---------- session and order shape ----------

@pytest.mark.parametrize("hh,mm,expected", [
    (3, 59, "closed"), (4, 0, "premarket"), (9, 29, "premarket"),
    (9, 30, "regular"), (15, 59, "regular"), (16, 0, "afterhours"),
    (19, 59, "afterhours"), (20, 0, "closed"),
])
def test_session_phase_boundaries(hh, mm, expected):
    assert broker_orders.session_phase(at(hh, mm)) == expected


def test_weekend_is_closed():
    assert broker_orders.session_phase(at(11, 0, dt.date(2026, 9, 12))) == "closed"   # Saturday


def test_premarket_accepts_only_a_day_limit_with_extended_hours():
    ok = broker_orders.validate_shape(phase="premarket", order_type="limit", time_in_force="day",
                                      extended_hours=True, order_class="simple")
    assert ok == []


@pytest.mark.parametrize("kwargs,expect", [
    ({"order_type": "market"}, "only limit orders"),
    ({"order_type": "stop"}, "do NOT trigger outside regular hours"),
    ({"order_type": "stop_limit"}, "only limit orders"),
    ({"time_in_force": "gtc"}, "must be 'day'"),
    ({"extended_hours": False}, "extended_hours must be true"),
    ({"order_class": "bracket"}, "not accepted outside regular hours"),
    ({"order_class": "oco"}, "not accepted outside regular hours"),
])
def test_premarket_rejects_every_other_shape(kwargs, expect):
    base = {"phase": "premarket", "order_type": "limit", "time_in_force": "day",
            "extended_hours": True, "order_class": "simple"}
    problems = broker_orders.validate_shape(**(base | kwargs))
    assert problems and any(expect in p for p in problems), problems


def test_regular_hours_allows_oco_and_market():
    assert broker_orders.validate_shape(phase="regular", order_type="market", time_in_force="day",
                                        extended_hours=False, order_class="oco") == []


def test_closed_session_refuses_everything():
    problems = broker_orders.validate_shape(phase="closed", order_type="limit", time_in_force="day",
                                            extended_hours=False, order_class="simple")
    assert problems and "market is closed" in problems[0]


# ---------- the two hard locks ----------

def test_live_account_is_refused_in_code(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "paper_mode", False, raising=False)
    with pytest.raises(broker_orders.OrderRefused) as e:
        broker_orders.assert_paper_only()
    assert "live trading is disabled in code" in str(e.value)


def test_a_non_paper_base_url_is_refused(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "paper_mode", True, raising=False)
    monkeypatch.setattr(s, "alpaca_paper_base_url", "https://api.alpaca.markets", raising=False)
    with pytest.raises(broker_orders.OrderRefused) as e:
        broker_orders.assert_paper_only()
    assert "does not look like a paper endpoint" in str(e.value)


def test_kill_switch_stops_a_submission_before_the_broker(monkeypatch):
    monkeypatch.setattr(broker_orders, "kill_switch_engaged", lambda: True)
    monkeypatch.setattr(broker_orders, "_trading_client",
                        lambda: pytest.fail("the broker must not be contacted with the kill switch on"))
    with pytest.raises(broker_orders.OrderRefused) as e:
        broker_orders.submit(symbol="AAPL", side="buy", qty=1, limit_price=100.0, now=at(10, 0))
    assert "kill switch is ENGAGED" in str(e.value)


def test_submit_refuses_a_bad_request_before_the_broker(monkeypatch):
    monkeypatch.setattr(broker_orders, "kill_switch_engaged", lambda: False)
    monkeypatch.setattr(broker_orders, "_trading_client",
                        lambda: pytest.fail("must not reach the broker with an invalid request"))
    base = {"symbol": "AAPL", "side": "buy", "qty": 1, "limit_price": 100.0, "now": at(10, 0)}
    for override, expect in (({"qty": 0}, "qty must be positive"),
                             ({"limit_price": None}, "needs a limit_price"),
                             ({"side": "hold"}, "side must be buy or sell")):
        with pytest.raises(broker_orders.OrderRefused) as e:
            broker_orders.submit(**(base | override))
        assert expect in str(e.value)


# ---------- Guardian Layer 1 ----------

def test_strategy_risk_may_tighten_a_default_but_never_loosen_it():
    loose = {"spread_ceiling_pct": 9.0, "daily_loss_limit_r": 10.0, "max_concurrent": 99,
             "risk_per_trade_pct": 5.0}
    eff = guardian.effective_limits(loose, DEFAULTS)
    assert eff["spread_ceiling_pct"] == 2.0        # global ceiling wins
    assert eff["daily_loss_limit_r"] == 2.0
    assert eff["max_concurrent"] == 3
    assert eff["risk_per_trade_pct"] == 0.5

    tight = {"spread_ceiling_pct": 0.5, "max_concurrent": 1}
    eff2 = guardian.effective_limits(tight, DEFAULTS)
    assert eff2["spread_ceiling_pct"] == 0.5      # strategy is stricter, so it wins
    assert eff2["max_concurrent"] == 1


def test_spread_over_the_ceiling_is_red():
    assert guardian.check_spread("TNON", 2.0, {"spread_pct": 3.29}).verdict == guardian.RED
    assert guardian.check_spread("AAPL", 2.0, {"spread_pct": 0.02}).verdict == guardian.GREEN


def test_a_missing_quote_is_yellow_not_green():
    c = guardian.check_spread("TNON", 2.0, None)
    assert c.verdict == guardian.YELLOW and "no quote" in c.detail


def test_buying_power_holds_a_buffer_back():
    assert guardian.check_buying_power(ACCT, 359_000, 10.0).verdict == guardian.GREEN
    red = guardian.check_buying_power(ACCT, 361_000, 10.0)
    assert red.verdict == guardian.RED and "usable buying power" in red.detail


def test_buying_power_unknown_when_the_broker_is_down():
    assert guardian.check_buying_power(DOWN, 100.0, 10.0).verdict == guardian.YELLOW


def test_position_risk_blocks_an_oversized_position_and_a_zero_width_stop():
    ok = guardian.check_position_risk(4.0, 3.5, 900, 100_000.0, 0.5)
    assert ok.verdict == guardian.GREEN                      # 900 x 0.50 = 450 <= 500
    big = guardian.check_position_risk(4.0, 3.5, 3000, 100_000.0, 0.5)
    assert big.verdict == guardian.RED and "max 1000 shares" in big.detail
    flat = guardian.check_position_risk(4.0, 4.0, 100, 100_000.0, 0.5)
    assert flat.verdict == guardian.RED and "no defined risk" in flat.detail


def test_max_concurrent_blocks_a_new_symbol_but_allows_adding_to_one_held():
    full = AccountSnapshot(ok=True, equity=1e5, buying_power=4e5,
                           positions=[{"symbol": "AAA", "qty": 1}, {"symbol": "BBB", "qty": 1}])
    assert guardian.check_max_concurrent(full, "CCC", 2).verdict == guardian.RED
    assert guardian.check_max_concurrent(full, "AAA", 2).verdict == guardian.GREEN


def test_daily_loss_limit_blocks_once_reached(monkeypatch):
    monkeypatch.setattr(guardian, "realized_r_today", lambda day: (-2.4, "3 closed trades"))
    c = guardian.check_daily_loss_limit(dt.date(2026, 9, 10), 2.0)
    assert c.verdict == guardian.RED and "daily limit" in c.detail
    monkeypatch.setattr(guardian, "realized_r_today", lambda day: (-1.0, "1 closed trade"))
    assert guardian.check_daily_loss_limit(dt.date(2026, 9, 10), 2.0).verdict == guardian.GREEN


def test_daily_loss_limit_is_yellow_when_the_journal_cannot_be_read(monkeypatch):
    monkeypatch.setattr(guardian, "realized_r_today", lambda day: (None, "journal unreachable"))
    assert guardian.check_daily_loss_limit(dt.date(2026, 9, 10), 2.0).verdict == guardian.YELLOW


def test_wash_trade_blocks_a_self_cross(monkeypatch):
    monkeypatch.setattr(broker_orders, "open_orders",
                        lambda symbol=None: [{"side": "sell", "id": "x" * 8, "client_order_id": "th-1"}])
    c = guardian.check_wash_trade("AAPL", "buy", True)
    assert c.verdict == guardian.RED and "self-cross" in c.detail


def test_wash_trade_warns_on_a_duplicate_same_side_order(monkeypatch):
    monkeypatch.setattr(broker_orders, "open_orders",
                        lambda symbol=None: [{"side": "buy", "id": "y" * 8, "client_order_id": "th-2"}])
    assert guardian.check_wash_trade("AAPL", "buy", True).verdict == guardian.YELLOW


def test_shorts_require_shortable_and_easy_to_borrow():
    assert guardian.check_shortable("X", "long", None, True).verdict == guardian.GREEN
    assert guardian.check_shortable("X", "short", {"shortable": False}, True).verdict == guardian.RED
    assert guardian.check_shortable("X", "short", {"shortable": True, "easy_to_borrow": False},
                                    True).verdict == guardian.RED
    assert guardian.check_shortable("X", "short", {"shortable": True, "easy_to_borrow": True},
                                    True).verdict == guardian.GREEN
    assert guardian.check_shortable("X", "short", None, True).verdict == guardian.YELLOW


def test_gate_verdict_is_the_worst_check():
    checks = [guardian.Check("a", guardian.GREEN, ""), guardian.Check("b", guardian.YELLOW, ""),
              guardian.Check("c", guardian.RED, "")]
    worst = min((c.verdict for c in checks), key=lambda v: guardian._ORDER[v])
    assert worst == guardian.RED
    res = guardian.GateResult(verdict=guardian.YELLOW, checks=checks[:2])
    assert res.passed is True and res.as_dict()["advisory"] is False


def test_a_red_result_does_not_pass():
    assert guardian.GateResult(verdict=guardian.RED, blocking=["spread: too wide"]).passed is False


# ---------- deploy path ----------

def _signal(**over) -> dict:
    return {"id": "sig-1", "strategy_id": "s1", "symbol": "AAPL", "direction": "long",
            "entry_price": 100.0, "stop": 98.0, "target": 106.0, "size": 100,
            "status": "proposed", "evidence": {}} | over


def _preflight_ok(monkeypatch):
    """Phase 8 put the §33 failure policy in front of the Guardian gate. These tests are about the gate,
    so the policy is satisfied explicitly — never implicitly, or a green here would only mean the
    preflight happened to pass."""
    ok = ops_preflight.Preflight(checks=[ops_preflight.Check("test", ops_preflight.GREEN, "stubbed")],
                                 computed_at="2026-09-11T09:00:00-04:00")
    monkeypatch.setattr(deploy_mod.ops_preflight, "assert_can_enter", lambda **kw: ok)
    return ok


def test_deploy_refuses_a_signal_that_is_not_proposed(monkeypatch):
    monkeypatch.setattr(deploy_mod, "load_signal", lambda sid: _signal(status="approved"))
    with pytest.raises(deploy_mod.DeployRefused) as e:
        deploy_mod.deploy("sig-1", notify=False)
    assert "already acted on" in str(e.value)


def test_deploy_blocks_on_a_red_gate_and_submits_nothing(monkeypatch):
    _preflight_ok(monkeypatch)
    monkeypatch.setattr(deploy_mod, "load_signal", lambda sid: _signal())
    monkeypatch.setattr(deploy_mod, "strategy_risk", lambda sid: (RISK, "S1"))
    monkeypatch.setattr(deploy_mod.broker_account, "snapshot", lambda: ACCT)
    monkeypatch.setattr(deploy_mod.guardian, "evaluate",
                        lambda **kw: guardian.GateResult(verdict=guardian.RED,
                                                         blocking=["spread: 9% above the 2% ceiling"]))
    monkeypatch.setattr(deploy_mod.broker_orders, "submit",
                        lambda **kw: pytest.fail("a red gate must never reach the broker"))
    events, marks, sent = [], [], []
    monkeypatch.setattr(deploy_mod.guardian, "log_event", lambda **kw: events.append(kw))
    monkeypatch.setattr(deploy_mod, "mark_signal", lambda sid, st, r=None: marks.append((st, r)))
    monkeypatch.setattr(deploy_mod, "send_telegram", lambda t: sent.append(t) or True)

    res = deploy_mod.deploy("sig-1", notify=True)
    assert res.submitted is False and res.gate_verdict == guardian.RED
    assert events and events[0]["kind"] == "gate_block"
    assert marks and marks[0][0] == "rejected"
    assert sent and "BLOCKED" in sent[0] and "No order was placed" in sent[0]


def test_deploy_dry_run_passes_the_gate_but_submits_nothing(monkeypatch):
    _preflight_ok(monkeypatch)
    monkeypatch.setattr(deploy_mod, "load_signal", lambda sid: _signal())
    monkeypatch.setattr(deploy_mod, "strategy_risk", lambda sid: (RISK, "S1"))
    monkeypatch.setattr(deploy_mod.broker_account, "snapshot", lambda: ACCT)
    monkeypatch.setattr(deploy_mod.guardian, "evaluate",
                        lambda **kw: guardian.GateResult(verdict=guardian.GREEN))
    monkeypatch.setattr(deploy_mod.broker_orders, "submit",
                        lambda **kw: pytest.fail("dry_run must not submit"))
    res = deploy_mod.deploy("sig-1", dry_run=True, notify=False)
    assert res.submitted is False and "dry_run" in res.reason


def test_deploy_records_a_broker_refusal_rather_than_retrying(monkeypatch):
    _preflight_ok(monkeypatch)
    monkeypatch.setattr(deploy_mod, "load_signal", lambda sid: _signal())
    monkeypatch.setattr(deploy_mod, "strategy_risk", lambda sid: (RISK, "S1"))
    monkeypatch.setattr(deploy_mod.broker_account, "snapshot", lambda: ACCT)
    monkeypatch.setattr(deploy_mod.guardian, "evaluate",
                        lambda **kw: guardian.GateResult(verdict=guardian.GREEN))
    monkeypatch.setattr(deploy_mod.alpaca, "get_latest_quotes", lambda *a, **kw: {})
    monkeypatch.setattr(deploy_mod.broker_orders, "submit",
                        lambda **kw: (_ for _ in ()).throw(broker_orders.OrderRefused("market is closed")))
    events, marks = [], []
    monkeypatch.setattr(deploy_mod.guardian, "log_event", lambda **kw: events.append(kw))
    monkeypatch.setattr(deploy_mod, "mark_signal", lambda sid, st, r=None: marks.append((st, r)))
    monkeypatch.setattr(deploy_mod, "send_telegram", lambda t: True)
    res = deploy_mod.deploy("sig-1", notify=True)
    assert res.submitted is False and "market is closed" in res.reason
    assert marks[0][0] == "rejected" and events[0]["kind"] == "failure"


def test_deploy_is_refused_by_the_failure_policy_before_the_guardian_runs(monkeypatch):
    """Plan §33: a stale feed or an unreachable broker stops the order, and the Guardian never runs.

    Ordering is the point. The Guardian asks whether THIS order is sane; it has no way to notice that
    the price it is reasoning about is four minutes old.
    """
    monkeypatch.setattr(deploy_mod, "load_signal", lambda sid: _signal())
    stale = ops_preflight.Preflight(
        checks=[ops_preflight.Check("data_feed", ops_preflight.RED, "AAPL quote is 412s old (limit 60s)")],
        computed_at="2026-09-11T09:00:00-04:00")

    def blocked(**kw):
        raise ops_preflight.PreflightBlocked(stale.reason(), stale)

    monkeypatch.setattr(deploy_mod.ops_preflight, "assert_can_enter", blocked)
    monkeypatch.setattr(deploy_mod.guardian, "evaluate",
                        lambda **kw: pytest.fail("the Guardian must not run once the policy has blocked"))
    monkeypatch.setattr(deploy_mod.broker_orders, "submit",
                        lambda **kw: pytest.fail("a blocked preflight must never reach the broker"))
    marks, sent = [], []
    monkeypatch.setattr(deploy_mod, "mark_signal", lambda sid, st, r=None: marks.append((st, r)))
    monkeypatch.setattr(deploy_mod, "send_telegram", lambda t: sent.append(t) or True)

    res = deploy_mod.deploy("sig-1", notify=True)
    assert res.submitted is False and "412s old" in res.reason
    assert res.preflight["can_enter"] is False
    assert marks and marks[0][0] == "rejected"
    assert sent and "BLOCKED" in sent[0]

# ---------- Telegram approval ----------

def test_only_the_configured_chat_may_act(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "5995574699", raising=False)
    assert tg.is_owner("5995574699") is True
    assert tg.is_owner(5995574699) is True
    assert tg.is_owner("123") is False
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "", raising=False)
    assert tg.is_owner("5995574699") is False       # unconfigured means nobody, not everybody


def test_callback_payload_parsing():
    assert tg.parse_callback("deploy:abc-123") == ("deploy", "abc-123")
    assert tg.parse_callback("cancel:abc-123") == ("cancel", "abc-123")
    for bad in ("", "deploy:", "delete:abc", "abc-123", None):
        with pytest.raises(ValueError):
            tg.parse_callback(bad)


def test_signal_card_shows_the_plan_and_says_the_gate_runs_again():
    text = tg.signal_card(_signal(evidence={"in_play_sentence": "AAPL is in play because X"},
                                  guardian_precheck={"verdict": "green", "advisory": True},
                                  reasoning="because of the thing"), "S1")
    assert "AAPL" in text and "in play because X" in text
    assert "entry 100.0" in text and "98.0" in text and "target 106.0" in text
    assert "runs again when you tap Deploy" in text


def test_callback_endpoint_rejects_a_foreign_chat(monkeypatch):
    monkeypatch.setattr(get_settings(), "telegram_chat_id", "5995574699", raising=False)
    payload = {"callback_query": {"id": "1", "data": "deploy:x",
                                  "message": {"message_id": 1, "chat": {"id": 42}}}}
    r = client.post("/execute/telegram/callback", headers=AUTH, json=payload)
    assert r.status_code == 403


# ---------- synthetic stop ----------

def test_a_long_is_stopped_by_a_print_at_or_below_the_stop():
    w = monitor.Watch("TNON", 100, True, 3.17, "sig-1")
    assert w.breached(3.16) and w.breached(3.17)
    assert not w.breached(3.18)


def test_a_short_is_stopped_by_a_print_at_or_above_the_stop():
    w = monitor.Watch("TNON", 100, False, 4.00, "sig-1")
    assert w.breached(4.00) and w.breached(4.01)
    assert not w.breached(3.99)


def test_the_exit_is_a_bounded_limit_priced_through_the_stop():
    long_w = monitor.Watch("TNON", 100, True, 10.0, None)
    short_w = monitor.Watch("TNON", 100, False, 10.0, None)
    assert long_w.exit_limit() < 10.0        # sells below the stop so it fills
    assert short_w.exit_limit() > 10.0       # buys above it
    assert long_w.exit_limit() >= 9.9        # but bounded — never a market order


def test_the_stop_fires_once_per_symbol(monkeypatch):
    m = monitor.SyntheticStopMonitor()
    m._watch = {"TNON": monitor.Watch("TNON", 100, True, 3.17, "sig-1")}
    fired = []
    monkeypatch.setattr(monitor.broker_orders, "close_position_limit",
                        lambda *a, **kw: fired.append(a) or {
                            "order": {"id": "o1", "client_order_id": "c1", "status": "new",
                                      "time_in_force": "day", "extended_hours": True,
                                      "submitted_at": None},
                            "request": {}})
    monkeypatch.setattr(monitor, "send_telegram", lambda t: True)
    monkeypatch.setattr(monitor.guardian, "log_event", lambda **kw: None)
    logged: list = []
    monkeypatch.setattr(monitor, "get_client", lambda: _sink_client(logged))

    m.on_price("TNON", 3.10, "trade")
    m.on_price("TNON", 3.05, "trade")        # a second breach must not double the exit
    assert len(fired) == 1
    assert len(logged) == 1                  # and exactly one orders row is written


def test_a_failed_exit_shouts_instead_of_going_quiet(monkeypatch):
    m = monitor.SyntheticStopMonitor()
    w = monitor.Watch("TNON", 100, True, 3.17, "sig-1")
    m._watch = {"TNON": w}
    monkeypatch.setattr(monitor.broker_orders, "close_position_limit",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("broker down")))
    alerts, events = [], []
    monkeypatch.setattr(monitor, "send_telegram", lambda t: alerts.append(t) or True)
    monkeypatch.setattr(monitor.guardian, "log_event", lambda **kw: events.append(kw))
    m.on_price("TNON", 3.10, "trade")
    assert alerts and "STILL OPEN and unprotected" in alerts[0]
    assert events and events[0]["verdict"] == "red"


def test_a_position_with_no_stop_is_not_watched(monkeypatch):
    monkeypatch.setattr(monitor.broker_account, "snapshot",
                        lambda: AccountSnapshot(ok=True, positions=[{"symbol": "AAA", "qty": 10}]))

    class C:
        def table(self, name):
            return self

        def select(self, *a, **kw):
            return self

        def eq(self, *a, **kw):
            return self

        def in_(self, *a, **kw):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    monkeypatch.setattr(monitor, "get_client", lambda: C())
    assert monitor.build_watchlist(dt.date(2026, 9, 10)) == {}


def test_watchlist_is_empty_and_loud_when_the_broker_is_down(monkeypatch):
    monkeypatch.setattr(monitor.broker_account, "snapshot", lambda: DOWN)
    events = []
    monkeypatch.setattr(monitor.guardian, "log_event", lambda **kw: events.append(kw))
    assert monitor.build_watchlist(dt.date(2026, 9, 10)) == {}
    assert events and events[0]["verdict"] == "red"


def _sink_client(sink: list):
    """Minimal Supabase stand-in that records inserted rows."""
    class _T:
        def insert(self, rows):
            sink.extend(rows)
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    return type("C", (), {"table": lambda self, name: _T()})()


# ---------- endpoints ----------

def test_execute_endpoints_require_auth():
    for method, path in (("post", "/execute/deploy?signal_id=x"), ("get", "/execute/status"),
                         ("post", "/execute/oco/attach"), ("post", "/execute/kill-switch?engage=true"),
                         ("get", "/execute/orders")):
        r = getattr(client, method)(path)
        assert r.status_code == 401, f"{method} {path} was not protected"
