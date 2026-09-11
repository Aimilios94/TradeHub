"""Guardian Layer 1 — the pre-trade gate (plan §11). Code, blocking, runs BEFORE any order.

This replaces Phase 4's advisory pre-check (docs/DECISIONS.md #31) with the enforcing version. Every
check answers green / yellow / red, and the gate's verdict is the worst of them:

  green   proceed
  yellow  proceed, but the owner sees the warning on the confirmation
  red     BLOCK — the order is never submitted, and the reason is logged to `compliance_events`

Two principles decide every ambiguous case:

  * Unknown is not green. If a check cannot be evaluated — broker unreachable, no quote, the daily loss
    limit needs a journal that is still empty — it returns yellow with the reason, never a silent pass.
  * Per-strategy risk blocks may TIGHTEN a default, never loosen it (plan §11). `_tighter` enforces that,
    so a strategy config cannot widen the spread ceiling or raise the loss limit past the global one.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from app.broker import account as broker_account
from app.db import get_client, kill_switch_engaged
from app.marketdata import alpaca
from app.marketdata import repo as md_repo

log = logging.getLogger(__name__)

GREEN, YELLOW, RED = "green", "yellow", "red"
_ORDER = {RED: 0, YELLOW: 1, GREEN: 2}


@dataclass(slots=True)
class Check:
    name: str
    verdict: str
    detail: str

    def as_dict(self) -> dict:
        return {"check": self.name, "verdict": self.verdict, "detail": self.detail}


@dataclass(slots=True)
class GateResult:
    verdict: str
    checks: list[Check] = field(default_factory=list)
    computed_at: str = ""
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict != RED

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "advisory": False, "computed_at": self.computed_at,
                "checks": [c.as_dict() for c in self.checks],
                "blocking": self.blocking, "warnings": self.warnings}

    def reason(self) -> str:
        if self.blocking:
            return "; ".join(self.blocking)
        if self.warnings:
            return "; ".join(self.warnings)
        return "all checks green"


def _tighter(strategy_value, default_value, *, lower_is_tighter: bool):
    """Per-strategy risk may tighten a global default, never loosen it (plan §11)."""
    if strategy_value is None:
        return default_value
    if default_value is None:
        return strategy_value
    return min(strategy_value, default_value) if lower_is_tighter else max(strategy_value, default_value)


def effective_limits(risk: dict, defaults: dict) -> dict:
    return {
        "spread_ceiling_pct": _tighter(risk.get("spread_ceiling_pct"),
                                       defaults.get("spread_ceiling_pct"), lower_is_tighter=True),
        "risk_per_trade_pct": _tighter(risk.get("risk_per_trade_pct"),
                                       defaults.get("risk_per_trade_pct_default"), lower_is_tighter=True),
        "daily_loss_limit_r": _tighter(risk.get("daily_loss_limit_r"),
                                       defaults.get("daily_loss_limit_r"), lower_is_tighter=True),
        "max_concurrent": _tighter(risk.get("max_concurrent"),
                                   defaults.get("max_concurrent_positions"), lower_is_tighter=True),
        "buying_power_buffer_pct": defaults.get("buying_power_buffer_pct", 10.0),
        "require_etb_for_shorts": defaults.get("require_etb_for_shorts", True),
        "wash_trade_guard": defaults.get("wash_trade_guard", True),
    }


# ---------- individual checks ----------

def check_kill_switch(engaged: bool | None = None) -> Check:
    """`engaged` is injectable so Phase 8's drill can force the red branch without flipping the real
    switch (plan §17 criterion 3: every check must be proven capable of blocking). None = read the DB."""
    engaged = kill_switch_engaged() if engaged is None else engaged
    return Check("kill_switch", RED if engaged else GREEN,
                 "ENGAGED — no orders may be placed" if engaged else "not engaged")


def check_account(acct: broker_account.AccountSnapshot) -> Check:
    if not acct.ok:
        return Check("account_status", YELLOW, f"broker unreachable: {acct.error}")
    if acct.trading_blocked or acct.account_blocked:
        return Check("account_status", RED,
                     f"trading_blocked={acct.trading_blocked} account_blocked={acct.account_blocked}")
    return Check("account_status", GREEN, "account active")


def check_buying_power(acct: broker_account.AccountSnapshot, order_value: float, buffer_pct: float) -> Check:
    if not acct.ok or acct.buying_power is None:
        return Check("buying_power", YELLOW, "buying power unknown (broker unreachable)")
    usable = acct.buying_power * (1 - buffer_pct / 100.0)
    if order_value > usable:
        return Check("buying_power", RED,
                     f"order value {order_value:,.2f} exceeds usable buying power {usable:,.2f} "
                     f"({acct.buying_power:,.2f} less a {buffer_pct}% buffer)")
    return Check("buying_power", GREEN,
                 f"order {order_value:,.2f} of {usable:,.2f} usable ({buffer_pct}% buffer held back)")


def check_spread(symbol: str, ceiling_pct: float | None, quote: dict | None) -> Check:
    if not quote or quote.get("spread_pct") is None:
        return Check("spread", YELLOW, f"no quote available for {symbol}")
    spread = quote["spread_pct"]
    if ceiling_pct is None:
        return Check("spread", GREEN, f"{spread}% (no ceiling configured)")
    if spread > ceiling_pct:
        return Check("spread", RED, f"spread {spread}% is above the {ceiling_pct}% ceiling")
    return Check("spread", GREEN, f"spread {spread}% within the {ceiling_pct}% ceiling")


def check_position_risk(entry: float | None, stop: float | None, qty: int | None,
                        equity: float | None, risk_pct: float | None) -> Check:
    if not all([entry, stop, qty, equity, risk_pct]):
        return Check("position_risk", YELLOW, "cannot verify risk (missing entry, stop, qty or equity)")
    per_share = abs(float(entry) - float(stop))
    if per_share <= 0:
        return Check("position_risk", RED, f"stop {stop} equals entry {entry} — the position has no defined risk")
    risk_dollars = qty * per_share
    budget = equity * (risk_pct / 100.0)
    if risk_dollars > budget * 1.05:
        return Check("position_risk", RED,
                     f"risking {risk_dollars:,.2f} ({qty} x {per_share:.4f}) against a "
                     f"{budget:,.2f} budget ({risk_pct}% of equity); max {int(budget // per_share)} shares")
    return Check("position_risk", GREEN,
                 f"risking {risk_dollars:,.2f} of the {budget:,.2f} budget ({risk_pct}% of equity)")


def check_max_concurrent(acct: broker_account.AccountSnapshot, symbol: str, cap: int | None) -> Check:
    if not acct.ok:
        return Check("max_concurrent", YELLOW, f"open positions unknown: {acct.error}")
    if cap is None:
        return Check("max_concurrent", GREEN, f"{acct.open_positions} open, no cap configured")
    already_in = any(p["symbol"] == symbol for p in acct.positions)
    if already_in:
        return Check("max_concurrent", GREEN, f"already holding {symbol}; not a new position")
    if acct.open_positions >= cap:
        return Check("max_concurrent", RED, f"{acct.open_positions} positions open, cap is {cap}")
    return Check("max_concurrent", GREEN, f"{acct.open_positions} of {cap} positions used")


def realized_r_today(day: dt.date) -> tuple[float | None, str]:
    """Today's realized R from `journal`. Returns (value, note); None means not knowable yet."""
    try:
        rows = (get_client().table("journal").select("r_multiple,exit_time")
                .gte("exit_time", f"{day.isoformat()}T00:00:00")
                .lte("exit_time", f"{day.isoformat()}T23:59:59").execute().data or [])
    except Exception as e:  # noqa: BLE001
        return None, f"journal unreachable: {str(e)[:80]}"
    if not rows:
        return 0.0, "no closed trades today"
    total = sum(float(r["r_multiple"]) for r in rows if r.get("r_multiple") is not None)
    return round(total, 3), f"{len(rows)} closed trade(s) today"


