"""Massive (formerly Polygon.io), free plans: futures backup for the strip, and a third news feed.

Free limits: 5 calls a minute, 2 years of history, futures data about 8 hours old. The adapter
paces itself to stay under the limit. The key goes in the Authorization header, never the URL.

Futures here are real dated contracts (ESZ6), not a continuous series. For display the adapter
picks the most active of the nearest contracts: the nearest by expiry is often an untraded serial
month (gold in November trades in the December contract). Display-only, never backtested.

Massive attaches its own model's sentiment to each news item. It is kept as a reference under
`meta`; it is never TradeHub's sentiment and never an eval label.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from app.ingest.base import NewsHeadline, SourceAdapter, SourceError, TickerRef, get_json

BASE_URL = "https://api.massive.com"
SOURCE_KEY = "massive"  # tickers.source_symbols key holding the futures product code, e.g. ES
MIN_SECONDS_BETWEEN_CALLS = 12.5  # 5 calls a minute
CANDIDATE_CONTRACTS = 4  # nearest expiries compared when choosing the active contract
NEWS_PAGE_SIZE = 1000


@dataclass(frozen=True)
class FuturesSession:
    """One daily session of one contract."""

    ticker_id: str
    contract: str  # e.g. ESZ6
    session_date: date
    open: float
    high: float
    low: float
    close: float
    settlement: float | None
    volume: int


def parse_contracts(payload, product: str) -> list[str]:
    """Contract tickers for a product, nearest expiry first."""
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SourceError(f"Massive contracts {product}: response has no 'results' list")
    try:
        ordered = sorted(results, key=lambda row: row["last_trade_date"])
        return [row["ticker"] for row in ordered]
    except (KeyError, TypeError) as exc:
        raise SourceError(f"Massive contracts {product}: unexpected row shape ({exc})") from exc


def parse_sessions(payload, ticker_id: str, contract: str) -> list[FuturesSession]:
    """Daily sessions of a contract, newest first."""
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SourceError(f"Massive sessions {contract}: response has no 'results' list")
    sessions = []
    for row in results:
        try:
            settlement = row.get("settlement_price")
            sessions.append(
                FuturesSession(
                    ticker_id=ticker_id,
                    contract=contract,
                    session_date=date.fromisoformat(row["session_end_date"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    settlement=float(settlement) if settlement is not None else None,
                    volume=int(row["volume"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(f"Massive sessions {contract}: unexpected row shape ({exc})") from exc
    return sorted(sessions, key=lambda session: session.session_date, reverse=True)


def parse_news(payload, ticker_id: str) -> list[NewsHeadline]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SourceError("Massive news: response has no 'results' list")
    headlines = []
    for row in results:
        title = str(row.get("title") or "").strip()
        url = str(row.get("article_url") or "").strip()
        if not title or not url:
            continue
        published = None
        if row.get("published_utc"):
            try:
                published = datetime.fromisoformat(row["published_utc"]).astimezone(UTC)
            except ValueError as exc:
                raise SourceError(f"Massive news: bad published_utc ({exc})") from exc
        insights = [
            {"ticker": item.get("ticker"), "sentiment": item.get("sentiment")}
            for item in row.get("insights") or []
        ]
        meta = {
            "publisher": (row.get("publisher") or {}).get("name"),
            "source_tickers_raw": row.get("tickers") or [],
            # Massive's own model output. Reference only.
            "massive_insights": insights,
        }
        headlines.append(
            NewsHeadline(
                title=title,
                url=url,
                published_at=published,
                excerpt=str(row.get("description") or "").strip() or None,
                ticker_ids=(ticker_id,),
                meta=meta,
            )
        )
    return headlines


class MassiveAdapter(SourceAdapter):
    source_id = "massive"

    def __init__(
        self,
        client: httpx.Client,
        api_key: str,
        min_interval: float = MIN_SECONDS_BETWEEN_CALLS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ):
        self._client = client
        self._api_key = api_key
        self._min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._today = today
        self._last_call: float | None = None
        self._active: dict[tuple[str, date], str] = {}  # (product, day) -> chosen contract

    def _get(self, path: str, what: str, params: dict):
        if not self._api_key:
            raise SourceError("Massive: MASSIVE_API_KEY is not set")
        if self._last_call is not None:
            wait = self._min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()
        return get_json(
            self._client,
            f"{BASE_URL}{path}",
            what=what,
            params=params,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    def _sessions(self, ticker_id: str, contract: str, limit: int) -> list[FuturesSession]:
        payload = self._get(
            f"/futures/v1/aggs/{contract}",
            f"Massive sessions {contract}",
            {"resolution": "1session", "limit": limit, "sort": "window_start.desc"},
        )
        return parse_sessions(payload, ticker_id, contract)

    def check(self) -> str:
        payload = self._get(
            "/futures/v1/contracts",
            "Massive contracts ES",
            {
                "product_code": "ES",
                "active": "true",
                "type": "single",
                # Without a date the list also holds contracts that expired years ago.
                "date": self._today().isoformat(),
                "limit": 200,
            },
        )
        contracts = parse_contracts(payload, "ES")
        if not contracts:
            raise SourceError("Massive contracts ES: no active contract returned")
        return f"ES: {len(contracts)} active contracts, nearest {contracts[0]}"

    def fetch_futures_sessions(self, ticker: TickerRef, limit: int = 2) -> list[FuturesSession]:
        """Latest daily sessions of the product's most active near contract, newest first."""
        product = ticker.symbol_for(SOURCE_KEY)
        if not product:
            raise SourceError(f"{ticker.id} has no Massive product code in tickers.source_symbols")
        day = self._today()
        chosen = self._active.get((product, day))
        if chosen:
            sessions = self._sessions(ticker.id, chosen, limit)
            if not sessions:
                raise SourceError(f"Massive sessions {chosen}: no rows returned")
            return sessions

        payload = self._get(
            "/futures/v1/contracts",
            f"Massive contracts {product}",
            {
                "product_code": product,
                "active": "true",
                "type": "single",
                "date": day.isoformat(),
                "limit": 200,
            },
        )
        candidates = parse_contracts(payload, product)[:CANDIDATE_CONTRACTS]
        if not candidates:
            raise SourceError(f"Massive contracts {product}: no active contract returned")
        best: list[FuturesSession] = []
        for contract in candidates:
            sessions = self._sessions(ticker.id, contract, limit)
            if sessions and (not best or sessions[0].volume > best[0].volume):
                best = sessions
        if not best:
            raise SourceError(f"Massive {product}: none of {candidates} has a session bar")
        self._active[(product, day)] = best[0].contract
        return best

    def fetch_news(self, ticker: TickerRef, since: datetime) -> list[NewsHeadline]:
        if ticker.asset_class not in ("EQ", "ETF"):
            return []
        payload = self._get(
            "/v2/reference/news",
            f"Massive news {ticker.symbol}",
            {
                "ticker": ticker.symbol,
                "published_utc.gte": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "order": "desc",
                "sort": "published_utc",
                "limit": NEWS_PAGE_SIZE,
            },
        )
        return parse_news(payload, ticker.id)
