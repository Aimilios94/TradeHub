"""News sources for the Phase 3 catalyst pipeline (plan §Phase 3, §35).

One fetcher per source, each returning a `SourceResult` rather than raising: a source that is not
configured comes back `skipped`, one that errors comes back `failed` with the real message. Neither is
ever reported as "no news" — plan §33, fail safe, never fake.

Adopted now: Finnhub (free tier, company news) and Yahoo RSS (no key).
Reddit is implemented but inert until the owner's script-app keys land in .env.
Polymarket and Firecrawl are values in the `news_source_t` enum but out of scope for Phase 3
(Firecrawl is the plan's explicit last resort, §57).
"""

from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT = 20.0
MAX_ITEMS = 20
MAX_CHARS = 4000
SUMMARY_CHARS = 300

# Reddit needs an explicit subreddit list; overridable from app_settings.sentiment.reddit_subs.
DEFAULT_SUBS = ("stocks", "wallstreetbets", "pennystocks", "StockMarket")


@dataclass(slots=True)
class NewsItem:
    ts: dt.datetime | None
    title: str
    summary: str = ""
    url: str = ""

    def as_dict(self) -> dict:
        return {"ts": self.ts.isoformat() if self.ts else None, "title": self.title,
                "summary": self.summary[:SUMMARY_CHARS], "url": self.url}


@dataclass(slots=True)
class SourceResult:
    source: str
    status: str                                   # ok | skipped | failed
    items: list[NewsItem] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict:
        return {"source": self.source, "status": self.status, "items": len(self.items), "error": self.error}


def _trim(items: list[NewsItem], max_items: int = MAX_ITEMS) -> list[NewsItem]:
    """Newest first, capped. Undated items sort last — they are usually evergreen filler."""
    dated = sorted([i for i in items if i.ts], key=lambda i: i.ts, reverse=True)
    undated = [i for i in items if not i.ts]
    return (dated + undated)[:max_items]


def format_items(items: list[NewsItem], max_chars: int = MAX_CHARS) -> str:
    """Render for the condenser prompt: newest first, each prefixed by its timestamp (template 01)."""
    lines: list[str] = []
    used = 0
    for i in items:
        stamp = i.ts.strftime("%Y-%m-%d %H:%M") if i.ts else "undated"
        text = f"- [{stamp}] {i.title}".strip()
        if i.summary:
            text += " - " + i.summary[:SUMMARY_CHARS]
        if used + len(text) > max_chars:
            break
        lines.append(text)
        used += len(text)
    return "\n".join(lines) if lines else "(no items)"


# ---------------------------------------------------------------- Finnhub

def fetch_finnhub(symbol: str, day: dt.date, lookback_days: int = 2) -> SourceResult:
    """`/company-news` on the free tier. 60 calls/min; the job stays far under that (cap 5 per strategy)."""
    key = get_settings().finnhub_api_key
    if not key:
        return SourceResult("finnhub", "skipped", error="FINNHUB_API_KEY not set")
    frm = (day - dt.timedelta(days=lookback_days)).isoformat()
    try:
        r = httpx.get("https://finnhub.io/api/v1/company-news",
                      params={"symbol": symbol, "from": frm, "to": day.isoformat(), "token": key},
                      timeout=TIMEOUT)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:  # noqa: BLE001
        return SourceResult("finnhub", "failed", error=f"{type(e).__name__}: {str(e)[:160]}")
    if not isinstance(raw, list):
        return SourceResult("finnhub", "failed", error=f"unexpected payload: {str(raw)[:160]}")
    items = []
    for a in raw:
        title = (a.get("headline") or "").strip()
        if not title:
            continue
        stamp = a.get("datetime")
        items.append(NewsItem(
            ts=dt.datetime.fromtimestamp(stamp, tz=dt.UTC) if stamp else None,
            title=title, summary=(a.get("summary") or "").strip(), url=a.get("url") or ""))
    return SourceResult("finnhub", "ok", _trim(items))


