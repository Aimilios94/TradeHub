"""`/ops/*` — Phase 8 endpoints: health, the failure policy, the Guardian drill and the go-live scorecard.

Read-mostly. The two that write are `/ops/paper-month/start` (stamps day zero) and `/ops/drill`
(files its result); neither can place, cancel or modify an order.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_token
from app.ops import acceptance as acceptance_mod
from app.ops import backup as backup_mod
from app.ops import drill as drill_mod
from app.ops import failures as failures_mod
from app.ops import preflight as preflight_mod
from app.ops import resilience

router = APIRouter(prefix="/ops", tags=["ops"], dependencies=[Depends(require_token)])


@router.get("/preflight")
def preflight(symbol: str = Query(preflight_mod.REFERENCE_SYMBOL),
              monitor: bool = Query(True, description="include the synthetic stop monitor heartbeat")) -> dict:
    """Plan §33 in one call: may the engine trade right now, and if not, why not."""
    return preflight_mod.run(symbol=symbol.upper(), include_monitor=monitor).as_dict()


@router.get("/quote-age")
def quote_age(symbol: str = Query(preflight_mod.REFERENCE_SYMBOL)) -> dict:
    """How old the newest IEX quote is. SIP is excluded on purpose — the free plan delays it ~15 min
    by subscription, so measuring staleness against it would flag a healthy feed every day."""
    age, note = preflight_mod.quote_age_seconds(symbol.upper())
    return {"symbol": symbol.upper(), "feed": "iex", "age_seconds": age, "note": note,
            "limit_seconds": preflight_mod.policy()["data_stale_seconds"]}


@router.get("/rate-limits")
def rate_limits() -> dict:
    """The pacing the engine applies to itself, per provider, and where each number came from."""
    cfg = {p: resilience.config_for(p) for p in sorted(set(resilience.DEFAULT_LIMITS) | set(resilience.limits()))}
    return {"providers": cfg, "max_delay_s": resilience.MAX_DELAY_S,
            "note": "Reads are retried on transient failures only. Order submission is never auto-retried "
                    "(app/ops/resilience.guard_write): a broker timeout cannot be distinguished from a "
                    "silent success, so a retry risks a duplicate position. Reconcile instead."}


@router.post("/drill")
def run_drill(live_kill_switch: bool = Query(False, description="also engage and release the REAL kill switch"),
              notify: bool = Query(False), store: bool = Query(True)) -> dict:
    """Force every Guardian check to block, once (§17 criterion 3). Submits nothing."""
    try:
        return drill_mod.run(live_kill_switch=live_kill_switch, store=store, notify=notify).as_dict()
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/drill")
def last_drill(within_days: int = Query(90, ge=1, le=365)) -> dict:
    last = drill_mod.latest(within_days=within_days)
    return {"found": bool(last), "drill": last,
            "note": "no drill on record — the Guardian's ability to block is unproven" if not last else ""}


@router.get("/acceptance")
def acceptance(notify: bool = Query(False, description="also send the plain-language summary to Telegram")) -> dict:
    """The §17 gate to real money, measured. Nothing here can grant permission — see the note field."""
    card = acceptance_mod.scorecard()
    if notify:
        from app.notify import send_telegram

        send_telegram(acceptance_mod.written_summary(card))
    return card.as_dict() | {"summary": acceptance_mod.written_summary(card)}


@router.get("/paper-month")
def paper_month() -> dict:
    return acceptance_mod.window() | {"criterion": acceptance_mod.criterion_2_paper_month().as_dict()}


@router.post("/paper-month/start")
def paper_month_start(
        date: dt.date | None = Query(None, description="defaults to today (ET)"),  # noqa: B008 — FastAPI style
) -> dict:
    """Stamp day zero of the paper month. Restarting resets the four-week clock, by design."""
    return acceptance_mod.start_paper_month(date)


@router.get("/reconcile")
def reconcile(days: int = Query(30, ge=1, le=365)) -> dict:
    """Guardian Layer 2 against Alpaca: is every fee row either backed by an activity id or labelled
    an estimate? A paper account is never charged, so an unlabelled fee would be an invented charge."""
    return acceptance_mod.reconcile_ledger(days=days)


@router.get("/failures")
def failures(days: int = Query(7, ge=1, le=90), kind: str | None = None, limit: int = Query(100, le=500)) -> dict:
    """What has been going wrong, deduped alerts included — the row is written every time even when the
    Telegram ping is suppressed."""
    from app.db import get_client

    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).isoformat()
    q = (get_client().table("compliance_events").select("*")
         .gte("event_time", since).order("event_time", desc=True).limit(limit))
    if kind:
        q = q.eq("kind", kind)
    rows = q.execute().data or []
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"since": since, "count": len(rows), "by_kind": counts, "rows": rows}


@router.post("/failures/acknowledge")
def acknowledge(days: int = Query(30, ge=1, le=365), kind: str = Query("failure")) -> dict:
    """Mark failures as seen. Criterion 2 counts UNACKNOWLEDGED execution errors, so this is how a
    problem that has been understood and fixed stops holding the paper month open — acknowledging is a
    statement that it was dealt with, and the rows stay readable forever either way."""
    from app.db import get_client

    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).isoformat()
    res = (get_client().table("compliance_events").update({"acknowledged": True})
           .eq("kind", kind).eq("acknowledged", False).gte("event_time", since).execute())
    return {"acknowledged": len(res.data or []), "kind": kind, "since": since}


@router.post("/backup")
def backup(tables: str | None = Query(None, description="comma-separated subset; default is all of them"),
           notify: bool = Query(True), prune: bool = Query(True)) -> dict:
    """Take a backup now (workflow 11 calls this nightly). An incomplete backup alerts by itself —
    a backup missing three tables is not a backup, and finding that out during a restore is too late."""
    try:
        names = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
        return backup_mod.run_backup(tables=names, do_prune=prune, notify=notify)["summary"]
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {str(e)[:300]}") from e


@router.get("/backups")
def backups() -> dict:
    """What backups exist on disk. Restoring is CLI-only (scripts/restore.py) and deliberately not
    reachable over HTTP."""
    rows = backup_mod.list_backups()
    return {"path": str(backup_mod.root()), "count": len(rows), "backups": rows,
            "restore": "cd engine, then: python scripts/restore.py <folder> --yes"}


@router.post("/selftest")
def selftest(notify: bool = Query(False)) -> dict:
    """The onboarding connectivity test (§33), runnable any time: broker, data feed, database,
    Telegram and the kill switch's readability. Places no order and flips nothing."""
    from app.notify import send_telegram

    pre = preflight_mod.run()
    tg = send_telegram("🔧 TradeHub self-test — if you can read this, alerting works.") if notify else None
    return {"preflight": pre.as_dict(),
            "telegram": {"attempted": notify, "delivered": tg},
            "ok": pre.can_enter and (tg is not False)}


