"""`/execute/*` — Phase 5 execution endpoints. Bearer token required on every one.

This is the only router that can cause an order to exist. Every path through it passes the kill switch
and the Guardian Layer-1 gate, and refuses a live account outright (Phase 8 lifts that).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.auth import require_token
from app.broker import account as broker_account
from app.broker import orders as broker_orders
from app.db import get_client, kill_switch_engaged
from app.execution import deploy as deploy_mod
from app.execution import oco as oco_mod
from app.execution import telegram_deploy as tg
from app.guardian import gate as guardian
from app.marketdata import alpaca

router = APIRouter(prefix="/execute", tags=["execution"], dependencies=[Depends(require_token)])


@router.get("/status")
def status() -> dict:
    """Everything that decides whether an order could be placed right now."""
    from app.config import get_settings

    s = get_settings()
    acct = broker_account.snapshot()
    now = alpaca.now_et()
    live_locked = None
    try:
        broker_orders.assert_paper_only()
    except broker_orders.OrderRefused as e:
        live_locked = str(e)
    return {
        "session_phase": broker_orders.session_phase(now), "now_et": now.isoformat(),
        "paper_mode": s.paper_mode, "live_trading_locked": live_locked,
        "kill_switch_engaged": kill_switch_engaged(),
        "account": {k: v for k, v in acct.as_dict().items() if k != "positions"},
        "open_positions": acct.positions,
        "note": "orders require the owner's Deploy tap; auto_approve ships OFF and is not honoured",
    }


@router.post("/gate/preview")
def gate_preview(signal_id: str = Query(..., description="signal to evaluate without submitting")) -> dict:
    """Run the full Guardian Layer-1 gate against a signal and return the verdict. Submits nothing."""
    try:
        signal = deploy_mod.load_signal(signal_id)
    except deploy_mod.DeployRefused as e:
        raise HTTPException(404, str(e)) from e
    risk, code = deploy_mod.strategy_risk(signal["strategy_id"])
    res = guardian.evaluate(symbol=signal["symbol"], direction=signal.get("direction") or "long",
                            qty=signal.get("size"),
                            entry=float(signal["entry_price"]) if signal.get("entry_price") else None,
                            stop=float(signal["stop"]) if signal.get("stop") else None, risk=risk)
    return {"signal_id": signal_id, "symbol": signal["symbol"], "strategy": code} | res.as_dict()


@router.post("/deploy")
def deploy(signal_id: str = Query(...), dry_run: bool = False, notify: bool = True,
           qty: int | None = Query(None, ge=1, description="reduce size only; never raises it")) -> dict:
    """The Deploy tap: kill switch → Guardian gate → limit order → `orders`. Nothing else submits."""
    try:
        res = deploy_mod.deploy(signal_id, dry_run=dry_run, notify=notify, override_qty=qty)
    except deploy_mod.DeployRefused as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e
    return res.as_dict()


@router.post("/cancel")
def cancel_signal(signal_id: str = Query(...), reason: str = "cancelled by owner") -> dict:
    """Decline a proposed signal. Records the decision; places nothing."""
    try:
        signal = deploy_mod.load_signal(signal_id)
    except deploy_mod.DeployRefused as e:
        raise HTTPException(404, str(e)) from e
    deploy_mod.mark_signal(signal_id, "rejected", reason)
    guardian.log_event(kind="gate_block", verdict=None, symbol=signal["symbol"], signal_id=signal_id,
                       reason=f"owner cancelled: {reason}")
    return {"signal_id": signal_id, "symbol": signal["symbol"], "status": "rejected", "reason": reason}


@router.post("/telegram/callback")
def telegram_callback(payload: dict = Body(...)) -> dict:  # noqa: B008 — FastAPI dependency style
    """Handle a Deploy / Cancel button tap forwarded by n8n's Telegram trigger.

    The payload is untrusted: only the configured owner chat is honoured, and the signal id is
    re-validated from the database before anything happens.
    """
    cq = payload.get("callback_query") or payload
    data = cq.get("data")
    chat = (((cq.get("message") or {}).get("chat")) or {}).get("id") or cq.get("chat_id")
    cq_id = cq.get("id")
    message_id = (cq.get("message") or {}).get("message_id")

    if not tg.is_owner(chat):
        if cq_id:
            tg.answer_callback(cq_id, "Not authorised.")
        raise HTTPException(403, f"chat {chat!r} is not the configured owner chat")

    try:
        action, signal_id = tg.parse_callback(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    if action == "cancel":
        out = cancel_signal(signal_id=signal_id, reason="cancelled from Telegram")
        if cq_id:
            tg.answer_callback(cq_id, f"Cancelled {out['symbol']}.")
        if message_id:
            tg.edit_card(chat, message_id, f"✖️ Cancelled {out['symbol']} — no order placed.")
        return {"action": "cancel"} | out

    try:
        res = deploy_mod.deploy(signal_id, notify=False)
    except deploy_mod.DeployRefused as e:
        if cq_id:
            tg.answer_callback(cq_id, f"Refused: {str(e)[:150]}")
        if message_id:
            tg.edit_card(chat, message_id, f"⛔ Not deployed: {str(e)[:600]}")
        raise HTTPException(409, str(e)) from e

    if res.submitted:
        toast = f"Order placed: {res.symbol} {res.order['status']}"
        card = (f"✅ Deployed {res.symbol}\n{res.order['side'].upper()} {res.order['qty']} @ "
                f"{res.order['limit_price']}\nstatus {res.order['status']} · guardian {res.gate_verdict}")
    else:
        toast = f"Blocked: {res.reason[:150]}"
        card = f"⛔ Blocked {res.symbol}\n{res.reason[:600]}\nNo order was placed."
    if cq_id:
        tg.answer_callback(cq_id, toast)
    if message_id:
        tg.edit_card(chat, message_id, card)
    return {"action": "deploy"} | res.as_dict()


@router.post("/push-signals")
def push_signals(date: dt.date | None = None, limit: int = Query(10, ge=1, le=25)) -> dict:
    """Push today's proposed signals to Telegram as cards with Deploy / Cancel buttons."""
    day = (date or alpaca.now_et().date()).isoformat()
    rows = (get_client().table("signals").select("*")
            .eq("signal_date", day).eq("status", "proposed")
            .order("confidence", desc=True).limit(limit).execute().data or [])
    codes = {r["id"]: r["code"] for r in
             (get_client().table("strategy_configs").select("id,code").execute().data or [])}
    pushed = []
    for r in rows:
        sent = tg.push_signal(r, codes.get(r["strategy_id"], ""))
        pushed.append({"signal_id": r["id"], "symbol": r["symbol"], "sent": bool(sent and sent.get("ok"))})
    return {"signal_date": day, "count": len(pushed), "pushed": pushed}


