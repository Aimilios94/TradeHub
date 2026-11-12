"""Builds the adapters. The key is the source's id in the sources table.

Sources verified earlier without an adapter yet (FMP, Tiingo, Twelve Data, FRED, BLS) get
theirs in the phase that first uses them.
"""

import httpx

from app.config import Settings
from app.ingest.base import SourceAdapter, new_http_client
from app.ingest.cftc import CftcCotAdapter
from app.ingest.finnhub import FinnhubAdapter
from app.ingest.massive import MassiveAdapter
from app.ingest.searxng import SearxngAdapter
from app.ingest.stocktwits import StockTwitsAdapter
from app.ingest.yahoo import YahooFinanceAdapter
from app.ingest.yahoo_rss import YahooRssAdapter


def build_adapters(
    settings: Settings, client: httpx.Client | None = None
) -> dict[str, SourceAdapter]:
    client = client or new_http_client()
    adapters: list[SourceAdapter] = [
        YahooFinanceAdapter(),
        YahooRssAdapter(client),
        FinnhubAdapter(client, settings.finnhub_api_key),
        MassiveAdapter(client, settings.massive_api_key),
        SearxngAdapter(client, settings.searxng_base_url),
        CftcCotAdapter(client),
        StockTwitsAdapter(client),
    ]
    return {adapter.source_id: adapter for adapter in adapters}
