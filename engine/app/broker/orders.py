"""Order submission to Alpaca — the ONLY code in TradeHub that can create, replace or cancel an order.

Everything here is deliberately narrow and loud. Three gates stand in front of every submission and
none of them can be skipped by a caller:

  1. `assert_paper_only()` — Phase 5 physically refuses to trade a live account (owner's decision,
     docs/DECISIONS.md #37). It is lifted in Phase 8 by a deliberate one-line change, not by a flag.
  2. `app.db.kill_switch_engaged()` — plan §12 rule 8, checked at the top of every execution path.
     It fails SAFE: if the database cannot be reached it returns True and nothing trades.
  3. The Guardian Layer-1 gate — enforced by the caller in `app/execution/`, never bypassed.

Extended-hours rules (plan §3.4 / §12 rule 2) are enforced here rather than trusted from the caller:
pre-market accepts ONLY limit + TIF day + extended_hours=true. Brackets, stops, stop-limits and market
orders are rejected outright pre-market — Alpaca either refuses them or, worse, accepts a stop that
silently never triggers.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from app.config import get_settings
from app.db import kill_switch_engaged
from app.marketdata import alpaca

log = logging.getLogger(__name__)

PREMARKET_START = dt.time(4, 0)
REGULAR_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)


class OrderRefused(RuntimeError):
    """A submission was refused before it reached the broker. Never retried blindly by the caller."""


def assert_paper_only() -> None:
    """Phase 5 trades paper only — enforced in code, not by configuration.

    A wrong flag, a stray edit or a bug must not be able to reach real money before the strategies have
    proven themselves over Phase 8's paper month. Lifting this is a deliberate edit with a commit
    attached, reviewed on its own.
    """
    s = get_settings()
    if not s.paper_mode:
        raise OrderRefused(
            "PAPER_MODE is false, but live trading is disabled in code until Phase 8's paper month is "
            "complete (docs/DECISIONS.md #37). No order was sent.")
    if "paper" not in s.alpaca_paper_base_url:
        raise OrderRefused(
            f"ALPACA_PAPER_BASE_URL does not look like a paper endpoint ({s.alpaca_paper_base_url}). "
            "Refusing to submit.")


def _trading_client():
    from alpaca.trading.client import TradingClient

    s = get_settings()
    if not s.alpaca_configured:
        raise OrderRefused("Alpaca keys are not configured")
    return TradingClient(s.alpaca_paper_key_id, s.alpaca_paper_secret_key, paper=True)


def session_phase(now: dt.datetime | None = None) -> str:
    """premarket | regular | afterhours | closed — decides which order shapes are legal."""
    now = now or alpaca.now_et()
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if PREMARKET_START <= t < REGULAR_OPEN:
        return "premarket"
    if REGULAR_OPEN <= t < REGULAR_CLOSE:
        return "regular"
    if REGULAR_CLOSE <= t < dt.time(20, 0):
        return "afterhours"
    return "closed"


def validate_shape(*, phase: str, order_type: str, time_in_force: str, extended_hours: bool,
                   order_class: str | None) -> list[str]:
    """Plan §3.4 / §12 rule 2. Returns the reasons this order shape is illegal for this session."""
    problems: list[str] = []
    if phase in ("premarket", "afterhours"):
        if order_type != "limit":
            problems.append(f"{phase}: only limit orders are accepted (got {order_type!r}); "
                            "stop and stop-limit orders do NOT trigger outside regular hours and "
                            "market orders are rejected")
        if time_in_force != "day":
            problems.append(f"{phase}: time_in_force must be 'day' (got {time_in_force!r})")
        if not extended_hours:
            problems.append(f"{phase}: extended_hours must be true")
        if order_class and order_class != "simple":
            problems.append(f"{phase}: bracket/OCO orders are not accepted outside regular hours "
                            f"(got order_class={order_class!r}) — pre-market protection is the "
                            "synthetic stop monitor")
    elif phase == "closed":
        problems.append("the market is closed (outside 04:00-20:00 ET on a weekday)")
    return problems


def submit(*, symbol: str, side: str, qty: int, order_type: str = "limit", limit_price: float | None = None,
           stop_price: float | None = None, time_in_force: str = "day", extended_hours: bool | None = None,
           order_class: str | None = None, client_order_id: str | None = None,
           now: dt.datetime | None = None, take_profit: dict | None = None,
           stop_loss: dict | None = None) -> dict:
    """Submit one order. Raises OrderRefused before contacting the broker if anything is wrong.

    Returns the raw Alpaca order as a dict, plus the request we sent, for `orders` logging.
    """
    assert_paper_only()
    if kill_switch_engaged():
        raise OrderRefused("kill switch is ENGAGED (app_settings.kill_switch) — no orders may be placed")

    now = now or alpaca.now_et()
    phase = session_phase(now)
    if extended_hours is None:
        extended_hours = phase in ("premarket", "afterhours")

    problems = validate_shape(phase=phase, order_type=order_type, time_in_force=time_in_force,
                              extended_hours=extended_hours, order_class=order_class)
    if qty is None or qty <= 0:
        problems.append(f"qty must be positive (got {qty!r})")
    if order_type == "limit" and not limit_price:
        problems.append("a limit order needs a limit_price")
    if side not in ("buy", "sell"):
        problems.append(f"side must be buy or sell (got {side!r})")
    if problems:
        raise OrderRefused("; ".join(problems))

    from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, StopLimitOrderRequest

    coid = client_order_id or f"th-{uuid.uuid4().hex[:20]}"
    common = {
        "symbol": symbol, "qty": qty,
        "side": OrderSide.BUY if side == "buy" else OrderSide.SELL,
        "time_in_force": TimeInForce(time_in_force),
        "extended_hours": extended_hours,
        "client_order_id": coid,
    }
    if order_class and order_class != "simple":
        common["order_class"] = OrderClass(order_class)
        if take_profit:
            common["take_profit"] = take_profit
        if stop_loss:
            common["stop_loss"] = stop_loss

    if order_type == "limit":
        req = LimitOrderRequest(limit_price=limit_price, **common)
    elif order_type == "stop_limit":
        req = StopLimitOrderRequest(limit_price=limit_price, stop_price=stop_price, **common)
    elif order_type == "market":
        req = MarketOrderRequest(**common)
    else:
        raise OrderRefused(f"unsupported order_type {order_type!r}")

    request_log = {"symbol": symbol, "side": side, "qty": qty, "order_type": order_type,
                   "limit_price": limit_price, "stop_price": stop_price, "time_in_force": time_in_force,
                   "extended_hours": extended_hours, "order_class": order_class,
                   "client_order_id": coid, "session_phase": phase}
    log.info("submitting order: %s", request_log)
    # NOT wrapped in a retry helper — deliberately, and Phase 8 left it that way. A submission
    # is not idempotent: a timeout could mean the broker never saw the order or that it filled and the
    # reply was lost, and nothing at this end can tell those apart. Retrying the second case buys the
    # position twice. A failure here surfaces to the caller, which records it and stops; the truth is
    # then recovered by reconciliation (`/execute/orders/sync`), not by guessing.
    # `client_order_id` is the second half of that safety: Alpaca rejects a duplicate, so even a manual
    # re-send of the same ticket cannot silently double up.
    order = _trading_client().submit_order(req)
    return {"order": _order_to_dict(order), "request": request_log}


def _order_to_dict(o) -> dict:
    def num(v):
        return float(v) if v is not None else None

    return {
        "id": str(o.id), "client_order_id": o.client_order_id, "symbol": o.symbol,
        "side": getattr(o.side, "value", str(o.side)),
        "order_type": getattr(o.order_type, "value", str(getattr(o, "order_type", ""))),
        "order_class": getattr(o.order_class, "value", str(getattr(o, "order_class", "") or "")),
        "time_in_force": getattr(o.time_in_force, "value", str(o.time_in_force)),
        "qty": int(float(o.qty)) if o.qty is not None else None,
        "filled_qty": int(float(o.filled_qty or 0)),
        "limit_price": num(o.limit_price), "stop_price": num(o.stop_price),
        "filled_avg_price": num(o.filled_avg_price),
        "status": getattr(o.status, "value", str(o.status)),
        "extended_hours": bool(o.extended_hours),
        "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        "filled_at": o.filled_at.isoformat() if getattr(o, "filled_at", None) else None,
        "legs": [_order_to_dict(x) for x in (getattr(o, "legs", None) or [])],
    }


def get_order(order_id: str) -> dict:
    return _order_to_dict(_trading_client().get_order_by_id(order_id))


def open_orders(symbol: str | None = None) -> list[dict]:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    req = GetOrdersRequest(status=QueryOrderStatus.OPEN,
                           symbols=[symbol] if symbol else None)
    return [_order_to_dict(o) for o in _trading_client().get_orders(req)]


def cancel(order_id: str) -> None:
    assert_paper_only()
    _trading_client().cancel_order_by_id(order_id)


def close_position_limit(symbol: str, qty: int, limit_price: float, side: str,
                         now: dt.datetime | None = None) -> dict:
    """Exit an open position with a marketable limit — the synthetic stop's firing mechanism.

    Deliberately a LIMIT even in regular hours: a market order on a low-float name pre-market is how a
    stop becomes a 20% slip. The caller sets `limit_price` aggressively enough to fill.
    """
    return submit(symbol=symbol, side=side, qty=qty, order_type="limit", limit_price=limit_price,
                  time_in_force="day", now=now)
