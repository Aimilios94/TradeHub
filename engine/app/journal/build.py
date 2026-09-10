"""Fills → completed round trips → `journal` rows (plan §5.5, §12 rule 5).

FIFO matching, one journal row per closed position. A position opened with two buys and closed with one
sell produces one row when the last share is closed; a partial exit leaves the trip open until it is
flat. Anything still open at the end of the day is reported as such, never written as a closed trade.

Why FIFO and not lot-level tracking: the journal's job is per-trade coaching and strategy statistics,
where the round trip is the unit. Tax-lot accounting is a different problem and is not what §14 asks for.

Every number here is computed from actual fills. `r_multiple` needs the planned stop, so a trade with no
plan (a manual ticket, or a signal that never recorded one) gets `r_multiple = null` rather than a guess.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from app.broker import activities as broker_activities
from app.db import get_client
from app.marketdata import alpaca

log = logging.getLogger(__name__)

# Plan §14 time-of-day blocks, ET.
TIME_BLOCKS = (
    ("premarket", dt.time(0, 0), dt.time(9, 30)),
    ("09:30-10:30", dt.time(9, 30), dt.time(10, 30)),
    ("10:30-11:30", dt.time(10, 30), dt.time(11, 30)),
    ("11:30-12:30", dt.time(11, 30), dt.time(12, 30)),
    ("12:30-13:30", dt.time(12, 30), dt.time(13, 30)),
    ("13:30-14:30", dt.time(13, 30), dt.time(14, 30)),
    ("14:30-16:00", dt.time(14, 30), dt.time(16, 0)),
    ("afterhours", dt.time(16, 0), dt.time(23, 59, 59)),
)


def time_block(when: dt.datetime) -> str:
    t = when.astimezone(alpaca.ET).time()
    for name, start, end in TIME_BLOCKS:
        if start <= t < end:
            return name
    return "afterhours"


@dataclass(slots=True)
class Leg:
    ts: dt.datetime
    side: str
    qty: int
    price: float
    order_id: str | None = None

    @property
    def value(self) -> float:
        return self.qty * self.price


@dataclass(slots=True)
class RoundTrip:
    symbol: str
    direction: str
    entry_legs: list[Leg] = field(default_factory=list)
    exit_legs: list[Leg] = field(default_factory=list)

    @property
    def qty(self) -> int:
        return sum(leg.qty for leg in self.entry_legs)

    @property
    def entry_price(self) -> float:
        total = self.qty
        return round(sum(leg.value for leg in self.entry_legs) / total, 4) if total else 0.0

    @property
    def exit_price(self) -> float | None:
        total = sum(leg.qty for leg in self.exit_legs)
        return round(sum(leg.value for leg in self.exit_legs) / total, 4) if total else None

    @property
    def entry_time(self) -> dt.datetime:
        return min(leg.ts for leg in self.entry_legs)

    @property
    def exit_time(self) -> dt.datetime | None:
        return max(leg.ts for leg in self.exit_legs) if self.exit_legs else None

    @property
    def closed(self) -> bool:
        return bool(self.exit_legs) and sum(leg.qty for leg in self.exit_legs) == self.qty

    def pnl_gross(self) -> float | None:
        if not self.closed:
            return None
        out = sum(leg.value for leg in self.exit_legs)
        in_ = sum(leg.value for leg in self.entry_legs)
        return round(out - in_ if self.direction == "long" else in_ - out, 4)


def parse_fills(rows: list[dict]) -> list[Leg]:
    legs = []
    for a in rows:
        try:
            legs.append(Leg(ts=dt.datetime.fromisoformat(a["transaction_time"]),
                            side=a["side"], qty=int(float(a["qty"])), price=float(a["price"]),
                            order_id=a.get("order_id")))
        except (KeyError, ValueError, TypeError) as e:
            log.warning("unparseable fill activity skipped: %s (%s)", str(a)[:120], e)
    return sorted(legs, key=lambda leg: leg.ts)


def match_round_trips(legs: list[Leg]) -> tuple[list[RoundTrip], list[RoundTrip]]:
    """FIFO within one symbol. Returns (closed, still_open)."""
    trips: list[RoundTrip] = []
    current: RoundTrip | None = None
    for leg in legs:
        if current is None:
            current = RoundTrip(symbol="", direction="long" if leg.side == "buy" else "short")
            current.entry_legs.append(leg)
            continue
        opening_side = "buy" if current.direction == "long" else "sell"
        if leg.side == opening_side:
            if current.closed:                       # a new trip starts after the previous one flattened
                trips.append(current)
                current = RoundTrip(symbol="", direction="long" if leg.side == "buy" else "short")
            current.entry_legs.append(leg)
        else:
            open_qty = current.qty - sum(x.qty for x in current.exit_legs)
            take = min(leg.qty, open_qty)
            if take > 0:
                current.exit_legs.append(Leg(leg.ts, leg.side, take, leg.price, leg.order_id))
            remainder = leg.qty - take
            if remainder > 0:                        # flipped straight into the opposite direction
                trips.append(current)
                current = RoundTrip(symbol="", direction="long" if leg.side == "buy" else "short")
                current.entry_legs.append(Leg(leg.ts, leg.side, remainder, leg.price, leg.order_id))
    if current is not None:
        trips.append(current)
    return [t for t in trips if t.closed], [t for t in trips if not t.closed]


def round_trips_for_day(day: dt.date) -> tuple[dict[str, list[RoundTrip]], dict[str, list[RoundTrip]]]:
    """All of the day's fills, grouped by symbol and matched. (closed_by_symbol, open_by_symbol)."""
    by_symbol: dict[str, list[Leg]] = {}
    for a in broker_activities.fills(day):
        by_symbol.setdefault(a["symbol"], []).append(a)
    closed_out: dict[str, list[RoundTrip]] = {}
    open_out: dict[str, list[RoundTrip]] = {}
    for symbol, raw in by_symbol.items():
        closed, still_open = match_round_trips(parse_fills(raw))
        for t in closed + still_open:
            t.symbol = symbol
        if closed:
            closed_out[symbol] = closed
        if still_open:
            open_out[symbol] = still_open
    return closed_out, open_out


