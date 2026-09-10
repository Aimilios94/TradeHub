"""alpaca-py wrapper for Alpaca Market Data v2 (plan §21 touchpoint 2).

Feeds (verified 2026-09-10 on the free plan):
- IEX : real-time quotes + bars, but pre-market 1-min bars are almost empty (IEX is a small slice
        of the tape; most small caps print zero pre-market bars).
- SIP : full consolidated tape, historical only. The free plan returns SIP bars up to ~15 min ago,
        INCLUDING today's pre-market. Latest-quote / snapshot on SIP is refused
        ("subscription does not permit querying recent SIP data").
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.config import get_settings

ET = ZoneInfo("America/New_York")
FEEDS = ("iex", "sip")
BAR_LIMIT = 10_000  # Alpaca page size (informational — get_bars never caps totals)
TIMEFRAMES = ("1Min", "5Min", "15Min", "1Hour", "1Day")


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    ts: dt.datetime  # tz-aware (UTC from Alpaca)
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None
    trade_count: int | None
    feed: str

    def ts_et(self) -> dt.datetime:
        return self.ts.astimezone(ET)

    def as_dict(self) -> dict:
        return {"symbol": self.symbol, "ts": self.ts.isoformat(), "o": self.open, "h": self.high, "l": self.low,
                "c": self.close, "v": self.volume, "vwap": self.vwap, "n": self.trade_count, "feed": self.feed}


def now_et() -> dt.datetime:
    return dt.datetime.now(ET)


def et_datetime(day: dt.date, hh: int, mm: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), ET)


@lru_cache
def _client():
    from alpaca.data.historical import StockHistoricalDataClient

    s = get_settings()
    if not s.alpaca_configured:
        raise RuntimeError("Alpaca is not configured: set ALPACA_PAPER_KEY_ID / ALPACA_PAPER_SECRET_KEY in .env")
    return StockHistoricalDataClient(s.alpaca_paper_key_id, s.alpaca_paper_secret_key)


def _feed_enum(feed: str):
    from alpaca.data.enums import DataFeed

    if feed not in FEEDS:
        raise ValueError(f"feed must be one of {FEEDS}, got {feed!r}")
    return DataFeed.IEX if feed == "iex" else DataFeed.SIP


def _timeframe(tf: str):
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    table = {"1Min": (1, TimeFrameUnit.Minute), "5Min": (5, TimeFrameUnit.Minute), "15Min": (15, TimeFrameUnit.Minute),
             "1Hour": (1, TimeFrameUnit.Hour), "1Day": (1, TimeFrameUnit.Day)}
    if tf not in table:
        raise ValueError(f"timeframe must be one of {TIMEFRAMES}, got {tf!r}")
    n, unit = table[tf]
    return TimeFrame(n, unit)


def get_bars(symbols: list[str], start: dt.datetime, end: dt.datetime, *, feed: str = "iex",
             timeframe: str = "1Min") -> list[Bar]:
    """Bars for one or many symbols in [start, end]. Symbols with no data are simply absent."""
    from alpaca.data.requests import StockBarsRequest

    if not symbols:
        return []
    # limit=None → the SDK follows next_page_token until every bar is returned. Passing a `limit` caps the
    # TOTAL rows (not the page size) and silently drops later symbols in multi-symbol requests.
    req = StockBarsRequest(symbol_or_symbols=list(symbols), timeframe=_timeframe(timeframe), start=start, end=end,
                           feed=_feed_enum(feed), limit=None)
    res = _client().get_stock_bars(req)
    out: list[Bar] = []
    for sym, bars in res.data.items():
        for b in bars:
            out.append(Bar(symbol=sym, ts=b.timestamp, open=float(b.open), high=float(b.high), low=float(b.low),
                           close=float(b.close), volume=int(b.volume),
                           vwap=float(b.vwap) if b.vwap is not None else None,
                           trade_count=int(b.trade_count) if b.trade_count is not None else None, feed=feed))
    out.sort(key=lambda x: (x.symbol, x.ts))
    return out


def get_latest_quotes(symbols: list[str], *, feed: str = "iex") -> dict[str, dict]:
    from alpaca.data.requests import StockLatestQuoteRequest

    if not symbols:
        return {}
    res = _client().get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=list(symbols), feed=_feed_enum(feed)))
    out: dict[str, dict] = {}
    for sym, q in res.items():
        bid, ask = float(q.bid_price), float(q.ask_price)
        out[sym] = {"bid": bid, "ask": ask, "bid_size": int(q.bid_size), "ask_size": int(q.ask_size),
                    "ts": q.timestamp.isoformat(), "feed": feed,
                    "spread_pct": round((ask - bid) / ask * 100, 4) if ask else None}
    return out


def get_snapshots(symbols: list[str], *, feed: str = "iex") -> dict[str, dict]:
    from alpaca.data.requests import StockSnapshotRequest

    if not symbols:
        return {}
    res = _client().get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=list(symbols), feed=_feed_enum(feed)))
    out: dict[str, dict] = {}
    for sym, snap in res.items():
        q, t, db, pdb = snap.latest_quote, snap.latest_trade, snap.daily_bar, snap.previous_daily_bar
        out[sym] = {
            "feed": feed,
            "quote": {"bid": float(q.bid_price), "ask": float(q.ask_price), "ts": q.timestamp.isoformat()} if q else None,
            "trade": {"price": float(t.price), "size": int(t.size), "ts": t.timestamp.isoformat()} if t else None,
            "daily": {"o": float(db.open), "h": float(db.high), "l": float(db.low), "c": float(db.close),
                      "v": int(db.volume)} if db else None,
            "prev_daily": {"o": float(pdb.open), "h": float(pdb.high), "l": float(pdb.low), "c": float(pdb.close),
                           "v": int(pdb.volume)} if pdb else None,
        }
    return out
