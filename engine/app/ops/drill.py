"""The Guardian fire drill — plan §17 criterion 3: "at least one forced red-block test per check".

A check that has never blocked anything is a check nobody has evidence for. Ten checks stand between a
signal and an order; a typo in any one of them turns it into a permanent green, and a permanent green
looks exactly like a healthy market. So each check is handed inputs that leave it no choice but to go
red, and the drill records whether it did.

What this is NOT:
  * not a unit test — it runs against the live code path, on demand, and files its result in
    `compliance_events` so the acceptance scorecard can cite a date rather than a promise;
  * not an order — nothing is submitted, nothing is cancelled, no state is changed. The forced inputs
    are passed as arguments to the individual check functions;
  * not a substitute for the real block — `docs/DECISIONS.md` #44 records the day the gate stopped a
    genuine 3.3%-spread order on TNON. The drill proves capability; production proves it matters.

The kill switch is the one check whose red branch cannot be forced by argument alone without also
proving the switch itself responds, so it has an opt-in live mode (`live_kill_switch=True`) that
engages it, verifies the block, and releases it again in a `finally`. Off by default; reported as
`not exercised` rather than as a pass when it is skipped, because unknown is never green.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from app.broker.account import AccountSnapshot
from app.guardian import gate

log = logging.getLogger(__name__)

DRILL_SYMBOL = "DRILL"        # never a real ticker: nothing here may resolve to a tradable symbol


@dataclass(slots=True)
class DrillCase:
    check: str
    scenario: str
    expected: str
    got: str = ""
    detail: str = ""
    passed: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {"check": self.check, "scenario": self.scenario, "expected": self.expected, "got": self.got,
                "detail": self.detail[:300], "passed": self.passed, "error": self.error}


@dataclass(slots=True)
class DrillResult:
    cases: list[DrillCase] = field(default_factory=list)
    ran_at: str = ""
    live_kill_switch: bool = False

    @property
    def covered(self) -> list[str]:
        return sorted({c.check for c in self.cases if c.passed})

    @property
    def failed(self) -> list[DrillCase]:
        return [c for c in self.cases if not c.passed]

    @property
    def complete(self) -> bool:
        """Every Layer-1 check proven able to block."""
        return set(self.covered) >= set(gate_check_names())

    def as_dict(self) -> dict:
        return {"ran_at": self.ran_at, "complete": self.complete, "live_kill_switch": self.live_kill_switch,
                "checks_total": len(gate_check_names()), "checks_proven": len(self.covered),
                "covered": self.covered,
                "missing": sorted(set(gate_check_names()) - set(self.covered)),
                "cases": [c.as_dict() for c in self.cases]}


# gate function name -> the `Check.name` it returns. The two differ in one place (`check_account`
# returns "account_status"), and a test reads `gate.evaluate` to make sure this map has not fallen
# behind: a check added to the gate without a drill scenario would be a permanent green nobody noticed.
CHECK_FUNCTIONS: dict[str, str] = {
    "check_kill_switch": "kill_switch",
    "check_account": "account_status",
    "check_order_shape": "order_shape",
    "check_buying_power": "buying_power",
    "check_spread": "spread",
    "check_position_risk": "position_risk",
    "check_max_concurrent": "max_concurrent",
    "check_daily_loss_limit": "daily_loss_limit",
    "check_wash_trade": "wash_trade",
    "check_shortable": "shortable",
}


def gate_check_names() -> tuple[str, ...]:
    """The Layer-1 checks the drill must cover. Kept here so a new check fails the drill until it is
    given a scenario — a check added without a proof of blocking is the failure mode this guards."""
    return tuple(CHECK_FUNCTIONS.values())


def _case(check: str, scenario: str, fn, expected: str = gate.RED) -> DrillCase:
    c = DrillCase(check=check, scenario=scenario, expected=expected)
    try:
        res = fn()
        c.got, c.detail = res.verdict, res.detail
        c.passed = res.verdict == expected and res.name == check
        if res.name != check:
            c.error = f"scenario exercised '{res.name}', not '{check}'"
    except Exception as e:  # noqa: BLE001 — a check that raises is a check that cannot block
        c.got, c.error = "exception", f"{type(e).__name__}: {str(e)[:200]}"
    return c


def _blocked_account() -> AccountSnapshot:
    return AccountSnapshot(ok=True, equity=25_000.0, buying_power=50_000.0, cash=25_000.0,
                           trading_blocked=True, account_blocked=False, positions=[])


def _healthy_account(positions: int = 0) -> AccountSnapshot:
    return AccountSnapshot(ok=True, equity=25_000.0, buying_power=50_000.0, cash=25_000.0,
                           trading_blocked=False, account_blocked=False,
                           positions=[{"symbol": f"SYM{i}", "qty": 1.0, "side": "long",
                                       "avg_entry_price": 10.0, "unrealized_pl": 0.0, "market_value": 10.0}
                                      for i in range(positions)])


def run(*, live_kill_switch: bool = False, store: bool = True, notify: bool = False,
        now: dt.datetime | None = None) -> DrillResult:
    """Force every Layer-1 check to block, once. Submits nothing."""
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    res = DrillResult(ran_at=now.isoformat(), live_kill_switch=live_kill_switch)

    res.cases.append(_case(
        "account_status", "broker reports trading_blocked=true",
        lambda: gate.check_account(_blocked_account())))

    res.cases.append(_case(
        "order_shape", "a pre-market STOP order — Alpaca accepts these and never triggers them",
        lambda: gate.check_order_shape("premarket", "stop", True, "day", None)))

    res.cases.append(_case(
        "buying_power", "a $60,000 order against $50,000 of buying power",
        lambda: gate.check_buying_power(_healthy_account(), 60_000.0, 10.0)))

    res.cases.append(_case(
        "spread", "a 3.3% spread against a 2% ceiling (the live TNON block, decision #44)",
        lambda: gate.check_spread(DRILL_SYMBOL, 2.0, {"bid": 2.90, "ask": 3.00, "spread_pct": 3.33})))

    res.cases.append(_case(
        "position_risk", "1,000 shares risking $500 against a 0.5% ($125) budget on $25,000",
        lambda: gate.check_position_risk(10.00, 9.50, 1_000, 25_000.0, 0.5)))

    res.cases.append(_case(
        "max_concurrent", "a 4th new position against a cap of 3",
        lambda: gate.check_max_concurrent(_healthy_account(positions=3), DRILL_SYMBOL, 3)))

    res.cases.append(_case(
        "daily_loss_limit", "-2.5R realized today against a -2R limit",
        lambda: gate.check_daily_loss_limit(now.date(), 2.0, realized=(-2.5, "drill: forced realized R"))))

    res.cases.append(_case(
        "wash_trade", "a resting SELL while the new order is a BUY — a self-cross",
        lambda: gate.check_wash_trade(DRILL_SYMBOL, "buy", True,
                                      resting=[{"id": "drill-0000", "client_order_id": "drill-resting-sell",
                                                "side": "sell", "symbol": DRILL_SYMBOL}])))

    res.cases.append(_case(
        "shortable", "a short on a symbol the broker will not lend",
        lambda: gate.check_shortable(DRILL_SYMBOL, "short",
                                     {"shortable": False, "easy_to_borrow": False}, True)))

    res.cases.append(_kill_switch_case(live=live_kill_switch))

    if store:
        _store(res, notify=notify)
    return res


def _kill_switch_case(*, live: bool) -> DrillCase:
    """Forced-argument by default; opt-in live mode also proves the switch itself responds."""
    if not live:
        c = _case("kill_switch", "kill_switch engaged (forced argument; the switch itself was not flipped)",
                  lambda: gate.check_kill_switch(engaged=True))
        c.scenario += " — pass live_kill_switch=true to also verify the real switch responds"
        return c

    from app.db import get_client, kill_switch_engaged

    c = DrillCase(check="kill_switch", scenario="LIVE: engage the real kill switch, verify the block, release it",
                  expected=gate.RED)
    try:
        get_client().table("app_settings").update({"value": True}).eq("key", "kill_switch").execute()
        engaged_seen = kill_switch_engaged()
        verdict = gate.check_kill_switch()
        c.got, c.detail = verdict.verdict, f"{verdict.detail}; kill_switch_engaged()={engaged_seen}"
        c.passed = verdict.verdict == gate.RED and engaged_seen is True
    except Exception as e:  # noqa: BLE001
        c.got, c.error = "exception", f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        try:
            get_client().table("app_settings").update({"value": False}).eq("key", "kill_switch").execute()
            if kill_switch_engaged():
                c.error = ((c.error or "") + " | THE KILL SWITCH DID NOT RELEASE — trading stays halted "
                                             "until POST /execute/kill-switch?engage=false succeeds").strip()
                c.passed = False
                from app.ops import failures

                failures.failure("failure", "the drill could not release the kill switch — trading is halted",
                                 key="drill_kill_switch", severity="critical")
        except Exception as e:  # noqa: BLE001
            c.error = ((c.error or "") + f" | release failed: {type(e).__name__}: {str(e)[:160]}").strip()
            c.passed = False
    return c


def _store(res: DrillResult, *, notify: bool) -> None:
    from app.ops import failures

    summary = (f"Guardian drill: {len(res.covered)}/{len(gate_check_names())} checks proven able to block"
               + (f" — NOT PROVEN: {', '.join(sorted(set(gate_check_names()) - set(res.covered)))}"
                  if not res.complete else ""))
    failures.record(failures.Failure(
        kind="drill", reason=summary, key="guardian_drill",
        severity="info" if res.complete else "error", notify=notify, detail=res.as_dict()))


def latest(*, within_days: int = 90) -> dict | None:
    """The most recent stored drill, for the acceptance scorecard. None when there has never been one."""
    from app.db import get_client

    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=within_days)).isoformat()
    rows = (get_client().table("compliance_events").select("event_time,reason,detail")
            .eq("kind", "drill").gte("event_time", since)
            .order("event_time", desc=True).limit(1).execute().data or [])
    return rows[0] if rows else None
