"""StockTwits: the latest public messages for a symbol, counted by the label their author chose.

Unofficial public endpoint, no key. Each call returns about 30 recent messages, and only some
carry a Bullish or Bearish label, so the sample is small: the score uses it only above a minimum
count, and only as an addition (D21). The endpoint sometimes answers with an HTML page instead
of JSON; that is reported as the error it is, and nothing is retried or worked around.

Message text is never stored: only the counts.
"""

import httpx

from app.ingest.base import SocialSample, SourceAdapter, SourceError, TickerRef, get_json

STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
SOURCE_KEY = "stocktwits"  # optional tickers.source_symbols override; default is the symbol
LABELS = {"Bullish": "bullish", "Bearish": "bearish"}


def parse_stream(payload, ticker_id: str, symbol: str) -> SocialSample:
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        raise SourceError(f"StockTwits {symbol}: response has no 'messages' list")
    counts = {"bullish": 0, "bearish": 0}
    for message in payload["messages"]:
        if not isinstance(message, dict):
            raise SourceError(f"StockTwits {symbol}: a message is not an object")
        sentiment = (message.get("entities") or {}).get("sentiment") or {}
        label = LABELS.get(sentiment.get("basic"))
        if label:
            counts[label] += 1
    return SocialSample(ticker_id, counts["bullish"], counts["bearish"], len(payload["messages"]))


class StockTwitsAdapter(SourceAdapter):
    source_id = "stocktwits"

    def __init__(self, client: httpx.Client):
        self._client = client

    def _fetch(self, symbol: str, ticker_id: str) -> SocialSample:
        payload = get_json(
            self._client, STREAM_URL.format(symbol=symbol), what=f"StockTwits {symbol}"
        )
        return parse_stream(payload, ticker_id, symbol)

    def check(self) -> str:
        sample = self._fetch("SPY", "ETF:SPY")
        if not sample.messages:
            raise SourceError("StockTwits SPY: the stream held no messages")
        return (
            f"SPY: {sample.messages} messages, {sample.bullish} bullish, {sample.bearish} bearish"
        )

    def fetch_sentiment(self, ticker: TickerRef) -> SocialSample | None:
        """None when the ticker is not a stock or ETF (StockTwits symbols differ for the rest)."""
        if ticker.asset_class not in ("EQ", "ETF"):
            return None
        symbol = ticker.symbol_for(SOURCE_KEY) or ticker.symbol
        return self._fetch(symbol, ticker.id)
