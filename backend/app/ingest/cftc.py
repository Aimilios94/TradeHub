"""CFTC Commitments of Traders, legacy futures-only report, through the public Socrata API.

No key. History back to 1986. Each report shows positions as of a Tuesday and is published the
following Friday, so a backtest must not use a row before its release.
"""

from datetime import date

import httpx
import polars as pl

from app.ingest.base import COT_SCHEMA, SourceAdapter, SourceError, TickerRef, get_json

DATASET_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
SOURCE_KEY = "cftc"  # tickers.source_symbols key holding the CFTC contract market code
PAGE_SIZE = 50_000

# our column -> the dataset's field name (spelling is the CFTC's own)
_FIELDS = {
    "open_interest": "open_interest_all",
    "noncomm_long": "noncomm_positions_long_all",
    "noncomm_short": "noncomm_positions_short_all",
    "noncomm_spread": "noncomm_postions_spread_all",
    "comm_long": "comm_positions_long_all",
    "comm_short": "comm_positions_short_all",
    "nonrept_long": "nonrept_positions_long_all",
    "nonrept_short": "nonrept_positions_short_all",
}


def parse_reports(payload, ticker_id: str, source_id: str) -> pl.DataFrame:
    if not isinstance(payload, list):
        raise SourceError(f"CFTC COT: expected a list, got {type(payload).__name__}")
    rows = []
    for item in payload:
        try:
            row = {
                "ticker_id": ticker_id,
                "market_code": item["cftc_contract_market_code"],
                "report_date": date.fromisoformat(item["report_date_as_yyyy_mm_dd"][:10]),
                "market_name": item["market_and_exchange_names"],
                "source_id": source_id,
            }
            # An older report can lack a field; it stays empty rather than becoming a zero.
            for column, field in _FIELDS.items():
                row[column] = int(item[field]) if item.get(field) not in (None, "") else None
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError(
                f"CFTC COT: unexpected row shape ({type(exc).__name__}: {exc})"
            ) from exc
        rows.append(row)
    return pl.DataFrame(rows, schema=COT_SCHEMA).sort("report_date")


class CftcCotAdapter(SourceAdapter):
    source_id = "cftc_cot"

    def __init__(self, client: httpx.Client):
        self._client = client

    def _get(self, market_code: str, limit: int, offset: int = 0):
        return get_json(
            self._client,
            DATASET_URL,
            what=f"CFTC COT {market_code}",
            params={
                "cftc_contract_market_code": market_code,
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": limit,
                "$offset": offset,
            },
        )

    def check(self) -> str:
        frame = parse_reports(self._get("088691", limit=1), "FUT:GC", self.source_id)
        if frame.is_empty():
            raise SourceError("CFTC COT 088691 (gold): no report returned")
        return f"gold: latest report {frame['report_date'][0]}"

    def fetch_cot(self, ticker: TickerRef) -> pl.DataFrame:
        market_code = ticker.symbol_for(SOURCE_KEY)
        if not market_code:
            raise SourceError(f"{ticker.id} has no CFTC market code in tickers.source_symbols")
        pages, offset = [], 0
        while True:
            payload = self._get(market_code, limit=PAGE_SIZE, offset=offset)
            pages.append(parse_reports(payload, ticker.id, self.source_id))
            if len(payload) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        frame = pl.concat(pages)
        if frame.is_empty():
            raise SourceError(f"CFTC COT {market_code}: no reports returned")
        return frame.unique(["ticker_id", "report_date"], keep="last").sort("report_date")