def check_daily_loss_limit(day: dt.date, limit_r: float | None,
                           realized: tuple[float | None, str] | None = None) -> Check:
    """`realized` is injectable for the Phase 8 drill; None = read today's R from the journal."""
    value, note = realized if realized is not None else realized_r_today(day)
    if value is None:
        return Check("daily_loss_limit", YELLOW, note)
    if limit_r is None:
        return Check("daily_loss_limit", GREEN, f"{value:+.2f}R today ({note}); no limit configured")
    if value <= -abs(limit_r):
        return Check("daily_loss_limit", RED,
                     f"{value:+.2f}R today has reached the -{abs(limit_r)}R daily limit — no further entries")
    return Check("daily_loss_limit", GREEN, f"{value:+.2f}R today, limit -{abs(limit_r)}R ({note})")


def check_wash_trade(symbol: str, side: str, enabled: bool, resting: list[dict] | None = None) -> Check:
    """Plan §12 rule 7: refuse if a conflicting own resting order exists.

    `resting` is injectable so the Phase 8 drill can prove the red branch fires without placing a real
    opposing order. None = read the live open orders from the broker.
    """
    if not enabled:
        return Check("wash_trade", GREEN, "guard disabled in settings")
    try:
        from app.broker import orders as broker_orders

        resting = broker_orders.open_orders(symbol) if resting is None else resting
    except Exception as e:  # noqa: BLE001
        return Check("wash_trade", YELLOW, f"could not read open orders: {str(e)[:100]}")
    opposite = [o for o in resting if o["side"] != side]
    if opposite:
        ids = ", ".join(o["client_order_id"] or o["id"][:8] for o in opposite[:3])
        return Check("wash_trade", RED,
                     f"a resting {opposite[0]['side']} order on {symbol} would self-cross ({ids})")
    same = [o for o in resting if o["side"] == side]
    if same:
        return Check("wash_trade", YELLOW, f"{len(same)} resting {side} order(s) already on {symbol}")
    return Check("wash_trade", GREEN, f"no resting orders on {symbol}")


