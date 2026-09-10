"""09:30 OCO attachment and order reconciliation (plan §12 rules 4 and 5).

Pre-market a position has no real stop — the broker will accept one but it does not trigger outside
regular hours (§3.4), which is precisely the trap the synthetic monitor covers. At the open that
protection has to become real: for every open position with a plan, attach a one-cancels-other pair
(take-profit limit + stop-loss) so the position is protected by the broker rather than by our process.

`sync_orders` reconciles what we recorded against what Alpaca actually did — fills, cancels, rejections
that happened while nothing was watching. It is the only place `orders` rows are updated after insert.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from app.broker import account as broker_account
from app.broker import orders as broker_orders
from app.db import get_client
from app.guardian import gate as guardian
from app.marketdata import alpaca
from app.notify import send_telegram

log = logging.getLogger(__name__)


@dataclass(slots=True)
class OcoResult:
    trade_date: str
    as_of: str
    attached: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"trade_date": self.trade_date, "as_of": self.as_of, "attached": self.attached,
                "skipped": self.skipped, "errors": self.errors}

    def summary(self) -> str:
        lines = [f"🛡️ 09:30 OCO attach {self.trade_date} @ {self.as_of} ET"]
        for a in self.attached:
            lines.append(f"   ✅ {a['symbol']} {a['qty']} · stop {a['stop']} / target {a['target']}")
        for s in self.skipped:
            lines.append(f"   – {s['symbol']}: {s['reason']}")
        if self.errors:
            lines.append(f"⚠️ {len(self.errors)} error(s): {self.errors[0][:120]}")
        if not self.attached and not self.skipped:
            lines.append("   no open positions")
        return "\n".join(lines)


def plan_for_position(symbol: str, day: dt.date) -> dict | None:
    """The signal that produced this position today, for its stop and target."""
    rows = (get_client().table("signals").select("*")
            .eq("symbol", symbol).eq("signal_date", day.isoformat())
            .in_("status", ["approved", "executed"]).execute().data or [])
    return rows[0] if rows else None


def attach_oco(*, now: dt.datetime | None = None, dry_run: bool = False, notify: bool = True) -> OcoResult:
    """For each open position with a plan, attach a real OCO pair. Idempotent: skips ones already covered."""
    now = now or alpaca.now_et()
    day = now.date()
    res = OcoResult(trade_date=day.isoformat(), as_of=now.strftime("%H:%M"))

    phase = broker_orders.session_phase(now)
    if phase != "regular":
        res.errors.append(f"session is '{phase}' — real stops only attach during regular hours (§3.4)")
        if notify:
            send_telegram(res.summary())
        return res

    acct = broker_account.snapshot()
    if not acct.ok:
        res.errors.append(f"broker unreachable: {acct.error}")
        guardian.log_event(kind="failure", verdict="red", reason=f"OCO attach: broker unreachable: {acct.error}")
        if notify:
            send_telegram(res.summary())
        return res

    for pos in acct.positions:
        symbol = pos["symbol"]
        try:
            resting = broker_orders.open_orders(symbol)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{symbol}: could not read open orders: {str(e)[:120]}")
            continue
        if any(o["order_class"] in ("oco", "bracket") or o["order_type"] in ("stop", "stop_limit")
               for o in resting):
            res.skipped.append({"symbol": symbol, "reason": "already protected by a resting stop/OCO"})
            continue

        plan = plan_for_position(symbol, day)
        if not plan or plan.get("stop") is None or plan.get("target") is None:
            res.skipped.append({"symbol": symbol, "reason": "no plan with both a stop and a target for today"})
            guardian.log_event(kind="gate_warn", verdict="yellow", symbol=symbol,
                               reason=f"{symbol} is open at 09:30 with no stop/target plan to attach")
            continue

        qty = abs(int(pos["qty"]))
        long_side = float(pos["qty"]) > 0
        side = "sell" if long_side else "buy"
        stop, target = float(plan["stop"]), float(plan["target"])
        if dry_run:
            res.attached.append({"symbol": symbol, "qty": qty, "stop": stop, "target": target,
                                 "dry_run": True})
            continue
        try:
            submitted = broker_orders.submit(
                symbol=symbol, side=side, qty=qty, order_type="limit", limit_price=target,
                time_in_force="day", extended_hours=False, order_class="oco", now=now,
                take_profit={"limit_price": target},
                stop_loss={"stop_price": stop})
        except Exception as e:  # noqa: BLE001
            msg = f"{symbol}: OCO submit failed: {type(e).__name__}: {str(e)[:160]}"
            res.errors.append(msg)
            guardian.log_event(kind="failure", verdict="red", symbol=symbol, reason=msg)
            continue

        o = submitted["order"]
        try:
            get_client().table("orders").insert([{
                "signal_id": plan["id"], "strategy_id": plan["strategy_id"], "source": "exit_plugin",
                "alpaca_order_id": o["id"], "client_order_id": o["client_order_id"], "symbol": symbol,
                "side": side, "order_type": "limit", "time_in_force": o["time_in_force"], "qty": qty,
                "limit_price": target, "stop_price": stop, "extended_hours": False, "order_class": "oco",
                "status": o["status"],
                "status_timeline": [{"at": dt.datetime.now(dt.UTC).isoformat(), "status": o["status"]}],
                "submitted_at": o["submitted_at"], "raw_request": submitted["request"], "raw_response": o,
            }]).execute()
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{symbol}: OCO submitted but not logged: {str(e)[:120]}")
        res.attached.append({"symbol": symbol, "qty": qty, "stop": stop, "target": target,
                             "alpaca_order_id": o["id"]})

    if notify:
        send_telegram(res.summary())
    return res


def sync_orders(*, day: dt.date | None = None, limit: int = 200) -> dict:
    """Reconcile stored `orders` rows against Alpaca. The only updater of an order row after insert."""
    day = day or alpaca.now_et().date()
    rows = (get_client().table("orders").select("*")
            .gte("created_at", f"{day.isoformat()}T00:00:00")
            .limit(limit).execute().data or [])
    updated, filled, errors = 0, [], []
    for r in rows:
        if not r.get("alpaca_order_id"):
            continue
        try:
            live = broker_orders.get_order(r["alpaca_order_id"])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{r['symbol']}: {type(e).__name__}: {str(e)[:100]}")
            continue
        if live["status"] == r["status"] and live["filled_qty"] == r["filled_qty"]:
            continue
        timeline = list(r.get("status_timeline") or [])
        timeline.append({"at": dt.datetime.now(dt.UTC).isoformat(), "status": live["status"],
                         "filled_qty": live["filled_qty"]})
        try:
            get_client().table("orders").update({
                "status": live["status"], "filled_qty": live["filled_qty"],
                "filled_avg_price": live["filled_avg_price"], "filled_at": live["filled_at"],
                "status_timeline": timeline, "raw_response": live,
            }).eq("id", r["id"]).execute()
            updated += 1
            if live["status"] == "filled" and r["status"] != "filled":
                filled.append({"symbol": live["symbol"], "qty": live["filled_qty"],
                               "price": live["filled_avg_price"], "side": live["side"]})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{r['symbol']}: update failed: {str(e)[:100]}")
    return {"checked": len(rows), "updated": updated, "newly_filled": filled, "errors": errors}