# ---------- linking a trip back to its plan ----------

def plan_for(symbol: str, day: dt.date) -> dict | None:
    try:
        rows = (get_client().table("signals").select("*")
                .eq("symbol", symbol).eq("signal_date", day.isoformat())
                .in_("status", ["approved", "executed"]).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("plan lookup failed for %s: %s", symbol, e)
        return None
    return rows[0] if rows else None


def orders_for(symbol: str, day: dt.date) -> list[dict]:
    try:
        return (get_client().table("orders").select("*")
                .eq("symbol", symbol).gte("created_at", f"{day.isoformat()}T00:00:00")
                .execute().data or [])
    except Exception:  # noqa: BLE001
        return []


def r_multiple(trip: RoundTrip, planned_stop: float | None, pnl_net: float) -> float | None:
    """Result in units of the risk actually planned. No plan means no R — never an invented one."""
    if planned_stop is None or not trip.qty:
        return None
    per_share = abs(trip.entry_price - float(planned_stop))
    if per_share <= 0:
        return None
    return round(pnl_net / (per_share * trip.qty), 3)


def spread_cost(trip: RoundTrip, orders: list[dict]) -> float:
    """Realized spread cost: fill vs the quote midpoint recorded when the order was submitted (§11 L2)."""
    mids = {o["alpaca_order_id"]: o.get("quote_mid_at_submit") for o in orders
            if o.get("quote_mid_at_submit") is not None}
    if not mids:
        return 0.0
    cost = 0.0
    for leg in trip.entry_legs + trip.exit_legs:
        mid = mids.get(leg.order_id)
        if mid is None:
            continue
        mid = float(mid)
        # paying above the mid to buy, or selling below it, is the cost either way
        slip = (leg.price - mid) if leg.side == "buy" else (mid - leg.price)
        cost += slip * leg.qty
    return round(max(cost, 0.0), 4)


def build_row(trip: RoundTrip, day: dt.date, *, plan: dict | None, orders: list[dict],
              fees: float, trade_number: int) -> dict:
    gross = trip.pnl_gross() or 0.0
    spread = spread_cost(trip, orders)
    net = round(gross - fees - spread, 4)
    planned_stop = plan.get("stop") if plan else None
    ev = (plan or {}).get("evidence") or {}
    catalyst = ((ev.get("catalysts") or [{}])[0] or {}).get("catalyst_type") if ev.get("catalysts") else None

    return {
        "strategy_id": (plan or {}).get("strategy_id"),
        "signal_id": (plan or {}).get("id"),
        "source": "signal" if plan else "manual",
        "symbol": trip.symbol, "direction": trip.direction,
        "entry_time": trip.entry_time.isoformat(), "entry_price": trip.entry_price,
        "exit_time": trip.exit_time.isoformat() if trip.exit_time else None,
        "exit_price": trip.exit_price, "qty": trip.qty,
        "pnl_gross": gross, "fees": fees, "spread_cost": spread, "pnl_net": net,
        "r_multiple": r_multiple(trip, planned_stop, net),
        "catalyst": catalyst,
        "setup_type": ev.get("entry_basis"),
        "market_regime": None,        # filled by the analytics regime tagger
        "time_block": time_block(trip.entry_time),
        "planned": {"entry": (plan or {}).get("entry_price"), "stop": planned_stop,
                    "target": (plan or {}).get("target"), "size": (plan or {}).get("size"),
                    "entry_type": (plan or {}).get("entry_type"),
                    "in_play_sentence": ev.get("in_play_sentence")} if plan else None,
        "actual": {
            "entry_legs": [{"ts": leg.ts.isoformat(), "qty": leg.qty, "price": leg.price} for leg in trip.entry_legs],
            "exit_legs": [{"ts": leg.ts.isoformat(), "qty": leg.qty, "price": leg.price} for leg in trip.exit_legs],
            "deviations": deviations(trip, plan),
        },
        "trade_number_of_day": trade_number,
    }


def deviations(trip: RoundTrip, plan: dict | None) -> list[str]:
    """Plan vs reality, stated plainly — this is what the autopsy is asked to judge."""
    if not plan:
        return ["no plan on file for this trade (manual or unlinked)"]
    out = []
    planned_size = plan.get("size")
    if planned_size and trip.qty != int(planned_size):
        out.append(f"size {trip.qty} vs planned {int(planned_size)}")
    planned_entry = plan.get("entry_price")
    if planned_entry:
        drift = trip.entry_price - float(planned_entry)
        if abs(drift) > abs(float(planned_entry)) * 0.005:
            out.append(f"entry {trip.entry_price} vs planned {planned_entry} ({drift:+.4f})")
    stop, target, exit_px = plan.get("stop"), plan.get("target"), trip.exit_price
    if exit_px is not None and stop is not None and target is not None:
        long = trip.direction == "long"
        hit_target = exit_px >= float(target) if long else exit_px <= float(target)
        hit_stop = exit_px <= float(stop) if long else exit_px >= float(stop)
        if not hit_target and not hit_stop:
            out.append(f"exited at {exit_px}, between the {stop} stop and the {target} target "
                       f"(discretionary exit)")
    if len(trip.exit_legs) > 1:
        out.append(f"exited in {len(trip.exit_legs)} parts")
    return out