def check_shortable(symbol: str, direction: str, stats: dict | None, require_etb: bool) -> Check:
    if direction != "short":
        return Check("shortable", GREEN, "long order; borrow rules do not apply")
    if not stats:
        return Check("shortable", YELLOW, f"no symbol_stats row for {symbol}; borrow status unknown")
    shortable, etb = stats.get("shortable"), stats.get("easy_to_borrow")
    if not shortable:
        return Check("shortable", RED, f"{symbol} is not shortable at the broker")
    if require_etb and not etb:
        return Check("shortable", RED, f"{symbol} is not easy-to-borrow and ETB is required")
    return Check("shortable", GREEN, f"shortable={shortable} easy_to_borrow={etb}")


def check_order_shape(phase: str, order_type: str, extended_hours: bool, tif: str,
                      order_class: str | None) -> Check:
    from app.broker import orders as broker_orders

    problems = broker_orders.validate_shape(phase=phase, order_type=order_type, time_in_force=tif,
                                            extended_hours=extended_hours, order_class=order_class)
    if problems:
        return Check("order_shape", RED, "; ".join(problems))
    return Check("order_shape", GREEN, f"{phase}: {order_type}/{tif}/extended_hours={extended_hours} is legal")


# ---------- the gate ----------

def evaluate(*, symbol: str, direction: str, qty: int | None, entry: float | None, stop: float | None,
             risk: dict, order_type: str = "limit", time_in_force: str = "day",
             extended_hours: bool | None = None, order_class: str | None = None,
             now: dt.datetime | None = None, acct: broker_account.AccountSnapshot | None = None,
             quote: dict | None = None, stats: dict | None = None) -> GateResult:
    """Run every Layer-1 check. Nothing here submits anything; the caller obeys the verdict."""
    from app.broker import orders as broker_orders

    now = now or alpaca.now_et()
    phase = broker_orders.session_phase(now)
    if extended_hours is None:
        extended_hours = phase in ("premarket", "afterhours")

    defaults = md_repo.fetch_setting("guardian_defaults", {}) or {}
    limits = effective_limits(risk or {}, defaults)
    acct = acct if acct is not None else broker_account.snapshot()

    if quote is None:
        try:
            quote = (alpaca.get_latest_quotes([symbol], feed="iex") or {}).get(symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("quote unavailable for %s: %s", symbol, e)
            quote = None
    if stats is None and direction == "short":
        try:
            rows = get_client().table("symbol_stats").select("*").eq("symbol", symbol).execute().data or []
            stats = rows[0] if rows else None
        except Exception:  # noqa: BLE001
            stats = None

    side = "buy" if direction == "long" else "sell"
    order_value = (qty or 0) * float(entry or 0)

    checks = [
        check_kill_switch(),
        check_account(acct),
        check_order_shape(phase, order_type, extended_hours, time_in_force, order_class),
        check_buying_power(acct, order_value, limits["buying_power_buffer_pct"]),
        check_spread(symbol, limits["spread_ceiling_pct"], quote),
        check_position_risk(entry, stop, qty, acct.equity, limits["risk_per_trade_pct"]),
        check_max_concurrent(acct, symbol, limits["max_concurrent"]),
        check_daily_loss_limit(now.date(), limits["daily_loss_limit_r"]),
        check_wash_trade(symbol, side, limits["wash_trade_guard"]),
        check_shortable(symbol, direction, stats, limits["require_etb_for_shorts"]),
    ]
    verdict = min((c.verdict for c in checks), key=lambda v: _ORDER.get(v, 1))
    return GateResult(
        verdict=verdict, checks=checks, computed_at=now.isoformat(),
        blocking=[f"{c.name}: {c.detail}" for c in checks if c.verdict == RED],
        warnings=[f"{c.name}: {c.detail}" for c in checks if c.verdict == YELLOW],
    )


def log_event(*, kind: str, verdict: str | None, reason: str, symbol: str | None = None,
              signal_id: str | None = None, order_id: str | None = None, detail: dict | None = None) -> None:
    """Write to `compliance_events` (plan §11). Never raises — logging must not break execution."""
    try:
        get_client().table("compliance_events").insert([{
            "kind": kind, "verdict": verdict, "symbol": symbol, "signal_id": signal_id,
            "order_id": order_id, "reason": reason[:2000], "detail": detail or {},
        }]).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("compliance_events write failed (%s): %s", kind, e)
