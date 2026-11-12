"""The common interface every source adapter implements.

An adapter wraps exactly one external source. It turns that source's responses into the shared
record types below and raises SourceError, with the real reason, when anything goes wrong.
Adapters never write to the database and never invent or fill in data: an empty or malformed
answer is an error or an empty list, nothing else.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import ClassVar, Protocol, runtime_checkable

import httpx
import polars as pl

USER_AGENT = "TradeHub/0.1 (personal research; single user)"
DEFAULT_TIMEOUT = 30.0

# Bars are passed around as polars frames with exactly these columns and types.
DAILY_SCHEMA: dict[str, pl.DataType] = {
    "ticker_id": pl.String(),
    "date": pl.Date(),  # the exchange's own session date
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),  # as traded (split-adjusted by Yahoo, not dividend-adjusted)
    "adj_close": pl.Float64(),  # split- and dividend-adjusted, kept as its own column
    "volume": pl.Int64(),
    "dividends": pl.Float64(),
    "stock_splits": pl.Float64(),
    "source_id": pl.String(),
}
INTRADAY_SCHEMA: dict[str, pl.DataType] = {
    "ticker_id": pl.String(),
    "interval": pl.String(),
    "ts": pl.Datetime("us", "UTC"),  # bar start, UTC
    "open": pl.Float64(),
    "high": pl.Float64(),
    "low": pl.Float64(),
    "close": pl.Float64(),
    "volume": pl.Int64(),
    "source_id": pl.String(),
}
COT_SCHEMA: dict[str, pl.DataType] = {
    "ticker_id": pl.String(),
    "market_code": pl.String(),
    "report_date": pl.Date(),  # positions as of this Tuesday; published the following Friday
    "market_name": pl.String(),
    "open_interest": pl.Int64(),
    "noncomm_long": pl.Int64(),
    "noncomm_short": pl.Int64(),
    "noncomm_spread": pl.Int64(),
    "comm_long": pl.Int64(),
    "comm_short": pl.Int64(),
    "nonrept_long": pl.Int64(),
    "nonrept_short": pl.Int64(),
    "source_id": pl.String(),
}


class SourceError(Exception):
    """A source failed. The message is the real reason and is stored in sources.last_error."""


@dataclass(frozen=True)
class TickerRef:
    """A row of the tickers table, as much as an adapter needs."""

    id: str  # canonical, e.g. ETF:SPY
    asset_class: str
    symbol: str
    source_symbols: dict[str, str] = field(default_factory=dict)

    def symbol_for(self, source_key: str) -> str | None:
        """The symbol this source uses, or None when the source does not carry the ticker."""
        return self.source_symbols.get(source_key)


@dataclass(frozen=True)
class NewsHeadline:
    title: str
    url: str
    published_at: datetime | None  # UTC
    excerpt: str | None
    ticker_ids: tuple[str, ...]  # tickers the source itself attached to the item
    meta: dict = field(default_factory=dict)  # extra source fields, stored as reference only


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str | None
    engines: tuple[str, ...]


@dataclass(frozen=True)
class SocialSample:
    """Labeled-message counts for one ticker from one fetch. Message text is never kept."""

    ticker_id: str
    bullish: int
    bearish: int
    messages: int  # all messages in the sample, labeled or not


class SourceAdapter(ABC):
    """Base class. `source_id` must match a row in the sources table."""

    source_id: ClassVar[str]

    @abstractmethod
    def check(self) -> str:
        """One cheap real call. Returns a short note on what came back; raises SourceError."""


@runtime_checkable
class NewsSource(Protocol):
    source_id: ClassVar[str]

    def fetch_news(self, ticker: TickerRef, since: datetime) -> list[NewsHeadline]: ...


@runtime_checkable
class BarSource(Protocol):
    source_id: ClassVar[str]

    def fetch_daily_bars(self, ticker: TickerRef, start: date | None = None) -> pl.DataFrame: ...

    def fetch_intraday_bars(
        self, ticker: TickerRef, interval: str, lookback_days: int
    ) -> pl.DataFrame: ...


@runtime_checkable
class SearchSource(Protocol):
    source_id: ClassVar[str]

    def search(self, query: str, categories: str = "news") -> list[SearchResult]: ...


@runtime_checkable
class PositioningSource(Protocol):
    source_id: ClassVar[str]

    def fetch_cot(self, ticker: TickerRef) -> pl.DataFrame: ...


@runtime_checkable
class SocialSource(Protocol):
    source_id: ClassVar[str]

    def fetch_sentiment(self, ticker: TickerRef) -> SocialSample | None: ...


def new_http_client(**kwargs) -> httpx.Client:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    return httpx.Client(
        timeout=kwargs.pop("timeout", DEFAULT_TIMEOUT),
        headers=headers,
        follow_redirects=True,
        **kwargs,
    )


_SECRET_PARAM = re.compile(r"(?i)\b(token|apikey|api_key|api-key|key|secret|password)=([^&\s'\"]+)")


def scrub(message: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove anything key-like from an error message before it is stored or shown."""
    cleaned = _SECRET_PARAM.sub(r"\1=***", message)
    for secret in secrets:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "***")
    return cleaned


def get_json(client: httpx.Client, url: str, *, what: str, **kwargs):
    """GET and decode JSON, turning every failure into a SourceError with the real reason."""
    return _get(client, url, what=what, **kwargs)[1]


def get_bytes(client: httpx.Client, url: str, *, what: str, **kwargs) -> bytes:
    return _get(client, url, what=what, decode_json=False, **kwargs)[0]


def _get(client: httpx.Client, url: str, *, what: str, decode_json: bool = True, **kwargs):
    try:
        response = client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise SourceError(scrub(f"{what}: {type(exc).__name__}: {exc}")) from exc
    if response.status_code != 200:
        body = response.text[:200].replace("\n", " ")
        raise SourceError(scrub(f"{what}: HTTP {response.status_code}: {body}"))
    if not decode_json:
        return response.content, None
    try:
        return response.content, response.json()
    except ValueError as exc:
        start = response.text[:120].replace("\n", " ")
        raise SourceError(f"{what}: expected JSON, got: {start!r}") from exc
