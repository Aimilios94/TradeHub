"""Yahoo Finance through yfinance: daily bars, the strip symbols and recent intraday bars.

Unofficial endpoints. Futures (=F) series are display-only and are never used in backtests.
"""

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import polars as pl

from app.ingest.base import DAILY_SCHEMA, INTRADAY_SCHEMA, SourceAdapter, SourceError, TickerRef

SOURCE_KEY = "yahoo"  # key inside tickers.source_symbols

# interval -> (bar length, the longest lookback Yahoo serves for it, in days)
INTRADAY_INTERVALS: dict[str, tuple[timedelta, int]] = {
    "1m": (timedelta(minutes=1), 7),
    "5m": (timedelta(minutes=5), 59),
    "1h": (timedelta(hours=1), 729),
}
_REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _yfinance_history(symbol: str, **kwargs):
    import yfinance as yf

    return yf.Ticker(symbol).history(auto_adjust=False, actions=True, raise_errors=True, **kwargs)


def _floats(frame, column: str) -> list[float | None]:
    if column not in frame.columns:
        return [None] * len(frame)
    return [None if value is None or math.isnan(value) else float(value) for value in frame[column]]


def _volumes(frame) -> list[int | None]:
    return [None if value is None else int(value) for value in _floats(frame, "Volume")]


class YahooFinanceAdapter(SourceAdapter):
    source_id = "yahoo_finance"

    def __init__(
        self,
        history: Callable = _yfinance_history,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._history = history
        self._now = now

    def _symbol(self, ticker: TickerRef) -> str:
        symbol = ticker.symbol_for(SOURCE_KEY)
        if not symbol:
            raise SourceError(f"{ticker.id} has no Yahoo symbol in tickers.source_symbols")
        return symbol

    def _fetch(self, symbol: str, **kwargs):
        try:
            frame = self._history(symbol, **kwargs)
        except Exception as exc:
            raise SourceError(f"Yahoo {symbol}: {type(exc).__name__}: {exc}") from exc
        if frame is None or len(frame) == 0:
            raise SourceError(f"Yahoo {symbol}: no rows returned")
        missing = [column for column in _REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise SourceError(f"Yahoo {symbol}: response is missing columns {missing}")
        return frame

    def check(self) -> str:
        frame = self._fetch("SPY", period="5d", interval="1d")
        return f"SPY daily: {len(frame)} rows, last {frame.index[-1].date()}"

    def fetch_daily_bars(self, ticker: TickerRef, start: date | None = None) -> pl.DataFrame:
        symbol = self._symbol(ticker)
        window = {"start": start.isoformat()} if start else {"period": "max"}
        frame = self._fetch(symbol, interval="1d", **window)
        bars = pl.DataFrame(
            {
                "ticker_id": [ticker.id] * len(frame),
                # The index is midnight in the exchange's own timezone: its date is the session.
                "date": [stamp.date() for stamp in frame.index],
                "open": _floats(frame, "Open"),
                "high": _floats(frame, "High"),
                "low": _floats(frame, "Low"),
                "close": _floats(frame, "Close"),
                "adj_close": _floats(frame, "Adj Close"),
                "volume": _volumes(frame),
                "dividends": _floats(frame, "Dividends"),
                "stock_splits": _floats(frame, "Stock Splits"),
                "source_id": [self.source_id] * len(frame),
            },
            schema=DAILY_SCHEMA,
        )
        # Yahoo sometimes sends a placeholder row with no prices. It is dropped, never filled.
        bars = bars.drop_nulls(["open", "high", "low", "close"])
        if bars.is_empty():
            raise SourceError(f"Yahoo {symbol}: every daily row was empty")
        return bars.unique(["ticker_id", "date"], keep="last").sort("date")

    def fetch_intraday_bars(
        self, ticker: TickerRef, interval: str, lookback_days: int
    ) -> pl.DataFrame:
        if interval not in INTRADAY_INTERVALS:
            raise SourceError(f"Yahoo: unsupported intraday interval '{interval}'")
        length, max_days = INTRADAY_INTERVALS[interval]
        symbol = self._symbol(ticker)
        days = max(1, min(lookback_days, max_days))
        frame = self._fetch(symbol, interval=interval, period=f"{days}d", prepost=False)
        stamps = [stamp.to_pydatetime().astimezone(UTC) for stamp in frame.index]
        bars = pl.DataFrame(
            {
                "ticker_id": [ticker.id] * len(frame),
                "interval": [interval] * len(frame),
                "ts": stamps,
                "open": _floats(frame, "Open"),
                "high": _floats(frame, "High"),
                "low": _floats(frame, "Low"),
                "close": _floats(frame, "Close"),
                "volume": _volumes(frame),
                "source_id": [self.source_id] * len(frame),
            },
            schema=INTRADAY_SCHEMA,
        )
        bars = bars.drop_nulls(["open", "high", "low", "close"])
        # A bar still forming at fetch time is not recorded; the next run picks it up complete.
        bars = bars.filter(pl.col("ts") + length <= self._now())
        return bars.unique(["ticker_id", "interval", "ts"], keep="last").sort("ts")
