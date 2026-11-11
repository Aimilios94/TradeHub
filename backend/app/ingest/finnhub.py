"""Finnhub: company news per symbol (main news source). Free plan: 60 calls/min, 1 year of news.

The key is sent in the X-Finnhub-Token header, never in the URL, so it cannot end up in an
error message or a log line.
"""

from datetime import UTC, datetime

import httpx

from app.ingest.base import NewsHeadline, SourceAdapter, SourceError, TickerRef, get_json

BASE_URL = "https://finnhub.io/api/v1"
SOURCE_KEY = "finnhub"


def parse_company_news(payload, ticker_id: str) -> list[NewsHeadline]:
    if not isinstance(payload, list):
        raise SourceError(f"Finnhub company-news: expected a list, got {type(payload).__name__}")
    headlines = []
    for item in payload:
        if not isinstance(item, dict):
            raise SourceError("Finnhub company-news: an item is not an object")
        title = str(item.get("headline") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        stamp = item.get("datetime")
        published = datetime.fromtimestamp(stamp, UTC) if isinstance(stamp, int) and stamp else None
        headlines.append(
            NewsHeadline(
                title=title,
                url=url,
                published_at=published,
                excerpt=str(item.get("summary") or "").strip() or None,
                ticker_ids=(ticker_id,),
            )
        )
    return headlines


class FinnhubAdapter(SourceAdapter):
    source_id = "finnhub"

    def __init__(self, client: httpx.Client, api_key: str):
        self._client = client
        self._api_key = api_key

    def _get(self, path: str, what: str, params: dict):
        if not self._api_key:
            raise SourceError("Finnhub: FINNHUB_API_KEY is not set")
        return get_json(
            self._client,
            f"{BASE_URL}{path}",
            what=what,
            params=params,
            headers={"X-Finnhub-Token": self._api_key},
        )

    def check(self) -> str:
        quote = self._get("/quote", "Finnhub quote SPY", {"symbol": "SPY"})
        if not isinstance(quote, dict) or not quote.get("c"):
            raise SourceError("Finnhub quote SPY: no current price in the response")
        return "SPY quote returned a price"

    def fetch_news(self, ticker: TickerRef, since: datetime) -> list[NewsHeadline]:
        if ticker.asset_class not in ("EQ", "ETF"):
            return []
        symbol = ticker.symbol_for(SOURCE_KEY) or ticker.symbol
        params = {
            "symbol": symbol,
            "from": since.astimezone(UTC).date().isoformat(),
            "to": datetime.now(UTC).date().isoformat(),
        }
        payload = self._get("/company-news", f"Finnhub company-news {symbol}", params)
        items = parse_company_news(payload, ticker.id)
        return [item for item in items if item.published_at is None or item.published_at >= since]
