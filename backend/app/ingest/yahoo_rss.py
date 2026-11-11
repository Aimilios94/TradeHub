"""Yahoo Finance RSS: company headlines per symbol. Backup news source; no key."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from defusedxml import ElementTree

from app.ingest.base import NewsHeadline, SourceAdapter, SourceError, TickerRef, get_bytes

FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
SOURCE_KEY = "yahoo"


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_feed(payload: bytes, ticker_id: str) -> list[NewsHeadline]:
    try:
        root = ElementTree.fromstring(payload)
    except Exception as exc:  # defusedxml raises several unrelated error types
        raise SourceError(f"Yahoo RSS: response is not valid XML ({type(exc).__name__})") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise SourceError(f"Yahoo RSS: expected <rss><channel>, got <{root.tag}>")
    headlines = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url:
            continue
        headlines.append(
            NewsHeadline(
                title=title,
                url=url,
                published_at=_parse_date(item.findtext("pubDate")),
                excerpt=(item.findtext("description") or "").strip() or None,
                ticker_ids=(ticker_id,),
            )
        )
    return headlines


class YahooRssAdapter(SourceAdapter):
    source_id = "yahoo_rss"

    def __init__(self, client: httpx.Client):
        self._client = client

    def _fetch(self, symbol: str, ticker_id: str) -> list[NewsHeadline]:
        payload = get_bytes(
            self._client,
            FEED_URL,
            what=f"Yahoo RSS {symbol}",
            params={"s": symbol, "region": "US", "lang": "en-US"},
        )
        return parse_feed(payload, ticker_id)

    def check(self) -> str:
        items = self._fetch("SPY", "ETF:SPY")
        if not items:
            raise SourceError("Yahoo RSS SPY: the feed parsed but held no items")
        return f"SPY feed: {len(items)} items"

    def fetch_news(self, ticker: TickerRef, since: datetime) -> list[NewsHeadline]:
        """The feed only ever holds the latest ~20 items; `since` trims older ones."""
        symbol = ticker.symbol_for(SOURCE_KEY)
        if not symbol or ticker.asset_class not in ("EQ", "ETF"):
            return []
        items = self._fetch(symbol, ticker.id)
        return [item for item in items if item.published_at is None or item.published_at >= since]
