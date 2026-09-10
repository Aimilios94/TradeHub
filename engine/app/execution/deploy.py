"""The Deploy path: an approved signal → kill switch → Guardian gate → order → `orders` (plan §12).

Order of operations is the safety property. Nothing is submitted until the kill switch is checked, the
signal is confirmed to be a live proposal, and the Guardian Layer-1 gate returns non-red. A red verdict
is logged to `compliance_events` and the signal is marked rejected — never retried automatically.

Human-in-the-loop (§12 rule 1): `auto_approve` exists as a setting, ships OFF, and this module refuses
to act on it. A deploy always originates from the owner's tap.
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

DEPLOYABLE_STATUSES = ("proposed",)


class DeployRefused(RuntimeError):
    """The deploy did not reach the broker. The reason is always recorded."""


@dataclass(slots=True)
class DeployResult:
    signal_id: str
    symbol: str = ""
    submitted: bool = False
    gate_verdict: str = ""
    gate: dict = field(default_factory=dict)
    order: dict | None = None
    order_row_id: str | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"signal_id": self.signal_id, "symbol": self.symbol, "submitted": self.submitted,
                "gate_verdict": self.gate_verdict, "gate": self.gate, "order": self.order,
                "order_row_id": self.order_row_id, "reason": self.reason, "warnings": self.warnings}


def load_signal(signal_id: str) -> dict:
    rows = get_client().table("signals").select("*").eq("id", signal_id).execute().data or []
    if not rows:
        raise DeployRefused(f"signal {signal_id} not found")
    return rows[0]


def strategy_risk(strategy_id: str) -> tuple[dict, str]:
    rows = (get_client().table("strategy_configs").select("code,risk,status")
            .eq("id", strategy_id).execute().data or [])
    if not rows:
        return {}, "?"
    return rows[0].get("risk") or {}, rows[0].get("code", "?")


def write_order_row(*, signal: dict, submitted: dict, gate: guardian.GateResult,
                    quote_mid: float | None) -> str | None:
    o = submitted["order"]
    req = submitted["request"]
    row = {
        "signal_id": signal["id"], "strategy_id": signal["strategy_id"], "source": "signal",
        "alpaca_order_id": o["id"], "client_order_id": o["client_order_id"],
        "symbol": o["symbol"], "side": o["side"], "order_type": req["order_type"],
        "time_in_force": o["time_in_force"], "qty": o["qty"],
        "limit_price": o["limit_price"], "stop_price": o["stop_price"],
        "extended_hours": o["extended_hours"], "order_class": req.get("order_class") or "simple",
        "status": o["status"],
        "status_timeline": [{"at": dt.datetime.now(dt.UTC).isoformat(), "status": o["status"]}],
        "filled_qty": o["filled_qty"], "filled_avg_price": o["filled_avg_price"],
        "filled_at": o["filled_at"], "quote_mid_at_submit": quote_mid,
        "submitted_at": o["submitted_at"], "raw_request": req,
        "raw_response": o | {"guardian": gate.as_dict()},
    }
    try:
        res = get_client().table("orders").insert([row]).execute()
        return (res.data or [{}])[0].get("id")
    except Exception as e:  # noqa: BLE001
        log.error("orders row write failed for %s: %s", o["id"], e)
        guardian.log_event(kind="failure", verdict=None, symbol=o["symbol"], signal_id=signal["id"],
                           reason=f"order {o['id']} submitted but the orders row failed to write: {e}",
                           detail={"alpaca_order_id": o["id"]})
        return None


def mark_signal(signal_id: str, status: str, reason: str | None = None) -> None:
    patch = {"status": status, "decided_at": dt.datetime.now(dt.UTC).isoformat()}
    if reason:
        patch["reject_reason"] = reason[:2000]
    try:
        get_client().table("signals").update(patch).eq("id", signal_id).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("signal %s status update failed: %s", signal_id, e)


def deploy(signal_id: str, *, now: dt.datetime | None = None, dry_run: bool = False,
           notify: bool = True, override_qty: int | None = None) -> DeployResult:
    """Approve and submit one signal. The owner's tap is the only thing that calls this."""
    now = now or alpaca.now_et()
    res = DeployResult(signal_id=signal_id)

    signal = load_signal(signal_id)
    res.symbol = signal["symbol"]
    if signal["status"] not in DEPLOYABLE_STATUSES:
        res.reason = (f"signal is '{signal['status']}', not 'proposed' — "
                      f"{'already acted on' if signal['status'] in ('approved', 'executed') else 'not deployable'}")
        raise DeployRefused(res.reason)

    risk, code = strategy_risk(signal["strategy_id"])
    qty = override_qty if override_qty is not None else signal.get("size")
    entry, stop = signal.get("entry_price"), signal.get("stop")
    direction = signal.get("direction") or "long"
    entry = float(entry) if entry is not None else None
    stop = float(stop) if stop is not None else None

    acct = broker_account.snapshot()
    gate = guardian.evaluate(symbol=signal["symbol"], direction=direction, qty=qty, entry=entry, stop=stop,
                             risk=risk, order_type="limit", now=now, acct=acct)
    res.gate = gate.as_dict()
    res.gate_verdict = gate.verdict
    res.warnings = gate.warnings

    if not gate.passed:
        res.reason = gate.reason()
        guardian.log_event(kind="gate_block", verdict=gate.verdict, symbol=signal["symbol"],
                           signal_id=signal_id, reason=res.reason, detail=gate.as_dict())
        mark_signal(signal_id, "rejected", f"guardian_block: {res.reason}")
        if notify:
            send_telegram(f"⛔ BLOCKED {signal['symbol']} ({code})\n{res.reason}\nNo order was placed.")
        return res

    if gate.warnings:
        guardian.log_event(kind="gate_warn", verdict=gate.verdict, symbol=signal["symbol"],
                           signal_id=signal_id, reason="; ".join(gate.warnings), detail=gate.as_dict())

    if dry_run:
        res.reason = "dry_run: gate passed, no order submitted"
        return res

    quote_mid = None
    try:
        q = (alpaca.get_latest_quotes([signal["symbol"]], feed="iex") or {}).get(signal["symbol"]) or {}
        if q.get("bid") and q.get("ask"):
            quote_mid = round((q["bid"] + q["ask"]) / 2, 4)
    except Exception as e:  # noqa: BLE001 — spread-cost reference only; never blocks the order
        log.info("quote mid unavailable for %s: %s", signal["symbol"], e)

    side = "buy" if direction == "long" else "sell"
    try:
        submitted = broker_orders.submit(symbol=signal["symbol"], side=side, qty=qty, order_type="limit",
                                         limit_price=entry, time_in_force="day", now=now)
    except broker_orders.OrderRefused as e:
        res.reason = f"broker refused: {e}"
        guardian.log_event(kind="failure", verdict="red", symbol=signal["symbol"], signal_id=signal_id,
                           reason=res.reason, detail={"gate": gate.as_dict()})
        mark_signal(signal_id, "rejected", res.reason)
        if notify:
            send_telegram(f"⛔ REFUSED {signal['symbol']} ({code})\n{res.reason}")
        return res
    except Exception as e:
        res.reason = f"submit failed: {type(e).__name__}: {str(e)[:200]}"
        guardian.log_event(kind="failure", verdict="red", symbol=signal["symbol"], signal_id=signal_id,
                           reason=res.reason)
        if notify:
            send_telegram(f"⚠️ ORDER ERROR {signal['symbol']} ({code})\n{res.reason}")
        raise DeployRefused(res.reason) from e

    res.submitted = True
    res.order = submitted["order"]
    res.order_row_id = write_order_row(signal=signal, submitted=submitted, gate=gate, quote_mid=quote_mid)
    mark_signal(signal_id, "executed" if res.order["status"] == "filled" else "approved")

    if notify:
        warn = f"\n⚠️ {'; '.join(gate.warnings)}" if gate.warnings else ""
        send_telegram(
            f"✅ ORDER PLACED {signal['symbol']} ({code})\n"
            f"{side.upper()} {qty} @ limit {entry}  stop {stop}  target {signal.get('target')}\n"
            f"status {res.order['status']} · extended_hours={res.order['extended_hours']}\n"
            f"guardian {gate.verdict}{warn}")
    return res