@router.post("/oco/attach")
def oco_attach(dry_run: bool = False, notify: bool = True) -> dict:
    """09:30 job: give every open position a real broker-side OCO (plan §12 rule 4)."""
    return oco_mod.attach_oco(dry_run=dry_run, notify=notify).as_dict()


@router.post("/orders/sync")
def orders_sync(date: dt.date | None = None) -> dict:
    """Reconcile stored orders against Alpaca — fills, cancels and rejections."""
    return oco_mod.sync_orders(day=date)


@router.get("/orders")
def list_orders(date: dt.date | None = None, limit: int = Query(100, le=500)) -> dict:
    day = (date or alpaca.now_et().date()).isoformat()
    rows = (get_client().table("orders").select("*")
            .gte("created_at", f"{day}T00:00:00").order("created_at", desc=True)
            .limit(limit).execute().data or [])
    return {"date": day, "count": len(rows), "rows": rows}


@router.get("/compliance")
def list_compliance(date: dt.date | None = None, limit: int = Query(100, le=500)) -> dict:
    day = (date or alpaca.now_et().date()).isoformat()
    rows = (get_client().table("compliance_events").select("*")
            .gte("event_time", f"{day}T00:00:00").order("event_time", desc=True)
            .limit(limit).execute().data or [])
    return {"date": day, "count": len(rows), "rows": rows}


@router.post("/kill-switch")
def set_kill_switch(engage: bool = Query(..., description="true halts all order placement")) -> dict:
    """Flip the global halt. Every execution path checks this first and fails safe if the DB is down."""
    get_client().table("app_settings").update({"value": engage}).eq("key", "kill_switch").execute()
    guardian.log_event(kind="kill_switch", verdict="red" if engage else None,
                       reason=f"kill switch {'ENGAGED' if engage else 'released'} by the owner")
    from app.notify import send_telegram

    send_telegram(f"{'🛑 KILL SWITCH ENGAGED — no orders can be placed' if engage else '✅ Kill switch released'}")
    return {"kill_switch": engage, "verified": kill_switch_engaged()}
