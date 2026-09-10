"""Guardian Layer 2 — the always-on ledger (plan §11).

One row per cost or gain, so that "how much did this actually cost me" is answerable from data rather
than estimated later: SEC/TAF fees, realized spread cost, realized P&L, and a running tax provision.

The tax provision is 28% of net realized GAINS — the Portuguese flat CGT rate — and is **display only**.
It is not advice and not a filing: the spec's own words are "confirm with an accountant", and that
disclaimer travels with every row rather than living in a footnote somewhere.

Fees are real when the broker reports them and clearly `estimated` when it does not (a paper account
never charges SEC/TAF). Marking the difference is the point; quietly presenting an estimate as a charge
is the thing plan §33 forbids.
"""

from __future__ import annotations

import datetime as dt
import logging

from app.broker import activities as broker_activities
from app.db import get_client
from app.marketdata import repo as md_repo

log = logging.getLogger(__name__)

DEFAULT_TAX_RATE = 0.28
TAX_NOTE = "PT flat CGT estimate, display only — confirm with an accountant"


def tax_rate() -> float:
    defaults = md_repo.fetch_setting("guardian_defaults", {}) or {}
    return float(defaults.get("tax_provision_rate", DEFAULT_TAX_RATE))


def running_totals(before: dt.date | None = None) -> tuple[float, float]:
    """(running realized P&L, running tax provision) from the ledger so far."""
    try:
        q = get_client().table("guardian_ledger").select("amount,kind,event_time").eq("kind", "realized_pnl")
        if before:
            q = q.lt("event_time", f"{before.isoformat()}T00:00:00")
        rows = q.execute().data or []
    except Exception as e:  # noqa: BLE001
        log.warning("ledger totals unavailable: %s", e)
        return 0.0, 0.0
    pnl = round(sum(float(r["amount"]) for r in rows), 4)
    return pnl, round(max(pnl, 0.0) * tax_rate(), 4)


def fees_for_day(day: dt.date) -> tuple[dict[str, float], bool]:
    """Real reported fees per symbol. Returns ({}, False) on a paper account, which has none."""
    rows = broker_activities.fee_activities(day)
    if not rows:
        return {}, False
    out: dict[str, float] = {}
    for a in rows:
        symbol = a.get("symbol") or "_account"
        try:
            out[symbol] = round(out.get(symbol, 0.0) + abs(float(a.get("net_amount", 0))), 4)
        except (TypeError, ValueError):
            continue
    return out, True


def fees_for_trip(trip, real_fees: dict[str, float], real: bool, model: dict) -> tuple[float, dict]:
    """Real fee if the broker reported one for this symbol, otherwise a labelled estimate."""
    if real and trip.symbol in real_fees:
        return real_fees[trip.symbol], {"estimated": False, "basis": "reported by the broker"}
    total, detail = 0.0, []
    for leg in trip.exit_legs:
        est = broker_activities.estimate_fees(side=leg.side, qty=leg.qty, price=leg.price, model=model)
        total += est["total"]
        detail.append(est)
    return round(total, 4), {"estimated": True, "legs": detail,
                             "basis": "paper account reports no SEC/TAF; computed from published rates"}


def write_rows(*, day: dt.date, journal_rows: list[dict], fee_detail: dict[str, dict]) -> int:
    """One ledger row per cost/gain component of each closed trade. Idempotent by (journal_id, kind)."""
    client = get_client()
    pnl_running, _ = running_totals(before=day)
    rate = tax_rate()
    out: list[dict] = []

    for row in journal_rows:
        jid = row.get("id")
        stamp = row.get("exit_time") or row.get("entry_time")
        detail = fee_detail.get(row["symbol"], {})
        base = {"event_time": stamp, "symbol": row["symbol"], "journal_id": jid, "currency": "USD"}

        if row.get("fees"):
            out.append(base | {"kind": "fee_sec" if not detail.get("estimated") else "fee_taf",
                               "amount": -abs(float(row["fees"])),
                               "detail": {"note": "SEC + TAF combined", **detail}})
        if row.get("spread_cost"):
            out.append(base | {"kind": "spread_cost", "amount": -abs(float(row["spread_cost"])),
                               "detail": {"note": "fill vs quote midpoint at submit"}})

        net = float(row.get("pnl_net") or 0.0)
        pnl_running = round(pnl_running + net, 4)
        provision = round(max(pnl_running, 0.0) * rate, 4)
        out.append(base | {"kind": "realized_pnl", "amount": net,
                           "running_realized_pnl": pnl_running, "running_tax_provision": provision,
                           "detail": {"gross": row.get("pnl_gross"), "r_multiple": row.get("r_multiple")}})
        out.append(base | {"kind": "tax_provision", "amount": provision,
                           "running_realized_pnl": pnl_running, "running_tax_provision": provision,
                           "detail": {"rate": rate, "note": TAX_NOTE}})

    if not out:
        return 0
    try:
        for i in range(0, len(out), 200):
            client.table("guardian_ledger").insert(out[i:i + 200]).execute()
    except Exception as e:  # noqa: BLE001
        log.error("guardian_ledger write failed: %s", e)
        return 0
    return len(out)


def summary(days: int = 30) -> dict:
    """What the ledger says about the last N days — the numbers Layer 3 will narrate in Phase 6.5."""
    since = (dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=days)).isoformat()
    try:
        rows = (get_client().table("guardian_ledger").select("*")
                .gte("event_time", f"{since}T00:00:00").order("event_time", desc=False)
                .execute().data or [])
    except Exception as e:  # noqa: BLE001
        return {"error": f"ledger unavailable: {str(e)[:120]}"}

    def total(kind: str) -> float:
        return round(sum(float(r["amount"]) for r in rows if r["kind"] == kind), 4)

    pnl_rows = [r for r in rows if r["kind"] == "realized_pnl"]
    latest = pnl_rows[-1] if pnl_rows else {}
    fees = round(abs(total("fee_sec")) + abs(total("fee_taf")), 4)
    estimated = any((r.get("detail") or {}).get("estimated") for r in rows if r["kind"].startswith("fee"))
    return {
        "days": days, "trades": len(pnl_rows),
        "realized_pnl": total("realized_pnl"),
        "fees": fees, "fees_are_estimated": estimated,
        "spread_cost": round(abs(total("spread_cost")), 4),
        "running_realized_pnl": float(latest.get("running_realized_pnl") or 0.0),
        "tax_provision": float(latest.get("running_tax_provision") or 0.0),
        "tax_rate": tax_rate(), "tax_note": TAX_NOTE,
    }
