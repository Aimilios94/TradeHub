"""Plan §33's failure policy, as code the pipeline actually runs.

§33 is a list of promises — "broker unreachable: block new entries"; "data stale: refuse to generate or
deploy signals"; "scanner failure: a pipeline-incomplete notice rather than partial signals". A promise
in a document is not a control. This module turns each one into a named check with a consequence, and
the execution and signal paths call it.

Design notes worth keeping:

* **Unknown is never green.** A check that could not be evaluated returns `unknown`, and `unknown` blocks
  anything that would place an order. The tempting alternative — "we could not reach the broker, assume
  it is fine" — is exactly the failure §33 was written against.
* **Staleness is only meaningful when the tape is moving.** Outside 04:00–20:00 ET a quote is *supposed*
  to be hours old, so the freshness check reports `not_applicable` with the reason rather than a red that
  fires every night. Inside the session it is real: the free plan's SIP history lags ~15 minutes by
  design, so freshness is always measured on IEX, which is real-time.
* **Blocking is asymmetric on purpose.** A red blocks new entries. It never blocks an EXIT, a
  reconciliation or the synthetic stop — refusing to close a position because a data feed is unhappy
  would be the engine hurting the owner to protect itself.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

GREEN, YELLOW, RED, UNKNOWN, NA = "green", "yellow", "red", "unknown", "not_applicable"
_RANK = {RED: 0, UNKNOWN: 1, YELLOW: 2, GREEN: 3, NA: 4}

DEFAULT_STALE_SECONDS = 60
DEFAULT_MONITOR_HEARTBEAT_SECONDS = 180
REFERENCE_SYMBOL = "SPY"          # always trades; a stale SPY quote means the feed, not the symbol
EXTENDED_START = dt.time(4, 0)
EXTENDED_END = dt.time(20, 0)


class PreflightBlocked(RuntimeError):
    """A new entry was refused by the failure policy. Carries the failing checks."""

    def __init__(self, reason: str, result: Preflight):
        self.result = result
        super().__init__(reason)


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    data: dict = field(default_factory=dict)

    @property
    def blocks(self) -> bool:
        return self.status in (RED, UNKNOWN)

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail, "data": self.data}


@dataclass(slots=True)
class Preflight:
    checks: list[Check]
    computed_at: str
    session_phase: str = ""

    @property
    def status(self) -> str:
        return min((c.status for c in self.checks), key=lambda s: _RANK.get(s, 1)) if self.checks else UNKNOWN

    @property
    def can_enter(self) -> bool:
        """May the engine open a NEW position right now?"""
        return not any(c.blocks for c in self.checks)

    @property
    def blocking(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.blocks]

    @property
    def warnings(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.status == YELLOW]

    def reason(self) -> str:
        return "; ".join(self.blocking) or "ok"

    def as_dict(self) -> dict:
        return {"status": self.status, "can_enter": self.can_enter, "session_phase": self.session_phase,
                "computed_at": self.computed_at, "blocking": self.blocking, "warnings": self.warnings,
                "checks": [c.as_dict() for c in self.checks]}


# --------------------------------------------------------------------------- policy config

def policy() -> dict:
    """`app_settings.failure_policy`, with the defaults that make the checks work offline."""
    base = {
        "data_stale_seconds": DEFAULT_STALE_SECONDS,
        "monitor_heartbeat_seconds": DEFAULT_MONITOR_HEARTBEAT_SECONDS,
        "alert_cooldown_minutes": 30,
        "block_entries_when_broker_unreachable": True,
        "block_entries_when_data_stale": True,
    }
    try:
        from app.db import get_client

        row = get_client().table("app_settings").select("value").eq("key", "failure_policy").single().execute()
        base.update({k: v for k, v in (row.data.get("value") or {}).items() if k in base})
    except Exception as e:  # noqa: BLE001 — the defaults are the strict ones; a DB outage must not relax them
        log.debug("failure_policy not read (%s); using defaults", e)
    return base


def in_extended_session(now: dt.datetime) -> bool:
    return now.weekday() < 5 and EXTENDED_START <= now.time() <= EXTENDED_END


# --------------------------------------------------------------------------- individual checks

def check_kill_switch() -> Check:
    from app.db import kill_switch_engaged

    engaged = kill_switch_engaged()          # fails SAFE: True when the DB is unreachable
    return Check("kill_switch", RED if engaged else GREEN,
                 "kill switch ENGAGED — no orders may be placed" if engaged else "released")


def check_database() -> Check:
    from app.db import get_client

    try:
        get_client().table("app_settings").select("key").limit(1).execute()
        return Check("database", GREEN, "Supabase reachable")
    except Exception as e:  # noqa: BLE001
        return Check("database", RED, f"Supabase unreachable: {type(e).__name__}: {str(e)[:160]}")


def check_broker(acct=None) -> Check:
    """§33: broker unreachable → block new entries, alert, fall back to the synthetic monitor."""
    from app.broker import account as broker_account

    acct = acct if acct is not None else broker_account.snapshot()
    if not acct.ok:
        return Check("broker", RED, f"Alpaca unreachable: {acct.error}",
                     {"fallback": "open positions are covered by the synthetic stop monitor only"})
    if acct.account_blocked or acct.trading_blocked:
        return Check("broker", RED, "the broker has blocked this account for trading",
                     {"account_blocked": acct.account_blocked, "trading_blocked": acct.trading_blocked})
    return Check("broker", GREEN, f"account reachable · equity={acct.equity} buying_power={acct.buying_power}",
                 {"open_positions": acct.open_positions})


def quote_age_seconds(symbol: str, *, now: dt.datetime | None = None) -> tuple[float | None, str]:
    """Age in seconds of the latest IEX quote for `symbol`, and a note. `None` = could not be measured.

    IEX and not SIP: the free plan refuses SIP data from the last ~15 minutes, so a SIP quote is stale
    BY SUBSCRIPTION and measuring freshness against it would flag a healthy feed every single day.
    """
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    try:
        # `get_latest_quotes` is already paced and retried; wrapping it again would just charge the
        # rate limiter twice for one call.
        q = (alpaca.get_latest_quotes([symbol], feed="iex") or {}).get(symbol)
    except Exception as e:  # noqa: BLE001 — an unreadable quote is `unknown`, which blocks; it is not an error to raise
        return None, f"{type(e).__name__}: {str(e)[:140]}"
    if not q or not q.get("ts"):
        return None, "no quote returned"
    ts = dt.datetime.fromisoformat(q["ts"])
    return max(0.0, (now - ts.astimezone(now.tzinfo)).total_seconds()), f"bid={q.get('bid')} ask={q.get('ask')}"


def check_data_feed(symbol: str = REFERENCE_SYMBOL, *, now: dt.datetime | None = None,
                    stale_seconds: int | None = None) -> Check:
    """§33: quotes past the staleness threshold → refuse to generate or deploy signals."""
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    if not in_extended_session(now):
        return Check("data_feed", NA, f"outside 04:00–20:00 ET ({now:%H:%M}) — an old quote is expected here",
                     {"symbol": symbol})
    limit = int(stale_seconds if stale_seconds is not None else policy()["data_stale_seconds"])
    age, note = quote_age_seconds(symbol, now=now)
    if age is None:
        return Check("data_feed", UNKNOWN, f"could not read a {symbol} quote — {note}", {"symbol": symbol})
    if age > limit:
        return Check("data_feed", RED, f"{symbol} quote is {age:.0f}s old (limit {limit}s) — prices are stale",
                     {"symbol": symbol, "age_seconds": round(age, 1), "limit_seconds": limit})
    if age > limit * 0.5:
        return Check("data_feed", YELLOW, f"{symbol} quote {age:.0f}s old, approaching the {limit}s limit",
                     {"symbol": symbol, "age_seconds": round(age, 1), "limit_seconds": limit})
    return Check("data_feed", GREEN, f"{symbol} quote {age:.0f}s old · {note}",
                 {"symbol": symbol, "age_seconds": round(age, 1)})


def check_monitor(*, now: dt.datetime | None = None) -> Check:
    """Is the synthetic stop watcher alive? It is the ONLY protection a pre-market position has.

    A warning, not a block: the monitor matters for positions that already exist, and refusing to open a
    new one because the watcher is quiet would be the wrong trade-off outside pre-market. Pre-market it
    escalates to red, because that is the window where a broker stop silently does nothing.
    """
    from app.broker import orders as broker_orders
    from app.db import get_client
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    premarket = broker_orders.session_phase(now) == "premarket"
    try:
        row = get_client().table("app_settings").select("value").eq("key", "monitor_heartbeat").single().execute()
        beat = (row.data.get("value") or {}).get("at")
    except Exception as e:  # noqa: BLE001
        return Check("stop_monitor", UNKNOWN if premarket else YELLOW,
                     f"heartbeat unreadable: {type(e).__name__}: {str(e)[:120]}")
    if not beat:
        return Check("stop_monitor", UNKNOWN if premarket else YELLOW,
                     "no heartbeat recorded yet — start the monitor container")
    age = (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(beat)).total_seconds()
    limit = int(policy()["monitor_heartbeat_seconds"])
    if age > limit:
        detail = f"last heartbeat {age / 60:.1f} min ago (limit {limit}s) — pre-market stops are NOT being watched"
        return Check("stop_monitor", RED if premarket else YELLOW, detail, {"age_seconds": round(age)})
    return Check("stop_monitor", GREEN, f"alive, last heartbeat {age:.0f}s ago", {"age_seconds": round(age)})


def check_paper_lockout() -> Check:
    """The Phase 5 lockout is still the thing standing between this engine and real money."""
    from app.broker import orders as broker_orders

    try:
        broker_orders.assert_paper_only()
        return Check("paper_lockout", GREEN, "live trading is refused in code (docs/DECISIONS.md #37)")
    except broker_orders.OrderRefused as e:
        return Check("paper_lockout", RED, str(e)[:300])


# --------------------------------------------------------------------------- the composed check

def run(*, symbol: str = REFERENCE_SYMBOL, now: dt.datetime | None = None, include_monitor: bool = True) -> Preflight:
    """Every §33 check at once. Used by `/ops/preflight`, the morning pipeline and the deploy path."""
    from app.broker import orders as broker_orders
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    checks = [check_kill_switch(), check_database(), check_paper_lockout(), check_broker(),
              check_data_feed(symbol, now=now)]
    if include_monitor:
        checks.append(check_monitor(now=now))
    return Preflight(checks=checks, computed_at=now.isoformat(), session_phase=broker_orders.session_phase(now))


def assert_can_enter(*, symbol: str = REFERENCE_SYMBOL, now: dt.datetime | None = None,
                     record: bool = True) -> Preflight:
    """Gate a NEW entry on the failure policy. Raises `PreflightBlocked` with the reasons.

    Called before the Guardian gate, not instead of it: this asks "is the system in a fit state to trade
    at all", the Guardian asks "is this particular order sane".
    """
    res = run(symbol=symbol, now=now)
    if res.can_enter:
        return res
    reason = res.reason()
    if record:
        from app.ops import failures

        failures.failure("pipeline_blocked", f"entry refused by the failure policy — {reason}",
                         key=f"preflight:{symbol}", symbol=symbol, severity="error",
                         detail=res.as_dict())
    raise PreflightBlocked(reason, res)


def assert_data_fresh(symbol: str = REFERENCE_SYMBOL, *, now: dt.datetime | None = None) -> Check:
    """§33's narrow case: never plan or deploy on a stale price. Raises `PreflightBlocked`."""
    c = check_data_feed(symbol, now=now)
    if c.blocks:
        from app.ops import failures

        failures.failure("data_stale", f"{c.detail}", key=f"data_stale:{symbol}", symbol=symbol,
                         severity="error", detail=c.as_dict())
        raise PreflightBlocked(c.detail, Preflight(checks=[c], computed_at=(now or dt.datetime.now(dt.UTC)).isoformat()))
    return c


def pipeline_incomplete(stage: str, *, expected: int, produced: int, errors: list[str],
                        notify: bool = True) -> dict:
    """§33: a partial run is announced as incomplete — never presented as a complete result.

    The scanner and the enrichment step call this when part of the universe failed. The point is that
    "40 of 600 symbols scanned" must not reach the owner looking like "the market had 40 candidates".
    """
    from app.ops import failures

    reason = (f"{stage} completed on {produced} of {expected} items — the result is INCOMPLETE and must "
              f"not be read as a full scan. First errors: {'; '.join(errors[:3]) or 'none recorded'}")
    failures.record(failures.Failure(kind="pipeline_incomplete", reason=reason, key=stage, severity="warn",
                                     notify=notify, detail={"stage": stage, "expected": expected,
                                                            "produced": produced, "errors": errors[:20]}))
    return {"stage": stage, "complete": False, "expected": expected, "produced": produced,
            "coverage_pct": round(produced / expected * 100, 1) if expected else 0.0, "errors": errors[:20]}
