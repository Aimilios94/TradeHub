"""Sub-second synthetic stop, driven by the alpaca-py streaming client (plan §12 rule 3, §21).

Loop:
  1. every REFRESH_SECONDS, reload the watch list — open positions joined to today's plan for the stop
     level, so an edited stop is picked up without a restart (§28)
  2. subscribe to trades (and quotes as a fallback) for those symbols
  3. on a print through the stop, fire ONE closing limit order and stop watching that symbol

Deliberate choices:

  * a LIMIT, never a market order. A market exit on a low-float pre-market name is how a stop becomes a
    20% slip. The limit is placed through the stop by `SLIP_THROUGH_PCT` so it is marketable but bounded.
  * fire once per symbol, tracked in `_fired`. A duplicate exit would flip the position short.
  * a breach with no position, or a position already covered by a resting order, is skipped rather than
    doubled.
  * every fire and every failure alerts Telegram and writes a `compliance_events` row. A stop that could
    not be honoured must be loud (§33).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

from app.broker import account as broker_account
from app.broker import orders as broker_orders
from app.config import get_settings
from app.db import get_client
from app.guardian import gate as guardian
from app.marketdata import alpaca
from app.notify import send_telegram

log = logging.getLogger(__name__)

REFRESH_SECONDS = int(os.getenv("MONITOR_REFRESH_SECONDS", "30"))
SLIP_THROUGH_PCT = float(os.getenv("MONITOR_SLIP_THROUGH_PCT", "0.5"))   # how far through the stop to price
HEARTBEAT_MINUTES = int(os.getenv("MONITOR_HEARTBEAT_MINUTES", "0"))     # 0 = no heartbeat message


class Watch:
    __slots__ = ("long", "qty", "signal_id", "stop", "symbol")

    def __init__(self, symbol: str, qty: int, long: bool, stop: float, signal_id: str | None):
        self.symbol, self.qty, self.long, self.stop, self.signal_id = symbol, qty, long, stop, signal_id

    def breached(self, price: float) -> bool:
        return price <= self.stop if self.long else price >= self.stop

    def exit_limit(self) -> float:
        """Priced through the stop so it fills, but bounded — never a market order."""
        factor = (1 - SLIP_THROUGH_PCT / 100.0) if self.long else (1 + SLIP_THROUGH_PCT / 100.0)
        return round(self.stop * factor, 2)

    def __repr__(self) -> str:
        return f"Watch({self.symbol} {'long' if self.long else 'short'} {self.qty} stop {self.stop})"


def build_watchlist(day: dt.date | None = None) -> dict[str, Watch]:
    """Open positions that have a stop to enforce today. Positions without one are reported, not guessed."""
    day = day or alpaca.now_et().date()
    acct = broker_account.snapshot()
    if not acct.ok:
        log.error("broker unreachable while building the watchlist: %s", acct.error)
        guardian.log_event(kind="failure", verdict="red",
                           reason=f"synthetic stop monitor cannot read positions: {acct.error}")
        return {}
    if not acct.positions:
        return {}

    symbols = [p["symbol"] for p in acct.positions]
    try:
        signals = (get_client().table("signals").select("id,symbol,stop,status,signal_date")
                   .eq("signal_date", day.isoformat()).in_("symbol", symbols)
                   .in_("status", ["approved", "executed"]).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.error("could not load plans for the watchlist: %s", e)
        signals = []
    stop_by = {s["symbol"]: s for s in signals if s.get("stop") is not None}

    out: dict[str, Watch] = {}
    for p in acct.positions:
        plan = stop_by.get(p["symbol"])
        if not plan:
            log.warning("%s is open with no stop on today's plan — not watched", p["symbol"])
            continue
        qty = abs(int(float(p["qty"])))
        out[p["symbol"]] = Watch(p["symbol"], qty, float(p["qty"]) > 0, float(plan["stop"]), plan["id"])
    return out


class SyntheticStopMonitor:
    def __init__(self) -> None:
        self._watch: dict[str, Watch] = {}
        self._fired: set[str] = set()
        self._stream = None
        self._subscribed: set[str] = set()

    # ---------- firing ----------

    def fire(self, w: Watch, price: float, source: str) -> None:
        """Close the position with one bounded limit order. Fires at most once per symbol."""
        if w.symbol in self._fired:
            return
        self._fired.add(w.symbol)
        limit = w.exit_limit()
        side = "sell" if w.long else "buy"
        log.warning("STOP BREACH %s at %s (%s) — exiting %s %s @ limit %s",
                    w.symbol, price, source, side, w.qty, limit)
        try:
            submitted = broker_orders.close_position_limit(w.symbol, w.qty, limit, side)
        except Exception as e:  # noqa: BLE001
            msg = (f"SYNTHETIC STOP FAILED for {w.symbol}: breach at {price} (stop {w.stop}) but the exit "
                   f"order was refused: {type(e).__name__}: {str(e)[:200]}")
            log.error(msg)
            guardian.log_event(kind="failure", verdict="red", symbol=w.symbol, signal_id=w.signal_id,
                               reason=msg, detail={"price": price, "stop": w.stop})
            send_telegram(f"🚨 {msg}\nThe position is STILL OPEN and unprotected — act manually.")
            return

        o = submitted["order"]
        guardian.log_event(kind="gate_warn", verdict="yellow", symbol=w.symbol, signal_id=w.signal_id,
                           reason=f"synthetic stop fired at {price} (stop {w.stop}); exit limit {limit}",
                           detail={"alpaca_order_id": o["id"], "source": source})
        try:
            get_client().table("orders").insert([{
                "signal_id": w.signal_id, "source": "exit_plugin", "alpaca_order_id": o["id"],
                "client_order_id": o["client_order_id"], "symbol": w.symbol, "side": side,
                "order_type": "limit", "time_in_force": o["time_in_force"], "qty": w.qty,
                "limit_price": limit, "extended_hours": o["extended_hours"], "order_class": "simple",
                "status": o["status"],
                "status_timeline": [{"at": dt.datetime.now(dt.UTC).isoformat(), "status": o["status"],
                                     "note": f"synthetic stop, breach {price} vs stop {w.stop}"}],
                "submitted_at": o["submitted_at"], "raw_request": submitted["request"], "raw_response": o,
            }]).execute()
        except Exception as e:  # noqa: BLE001
            log.error("synthetic stop order not logged for %s: %s", w.symbol, e)
        send_telegram(f"🛑 SYNTHETIC STOP {w.symbol}\nbreach {price} (stop {w.stop}) via {source}\n"
                      f"{side.upper()} {w.qty} @ limit {limit} · status {o['status']}")

    def on_price(self, symbol: str, price: float, source: str) -> None:
        w = self._watch.get(symbol)
        if w and w.symbol not in self._fired and w.breached(price):
            self.fire(w, price, source)

    # ---------- stream plumbing ----------

    async def _handle_trade(self, trade) -> None:
        self.on_price(trade.symbol, float(trade.price), "trade")

    async def _handle_quote(self, quote) -> None:
        w = self._watch.get(quote.symbol)
        if not w:
            return
        # a long is stopped by the bid falling through; a short by the ask rising through
        price = float(quote.bid_price if w.long else quote.ask_price)
        if price > 0:
            self.on_price(quote.symbol, price, "quote")

    def _ensure_stream(self):
        if self._stream is not None:
            return self._stream
        from alpaca.data.live import StockDataStream

        s = get_settings()
        self._stream = StockDataStream(s.alpaca_paper_key_id, s.alpaca_paper_secret_key)
        return self._stream

    async def refresh(self) -> None:
        """Reload the watch list and subscribe to anything new."""
        try:
            fresh = await asyncio.to_thread(build_watchlist)
        except Exception as e:  # noqa: BLE001
            log.error("watchlist refresh failed: %s", e)
            return
        self._watch = fresh
        for symbol in set(self._watch) - self._fired:
            if symbol in self._subscribed:
                continue
            stream = self._ensure_stream()
            stream.subscribe_trades(self._handle_trade, symbol)
            stream.subscribe_quotes(self._handle_quote, symbol)
            self._subscribed.add(symbol)
            log.info("watching %s", self._watch[symbol])

    async def run(self) -> None:
        log.info("synthetic stop monitor starting (refresh %ss, slip-through %s%%)",
                 REFRESH_SECONDS, SLIP_THROUGH_PCT)
        broker_orders.assert_paper_only()          # same lockout as every other order path
        await self.refresh()
        if not self._watch:
            log.info("no positions with stops to watch yet")

        async def refresher():
            last_beat = dt.datetime.now(dt.UTC)
            while True:
                await asyncio.sleep(REFRESH_SECONDS)
                await self.refresh()
                if HEARTBEAT_MINUTES:
                    now = dt.datetime.now(dt.UTC)
                    if (now - last_beat).total_seconds() >= HEARTBEAT_MINUTES * 60:
                        last_beat = now
                        send_telegram(f"👁️ stop monitor alive — watching {len(self._watch)} position(s)")

        task = asyncio.create_task(refresher())
        try:
            while True:
                if self._subscribed:
                    stream = self._ensure_stream()
                    await asyncio.to_thread(stream.run)
                    log.warning("stream ended; reconnecting in 5s")
                await asyncio.sleep(5)
                await self.refresh()
        finally:
            task.cancel()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(SyntheticStopMonitor().run())
    except KeyboardInterrupt:
        log.info("monitor stopped")
    except Exception as e:
        log.exception("monitor crashed")
        send_telegram(f"🚨 SYNTHETIC STOP MONITOR CRASHED: {type(e).__name__}: {str(e)[:300]}\n"
                      "Open pre-market positions are unprotected until it restarts.")
        raise


if __name__ == "__main__":
    main()