# ---------------------------------------------------------------- Yahoo RSS

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"


def parse_yahoo_rss(xml_text: str) -> list[NewsItem]:
    """Stdlib RSS parsing — deliberately no feedparser dependency for four fields."""
    items: list[NewsItem] = []
    root = ET.fromstring(xml_text)
    for node in root.iterfind(".//item"):
        title = (node.findtext("title") or "").strip()
        if not title:
            continue
        ts = None
        raw_date = node.findtext("pubDate")
        if raw_date:
            try:
                ts = parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                ts = None
        items.append(NewsItem(ts=ts, title=title, summary=(node.findtext("description") or "").strip(),
                              url=(node.findtext("link") or "").strip()))
    return items


def fetch_yahoo(symbol: str) -> SourceResult:
    try:
        r = httpx.get(YAHOO_RSS, params={"s": symbol, "region": "US", "lang": "en-US"},
                      timeout=TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; tradehub/0.1)"})
        r.raise_for_status()
        items = parse_yahoo_rss(r.text)
    except ET.ParseError as e:
        return SourceResult("yahoo", "failed", error=f"RSS parse error: {str(e)[:160]}")
    except Exception as e:  # noqa: BLE001
        return SourceResult("yahoo", "failed", error=f"{type(e).__name__}: {str(e)[:160]}")
    return SourceResult("yahoo", "ok", _trim(items))


# ---------------------------------------------------------------- Reddit

def _reddit_token() -> str:
    s = get_settings()
    r = httpx.post("https://www.reddit.com/api/v1/access_token",
                   data={"grant_type": "client_credentials"},
                   auth=(s.reddit_client_id, s.reddit_client_secret),
                   headers={"User-Agent": s.reddit_user_agent}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_reddit(symbol: str, subs: tuple[str, ...] = DEFAULT_SUBS) -> SourceResult:
    """Implemented and ready; inert until REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are filled in."""
    s = get_settings()
    if not (s.reddit_client_id and s.reddit_client_secret):
        return SourceResult("reddit", "skipped", error="no credentials - set REDDIT_CLIENT_ID/SECRET in .env")
    try:
        token = _reddit_token()
        r = httpx.get("https://oauth.reddit.com/r/" + "+".join(subs) + "/search",
                      params={"q": symbol, "restrict_sr": 1, "sort": "new", "t": "day", "limit": 25},
                      headers={"Authorization": f"Bearer {token}", "User-Agent": s.reddit_user_agent},
                      timeout=TIMEOUT)
        r.raise_for_status()
        children = r.json().get("data", {}).get("children", [])
    except Exception as e:  # noqa: BLE001
        return SourceResult("reddit", "failed", error=f"{type(e).__name__}: {str(e)[:160]}")
    items = []
    for c in children:
        d = c.get("data", {})
        title = (d.get("title") or "").strip()
        if not title:
            continue
        created = d.get("created_utc")
        items.append(NewsItem(
            ts=dt.datetime.fromtimestamp(created, tz=dt.UTC) if created else None,
            title=title, summary=(d.get("selftext") or "")[:SUMMARY_CHARS].strip(),
            url="https://reddit.com" + (d.get("permalink") or "")))
    return SourceResult("reddit", "ok", _trim(items))


KNOWN_SOURCES = ("finnhub", "yahoo", "reddit")


def fetch(source: str, symbol: str, day: dt.date, *, lookback_days: int = 2,
          subs: tuple[str, ...] = DEFAULT_SUBS) -> SourceResult:
    """Dispatch by name. An unknown source name is a config error — reported, never guessed."""
    if source == "finnhub":
        return fetch_finnhub(symbol, day, lookback_days)
    if source == "yahoo":
        return fetch_yahoo(symbol)
    if source == "reddit":
        return fetch_reddit(symbol, subs)
    return SourceResult(source, "failed",
                        error=f"unknown source '{source}' (known: {', '.join(KNOWN_SOURCES)})")
