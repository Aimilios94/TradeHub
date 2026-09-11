"""Historical bar store for the backtester (plan §5, §13).

Supabase keeps a rolling ~90 days of 1-min bars; deep history lives here, as parquet under
`PARQUET_STORE_PATH`, one file per (timeframe, feed, symbol). A backtest iterated twenty times over
the same two years must hit Alpaca once, not twenty times — the free plan's rate limit is the binding
constraint on how often a hypothesis can be graded.

Each file carries its own coverage range in the sidecar `_coverage.json`, so a request that the cache
already spans is answered from disk without a network call, and a request that extends the range
fetches only what is missing and merges.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import get_settings
from app.marketdata import alpaca

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
COLUMNS = ("open", "high", "low", "close", "volume")


def root(timeframe: str, feed: str) -> Path:
    p = get_settings().parquet_store_path / "bars" / timeframe / feed
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(symbol: str, timeframe: str, feed: str) -> Path:
    return root(timeframe, feed) / f"{symbol.upper()}.parquet"


def _coverage_path(timeframe: str, feed: str) -> Path:
    return root(timeframe, feed) / "_coverage.json"


def load_coverage(timeframe: str, feed: str) -> dict[str, list[str]]:
    p = _coverage_path(timeframe, feed)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("bar cache coverage unreadable (%s) — treating the cache as empty", e)
        return {}


def save_coverage(timeframe: str, feed: str, cov: dict[str, list[str]]) -> None:
    _coverage_path(timeframe, feed).write_text(json.dumps(cov, indent=0, sort_keys=True), encoding="utf-8")


def read_cached(symbol: str, timeframe: str, feed: str) -> pd.DataFrame | None:
    p = _path(symbol, timeframe, feed)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except (OSError, ValueError) as e:
        log.warning("unreadable parquet for %s (%s) — refetching", symbol, e)
        return None
    if df.empty:
        return None
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
    return df.sort_index()


def write_cached(symbol: str, timeframe: str, feed: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    out = df[list(COLUMNS)].copy()
    out.index = pd.to_datetime(out.index, utc=True) if out.index.tz is None else out.index.tz_convert("UTC")
    out.to_parquet(_path(symbol, timeframe, feed), compression="snappy")


def _frame(bars: list[alpaca.Bar]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=list(COLUMNS))
    rows = [{"ts": b.ts_et(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
             "volume": b.volume} for b in bars]
    return pd.DataFrame(rows).set_index("ts").sort_index()


def _rejected_path(timeframe: str, feed: str) -> Path:
    return root(timeframe, feed) / "_rejected.json"


def load_rejected(timeframe: str, feed: str) -> set[str]:
    p = _rejected_path(timeframe, feed)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def save_rejected(timeframe: str, feed: str, bad: set[str]) -> None:
    if bad:
        _rejected_path(timeframe, feed).write_text(json.dumps(sorted(bad)), encoding="utf-8")


def _fetch_chunk(chunk: list[str], start: dt.datetime, end: dt.datetime, *, timeframe: str, feed: str,
                 out: dict[str, list[alpaca.Bar]], bad: set[str]) -> None:
    """Fetch one batch; on rejection, bisect to find the symbol Alpaca will not accept.

    Alpaca refuses an ENTIRE multi-symbol request when any one symbol is invalid, and the replay
    universe deliberately contains delisted names ("HYAC_DELISTED", CUSIP-shaped ids, preferred-share
    tickers with dots) — so one bad ticker used to silently cost the other 99 their history. That is
    not a small loss: those 99 are then screened out of every session, which quietly re-biases the very
    universe the survivorship countermeasure exists to protect. Bisecting isolates the offender in
    ~log2(n) extra calls, and the name is remembered so later runs never pay for it again.
    """
    try:
        for b in alpaca.get_bars(chunk, start, end, feed=feed, timeframe=timeframe):
            out.setdefault(b.symbol, []).append(b)
        return
    except Exception as e:  # noqa: BLE001
        if len(chunk) == 1:
            bad.add(chunk[0])
            log.debug("data API refuses %s: %s", chunk[0], e)
            return
        log.debug("batch of %d rejected (%s) — bisecting", len(chunk), str(e)[:80])
    mid = len(chunk) // 2
    _fetch_chunk(chunk[:mid], start, end, timeframe=timeframe, feed=feed, out=out, bad=bad)
    _fetch_chunk(chunk[mid:], start, end, timeframe=timeframe, feed=feed, out=out, bad=bad)


def fetch(symbols: list[str], start: dt.datetime, end: dt.datetime, *, timeframe: str,
          feed: str, batch: int = 100) -> dict[str, pd.DataFrame]:
    """Straight through to Alpaca, batched. No caching — `get` is the caching entry point."""
    known_bad = load_rejected(timeframe, feed)
    wanted = [s for s in symbols if s not in known_bad]
    out: dict[str, list[alpaca.Bar]] = {}
    bad: set[str] = set()
    for i in range(0, len(wanted), batch):
        _fetch_chunk(wanted[i:i + batch], start, end, timeframe=timeframe, feed=feed, out=out, bad=bad)
    if bad:
        log.info("%d symbol(s) refused by the data API and remembered: %s", len(bad),
                 ", ".join(sorted(bad)[:5]))
        save_rejected(timeframe, feed, known_bad | bad)
    return {s: _frame(bl) for s, bl in out.items()}


def get(symbols: list[str], start: dt.date, end: dt.date, *, timeframe: str = "5Min", feed: str = "sip",
        use_cache: bool = True, refresh: bool = False) -> tuple[dict[str, pd.DataFrame], dict]:
    """Bars for `symbols` over [start, end] inclusive, from cache where possible.

    Returns (frames, stats). `stats` reports how many symbols came from disk versus the network, which
    is worth seeing: a run that hit the network for everything is a run that will be slow to repeat.
    """
    symbols = sorted({s.upper() for s in symbols})
    cov = load_coverage(timeframe, feed) if use_cache else {}
    frames: dict[str, pd.DataFrame] = {}
    need: list[str] = []
    hits = 0
    for s in symbols:
        span = cov.get(s)
        if refresh or not use_cache or not span:
            need.append(s)
            continue
        c_lo, c_hi = dt.date.fromisoformat(span[0]), dt.date.fromisoformat(span[1])
        if c_lo <= start and c_hi >= end:
            df = read_cached(s, timeframe, feed)
            if df is not None:
                frames[s] = df.loc[str(start):str(end + dt.timedelta(days=1))]
                hits += 1
                continue
        need.append(s)

    fetched = 0
    if need:
        lo = alpaca.et_datetime(start, 0, 0)
        hi = alpaca.et_datetime(end, 23, 59)
        fresh = fetch(need, lo, hi, timeframe=timeframe, feed=feed)
        for s in need:
            df = fresh.get(s)
            if df is None or df.empty:
                continue
            fetched += 1
            if use_cache:
                old = read_cached(s, timeframe, feed)
                merged = df if old is None else pd.concat([old, df])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                write_cached(s, timeframe, feed, merged)
                span = cov.get(s)
                c_lo = min(dt.date.fromisoformat(span[0]), start) if span else start
                c_hi = max(dt.date.fromisoformat(span[1]), end) if span else end
                cov[s] = [c_lo.isoformat(), c_hi.isoformat()]
                frames[s] = merged.loc[str(start):str(end + dt.timedelta(days=1))]
            else:
                frames[s] = df
        if use_cache:
            save_coverage(timeframe, feed, cov)

    return frames, {"requested": len(symbols), "from_cache": hits, "from_network": fetched,
                    "empty": len(symbols) - hits - fetched, "timeframe": timeframe, "feed": feed,
                    "cache_path": str(root(timeframe, feed))}


def sessions(df: pd.DataFrame) -> dict[dt.date, pd.DataFrame]:
    """Split an intraday frame into one frame per ET trading date."""
    if df is None or df.empty:
        return {}
    return {d: g for d, g in df.groupby(df.index.date)}


def cache_stats() -> dict:
    """What the parquet store holds — shown with every backtest so the data behind it is never a mystery."""
    base = get_settings().parquet_store_path / "bars"
    out: dict = {"path": str(base), "sets": []}
    if not base.exists():
        return out | {"note": "no bars cached yet"}
    for tf in sorted(p for p in base.iterdir() if p.is_dir()):
        for fd in sorted(p for p in tf.iterdir() if p.is_dir()):
            files = [f for f in fd.glob("*.parquet")]
            out["sets"].append({"timeframe": tf.name, "feed": fd.name, "symbols": len(files),
                                "megabytes": round(sum(f.stat().st_size for f in files) / 1e6, 2)})
    return out


# ---------- day-partitioned cache (the scanner-replay access pattern) ----------
#
# A scanner replay wants "every candidate symbol, one session" — the opposite shape from "one symbol,
# a long range". Storing that per symbol would rewrite hundreds of tiny files per day, so a session is
# cached as ONE parquet plus the list of symbols it covers.

def _day_dir(timeframe: str, feed: str) -> Path:
    p = root(timeframe, feed) / "_days"
    p.mkdir(parents=True, exist_ok=True)
    return p


def day_frames(symbols: list[str], day: dt.date, *, timeframe: str = "1Min", feed: str = "sip",
               use_cache: bool = True) -> tuple[dict[str, pd.DataFrame], dict]:
    """One session's intraday bars for many symbols, as {symbol: frame}."""
    symbols = sorted({s.upper() for s in symbols})
    f = _day_dir(timeframe, feed) / f"{day.isoformat()}.parquet"
    meta = f.with_suffix(".json")
    have: set[str] = set()
    cached: pd.DataFrame | None = None
    if use_cache and f.exists() and meta.exists():
        try:
            have = set(json.loads(meta.read_text(encoding="utf-8")))
            cached = pd.read_parquet(f)
            cached.index = pd.to_datetime(cached.index, utc=True).tz_convert(ET)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            log.warning("day cache for %s unreadable (%s) — refetching", day, e)
            have, cached = set(), None

    missing = [s for s in symbols if s not in have]
    if missing:
        lo = alpaca.et_datetime(day, 0, 0)
        hi = alpaca.et_datetime(day, 23, 59)
        fresh = fetch(missing, lo, hi, timeframe=timeframe, feed=feed)
        rows = []
        for s, df in fresh.items():
            if df.empty:
                continue
            d = df.copy()
            d["symbol"] = s
            rows.append(d)
        new = pd.concat(rows) if rows else pd.DataFrame(columns=[*COLUMNS, "symbol"])
        cached = new if cached is None or cached.empty else pd.concat([cached, new])
        have |= set(missing)          # a symbol that returned nothing is COVERED, not retried forever
        if use_cache and cached is not None and not cached.empty:
            out = cached.copy()
            out.index = pd.to_datetime(out.index, utc=True) if out.index.tz is None \
                else out.index.tz_convert("UTC")
            out.to_parquet(f, compression="snappy")
            meta.write_text(json.dumps(sorted(have)), encoding="utf-8")

    if cached is None or cached.empty:
        return {}, {"day": day.isoformat(), "requested": len(symbols), "with_bars": 0,
                    "from_network": len(missing)}
    wanted = set(symbols)
    frames = {s: g.drop(columns=["symbol"]).sort_index()
              for s, g in cached.groupby("symbol") if s in wanted}
    return frames, {"day": day.isoformat(), "requested": len(symbols), "with_bars": len(frames),
                    "from_network": len(missing), "from_cache": len(symbols) - len(missing)}