@router.get("/incident-log")
def incident_log(days: int = Query(30, ge=1, le=365)) -> dict:
    """A compact operations digest: failures by kind and day, for the weekly review."""
    from app.db import get_client

    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=days)).isoformat()
    rows = (get_client().table("compliance_events").select("event_time,kind,reason,acknowledged")
            .gte("event_time", since).order("event_time", desc=True).limit(2_000).execute().data or [])
    by_day: dict[str, dict[str, int]] = {}
    for r in rows:
        d = r["event_time"][:10]
        by_day.setdefault(d, {})
        by_day[d][r["kind"]] = by_day[d].get(r["kind"], 0) + 1
    recurring = [k for k, n in sorted(
        ((k, sum(day.get(k, 0) for day in by_day.values())) for k in {r["kind"] for r in rows}),
        key=lambda kv: -kv[1]) if n >= 3]
    return {"since": since, "total": len(rows), "by_day": by_day, "recurring_kinds": recurring,
            "unacknowledged": sum(1 for r in rows if not r.get("acknowledged"))}


@router.post("/test-alert")
def test_alert(severity: str = Query("warn", pattern="^(info|warn|error|critical)$")) -> dict:
    """Prove the alert path end to end — writes a `compliance_events` row and sends one Telegram."""
    failures_mod.reset_alert_state()
    return failures_mod.failure("failure", "test alert from POST /ops/test-alert — no action needed",
                                key="test_alert", severity=severity)
