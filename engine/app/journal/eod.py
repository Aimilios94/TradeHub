"""The 16:30 ET end-of-day job (plan Phase 6): closed positions → `journal` → `guardian_ledger`.

Idempotent by design. It reads the day's fills from the broker, matches them into round trips, and skips
any trip already journalled (same symbol + entry time), so running it twice — or re-running it after a
failure — cannot double-count a trade or a fee. Positions still open are reported and left alone.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from app.broker import activities as broker_activities
from app.db import get_client
from app.journal import build as jbuild
from app.journal import ledger as jledger
from app.marketdata import alpaca
from app.notify import send_telegram

log = logging.getLogger(__name__)


@dataclass(slots=True)
class EodResult:
    trade_date: str
    journal_written: int = 0
    ledger_written: int = 0
    already_present: int = 0
    still_open: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    fees_estimated: bool = False
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {"trade_date": self.trade_date, "journal_written": self.journal_written,
                "ledger_written": self.ledger_written, "already_present": self.already_present,
                "still_open": self.still_open, "trades": self.trades,
                "fees_estimated": self.fees_estimated, "errors": self.errors,
                "elapsed_s": self.elapsed_s}

    def summary(self) -> str:
        lines = [f"📒 EOD journal {self.trade_date} — {self.journal_written} trade(s) closed"]
        for t in self.trades:
            r = f"{t['r_multiple']:+.2f}R" if t.get("r_multiple") is not None else "—R"
            lines.append(f"   {t['symbol']} {t['direction']} {t['qty']} · "
                         f"{t['pnl_net']:+.2f} net ({r}) · fees {t['fees']:.2f} "
                         f"spread {t['spread_cost']:.2f}")
        if self.fees_estimated:
            lines.append("   ℹ️ fees are ESTIMATED — a paper account is not charged SEC/TAF")
        for o in self.still_open:
            lines.append(f"   ⏳ {o['symbol']} still open ({o['qty']})")
        if self.already_present:
            lines.append(f"   ({self.already_present} already journalled, skipped)")
        if self.errors:
            lines.append(f"⚠️ {len(self.errors)} error(s): {self.errors[0][:120]}")
        if not self.trades and not self.still_open:
            lines.append("   no fills today")
        return "\n".join(lines)


def existing_keys(day: dt.date) -> set[tuple[str, str]]:
    """(symbol, entry_time) already in the journal for this day — the idempotency key."""
    try:
        rows = (get_client().table("journal").select("symbol,entry_time")
                .gte("entry_time", f"{day.isoformat()}T00:00:00")
                .lte("entry_time", f"{day.isoformat()}T23:59:59").execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("could not read existing journal rows: %s", e)
        return set()
    return {(r["symbol"], str(r["entry_time"])[:19]) for r in rows}


def run_eod(*, date: dt.date | None = None, notify: bool = True, dry_run: bool = False) -> EodResult:
    t0 = time.time()
    day = date or alpaca.now_et().date()
    res = EodResult(trade_date=day.isoformat())

    try:
        closed_by_symbol, open_by_symbol = jbuild.round_trips_for_day(day)
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"fills: {type(e).__name__}: {str(e)[:160]}")
        res.elapsed_s = round(time.time() - t0, 1)
        if notify:
            send_telegram(res.summary())
        return res

    for symbol, trips in open_by_symbol.items():
        for t in trips:
            res.still_open.append({"symbol": symbol, "qty": t.qty - sum(x.qty for x in t.exit_legs),
                                   "direction": t.direction})

    real_fees, have_real = jledger.fees_for_day(day)
    model = broker_activities.fee_model()
    seen = existing_keys(day)

    rows: list[dict] = []
    fee_detail: dict[str, dict] = {}
    trade_number = 0
    all_trips = sorted(((s, t) for s, trips in closed_by_symbol.items() for t in trips),
                       key=lambda pair: pair[1].entry_time)
    for symbol, trip in all_trips:
        key = (symbol, trip.entry_time.isoformat()[:19])
        if key in seen:
            res.already_present += 1
            continue
        trade_number += 1
        fees, detail = jledger.fees_for_trip(trip, real_fees, have_real, model)
        fee_detail[symbol] = detail
        if detail.get("estimated"):
            res.fees_estimated = True
        rows.append(jbuild.build_row(trip, day, plan=jbuild.plan_for(symbol, day),
                                     orders=jbuild.orders_for(symbol, day), fees=fees,
                                     trade_number=trade_number))

    if dry_run:
        res.journal_written = len(rows)
        res.trades = [{k: r[k] for k in ("symbol", "direction", "qty", "pnl_net", "r_multiple",
                                         "fees", "spread_cost")} for r in rows]
        res.elapsed_s = round(time.time() - t0, 1)
        return res

    written: list[dict] = []
    if rows:
        try:
            out = get_client().table("journal").insert(rows).execute()
            written = out.data or []
            res.journal_written = len(written)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"journal write: {type(e).__name__}: {str(e)[:160]}")

    if written:
        try:
            res.ledger_written = jledger.write_rows(day=day, journal_rows=written, fee_detail=fee_detail)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"ledger write: {type(e).__name__}: {str(e)[:160]}")

    res.trades = [{k: r.get(k) for k in ("symbol", "direction", "qty", "pnl_net", "r_multiple",
                                         "fees", "spread_cost")} for r in (written or rows)]
    res.elapsed_s = round(time.time() - t0, 1)
    if notify:
        send_telegram(res.summary())
    return res
