"""One place every failure is reported from (plan §33: fail safe, never fake).

Two problems this solves that scattered `try/except: log.warning` did not:

1. **A failure nobody sees did not get handled.** Every call here writes a `compliance_events` row, so
   the Guardian briefing and the weekly review can surface a problem that has been recurring quietly
   for a fortnight. Phase 8's acceptance criterion "zero unhandled execution errors" is only
   measurable because failures are rows rather than log lines.
2. **An alert that repeats 400 times is an alert nobody reads.** A broken feed fails on every symbol in
   the loop. The Telegram side is deduplicated by `(kind, key)` for `alert_cooldown_minutes`, while the
   `compliance_events` row is written EVERY time — the count is evidence, the notification is a nudge.
   Suppressing the row instead of the ping would be hiding the failure, which is the opposite of §33.

Nothing here raises. A failure in the failure reporter must not replace the failure being reported.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_COOLDOWN_MINUTES = 30

# (kind, key) -> last Telegram send. Process-local on purpose: the container restarting is itself a
# reason to hear about a failure again.
_last_alert: dict[tuple[str, str], dt.datetime] = {}
_lock = threading.Lock()

SEVERITY_ICON = {"info": "ℹ️", "warn": "⚠️", "error": "⛔", "critical": "🚨"}


@dataclass(slots=True)
class Failure:
    kind: str                 # compliance_events.kind — failure | pipeline_incomplete | data_stale | broker_down
    reason: str
    key: str = ""             # dedupe key for the alert; defaults to the kind
    severity: str = "error"   # info | warn | error | critical
    symbol: str | None = None
    signal_id: str | None = None
    order_id: str | None = None
    detail: dict | None = None
    notify: bool = True

    def as_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason, "severity": self.severity, "symbol": self.symbol,
                "detail": self.detail or {}}


def _cooldown_minutes() -> int:
    try:
        from app.db import get_client

        row = get_client().table("app_settings").select("value").eq("key", "failure_policy").single().execute()
        return int((row.data.get("value") or {}).get("alert_cooldown_minutes", DEFAULT_COOLDOWN_MINUTES))
    except Exception:  # noqa: BLE001
        return DEFAULT_COOLDOWN_MINUTES


def should_alert(kind: str, key: str, *, now: dt.datetime | None = None, cooldown_minutes: int | None = None) -> bool:
    """True the first time, then at most once per cooldown for the same (kind, key)."""
    now = now or dt.datetime.now(dt.UTC)
    minutes = cooldown_minutes if cooldown_minutes is not None else _cooldown_minutes()
    with _lock:
        last = _last_alert.get((kind, key))
        if last is not None and (now - last).total_seconds() < minutes * 60:
            return False
        _last_alert[(kind, key)] = now
        return True


def reset_alert_state() -> None:
    with _lock:
        _last_alert.clear()


def record(f: Failure, *, now: dt.datetime | None = None, cooldown_minutes: int | None = None) -> dict:
    """Log it, store it, and (at most once per cooldown) say it out loud. Never raises."""
    key = f.key or f.symbol or f.kind
    logger = log.error if f.severity in ("error", "critical") else log.warning
    logger("[%s] %s%s", f.kind, f.reason[:400], f" ({f.symbol})" if f.symbol else "")

    stored = False
    try:
        from app.guardian import gate as guardian

        guardian.log_event(kind=f.kind, verdict="red" if f.severity in ("error", "critical") else None,
                           reason=f.reason, symbol=f.symbol, signal_id=f.signal_id, order_id=f.order_id,
                           detail=(f.detail or {}) | {"severity": f.severity, "dedupe_key": key})
        stored = True
    except Exception as e:  # noqa: BLE001
        log.error("compliance_events write failed while recording '%s': %s", f.kind, e)

    alerted = False
    if f.notify and should_alert(f.kind, key, now=now, cooldown_minutes=cooldown_minutes):
        try:
            from app.notify import send_telegram

            icon = SEVERITY_ICON.get(f.severity, "⚠️")
            sym = f" {f.symbol}" if f.symbol else ""
            alerted = send_telegram(f"{icon} {f.kind.upper()}{sym}\n{f.reason[:1200]}")
        except Exception as e:  # noqa: BLE001
            log.error("telegram alert failed while recording '%s': %s", f.kind, e)

    return {"recorded": stored, "alerted": alerted, "kind": f.kind, "key": key, "reason": f.reason}


def failure(kind: str, reason: str, *, cooldown_minutes: int | None = None, **kw) -> dict:
    """Shorthand: `failures.failure("data_stale", "SPY quote 412s old", symbol="SPY")`."""
    return record(Failure(kind=kind, reason=reason, **kw), cooldown_minutes=cooldown_minutes)


def count_since(kinds: tuple[str, ...], since: dt.datetime) -> int:
    """How many failures of these kinds were recorded since `since`. 0 on a DB error is NOT reported
    as zero by callers — `acceptance` marks the criterion unknown instead."""
    from app.db import get_client

    rows = (get_client().table("compliance_events").select("id")
            .in_("kind", list(kinds)).gte("event_time", since.isoformat()).limit(5000).execute().data or [])
    return len(rows)
