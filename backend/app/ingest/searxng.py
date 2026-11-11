"""Self-hosted SearXNG: deep search. Engines come and go; that is reported, not hidden."""

import httpx

from app.ingest.base import SearchResult, SourceAdapter, SourceError, get_json


def parse_results(payload) -> tuple[list[SearchResult], list[str]]:
    """Returns the results and a note per engine that did not answer."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise SourceError("SearXNG: response has no 'results' list")
    results = []
    for item in payload["results"]:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=str(item.get("content") or "").strip() or None,
                engines=tuple(item.get("engines") or ()),
            )
        )
    unresponsive = [
        f"{entry[0]}: {entry[1]}" if len(entry) > 1 else str(entry[0])
        for entry in payload.get("unresponsive_engines") or []
    ]
    return results, unresponsive


class SearxngAdapter(SourceAdapter):
    source_id = "searxng"

    def __init__(self, client: httpx.Client, base_url: str):
        self._client = client
        self._base_url = base_url.rstrip("/")

    def _query(self, query: str, categories: str) -> tuple[list[SearchResult], list[str]]:
        payload = get_json(
            self._client,
            f"{self._base_url}/search",
            what=f"SearXNG '{query}'",
            params={"q": query, "format": "json", "categories": categories},
        )
        results, unresponsive = parse_results(payload)
        if not results:
            reason = "; ".join(unresponsive) or "no engine reported a problem"
            raise SourceError(f"SearXNG '{query}': no results ({reason})")
        return results, unresponsive

    def check(self) -> str:
        results, unresponsive = self._query("S&P 500 stock market", "news")
        engines = sorted({engine for result in results for engine in result.engines})
        note = f"{len(results)} news results from {', '.join(engines)}"
        return f"{note}; not answering: {'; '.join(unresponsive)}" if unresponsive else note

    def search(self, query: str, categories: str = "news") -> list[SearchResult]:
        return self._query(query, categories)[0]
