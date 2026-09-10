"""Nightly universe job — per-symbol stats for every tradable listed US equity (decision #16).

Alpaca assets (free) → SIP daily bars for the last ~60 sessions (free, one multi-symbol request per batch) →
prev close/open/high/low/volume, ADV20/50, Wilder ATR14, 20/60-day highs/lows → upsert `symbol_stats`.
Runs after the close (n8n 23:00 ET, workflow 01) in ~3 minutes for ~13k symbols. Float/shares data is
attached separately (`enrich_shares_outstanding`) only for scan survivors — FMP free tier is 250 calls/day.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from functools import lru_cache

import httpx
import pandas as pd

from app.config import get_settings
from app.db import get_client
from app.detectors.basic import wilder_atr
from app.marketdata import alpaca

log = logging.getLogger(__name__)

LISTED_EXCHANGES = ("NYSE", "NASDAQ", "ARCA", "AMEX", "BATS")
ASSET_BATCH = 1000     # symbols per daily-bars request (~60 rows each → ~6 pages)
UPSERT_CHUNK = 500


@dataclass(slots=True)
class UniverseResult:
    as_of: str
    assets: int = 0
    listed: int = 0
    with_bars: int = 0
    rows_written: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {"as_of": self.as_of, "assets": self.assets, "listed": self.listed, "with_bars": self.with_bars,
                "rows_written": self.rows_written, "errors": self.errors, "elapsed_s": self.elapsed_s}


@lru_cache
def _trading_client():
    from alpaca.trading.client import TradingClient

    s = get_settings()
    return TradingClient(s.alpaca_paper_key_id, s.alpaca_paper_secret_key, paper=True)


def list_assets() -> list[dict]:
    """Active, tradable US equities on the listed exchanges (OTC excluded)."""
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    assets = _trading_client().get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY))
    out = []
    for a in assets:
        if not a.tradable or a.exchange.value not in LISTED_EXCHANGES:
            continue
        out.append({"symbol": a.symbol, "exchange": a.exchange.value, "name": a.name, "tradable": a.tradable,
                    "marginable": a.marginable, "shortable": a.shortable, "easy_to_borrow": a.easy_to_borrow,
                    "fractionable": a.fractionable})
    return out


def daily_frame(bars: list[alpaca.Bar]) -> pd.DataFrame:
    """OHLCV frame indexed by ET session date, ascending — the `daily` context frame for detectors."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame([{"date": b.ts_et().date(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                        "volume": b.volume} for b in bars]).set_index("date").sort_index()
    return df[~df.index.duplicated(keep="last")]


def stats_from_daily(symbol: str, df: pd.DataFrame) -> dict | None:
    if df.empty:
        return None
    last = df.iloc[-1]
    t20, t60 = df.tail(20), df.tail(60)
    a = wilder_atr(df.tail(40), 14)
    return {
        "symbol": symbol, "as_of": str(df.index[-1]), "sessions": len(df),
        "prev_close": float(last["close"]), "prev_open": float(last["open"]), "prev_high": float(last["high"]),
        "prev_low": float(last["low"]), "prev_volume": int(last["volume"]),
        "adv20": int(round(t20["volume"].mean())), "adv50": int(round(df.tail(50)["volume"].mean())),
        "atr14": round(a, 4) if a is not None else None,
        "avg_range_pct20": round(float(((t20["high"] - t20["low"]) / t20["close"]).mean() * 100), 4),
        "high_20d": float(t20["high"].max()), "low_20d": float(t20["low"].min()),
        "high_60d": float(t60["high"].max()), "low_60d": float(t60["low"].min()),
    }


def run_universe(*, lookback_days: int = 90, symbols: list[str] | None = None, dry_run: bool = False) -> UniverseResult:
    import time

    t0 = time.time()
    now = alpaca.now_et()
    res = UniverseResult(as_of=now.date().isoformat())
    try:
        assets = list_assets()
    except Exception as e:  # noqa: BLE001
        res.errors.append(f"assets: {type(e).__name__}: {str(e)[:160]}")
        return res
    res.assets = len(assets)
    if symbols:
        want = {s.upper() for s in symbols}
        assets = [a for a in assets if a["symbol"] in want]
    res.listed = len(assets)
    by_sym = {a["symbol"]: a for a in assets}
    syms = sorted(by_sym)

    start = alpaca.et_datetime(now.date() - dt.timedelta(days=lookback_days), 0, 0)
    # Include today's daily bar only once the session is closed (else prev_close would be a partial bar), and
    # never ask SIP for the last 15 minutes — the free plan refuses "recent SIP data".
    if now.time() >= dt.time(16, 15):
        end = min(alpaca.et_datetime(now.date(), 23, 59), now - dt.timedelta(minutes=16))
    else:
        end = alpaca.et_datetime(now.date() - dt.timedelta(days=1), 23, 59)
    rows: list[dict] = []
    for i in range(0, len(syms), ASSET_BATCH):
        batch = syms[i:i + ASSET_BATCH]
        try:
            bars = alpaca.get_bars(batch, start, end, feed="sip", timeframe="1Day")
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"bars batch {i}: {type(e).__name__}: {str(e)[:120]}")
            continue
        grouped: dict[str, list[alpaca.Bar]] = {}
        for b in bars:
            grouped.setdefault(b.symbol, []).append(b)
        for sym, bl in grouped.items():
            st = stats_from_daily(sym, daily_frame(bl))
            if st:
                st.update({k: by_sym[sym][k] for k in ("exchange", "name", "tradable", "marginable", "shortable",
                                                       "easy_to_borrow", "fractionable")})
                rows.append(st)
    res.with_bars = len(rows)
    if not dry_run:
        client = get_client()
        for i in range(0, len(rows), UPSERT_CHUNK):
            chunk = rows[i:i + UPSERT_CHUNK]
            try:
                client.table("symbol_stats").upsert(chunk, on_conflict="symbol").execute()
                res.rows_written += len(chunk)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"upsert chunk {i}: {type(e).__name__}: {str(e)[:120]}")
    else:
        res.rows_written = len(rows)
    res.elapsed_s = round(time.time() - t0, 1)
    return res


# ---------- float / shares outstanding (FMP free tier, survivors only) ----------

def fmp_shares_outstanding(symbol: str) -> int | None:
    """FMP /stable/profile is free (250 calls/day): marketCap / price ≈ shares outstanding (≥ float)."""
    s = get_settings()
    if not s.fmp_api_key:
        return None
    r = httpx.get("https://financialmodelingprep.com/stable/profile", params={"symbol": symbol, "apikey": s.fmp_api_key},
                  timeout=15)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    p = data[0]
    mc, px = p.get("marketCap"), p.get("price")
    if mc and px:
        return int(mc / px)
    return None


def enrich_shares_outstanding(symbols: list[str], *, max_calls: int = 30, max_age_days: int = 7) -> dict[str, int | None]:
    """Fill symbol_stats.shares_outstanding for `symbols` lacking a fresh value. Budgeted (FMP free tier)."""
    if not symbols:
        return {}
    client = get_client()
    have = {r["symbol"]: r for r in (client.table("symbol_stats").select("symbol,shares_outstanding,float_as_of")
                                      .in_("symbol", symbols).execute().data or [])}
    today = alpaca.now_et().date()
    out: dict[str, int | None] = {}
    calls = 0
    for sym in symbols:
        row = have.get(sym) or {}
        fresh = row.get("float_as_of") and (today - dt.date.fromisoformat(row["float_as_of"])).days <= max_age_days
        if row.get("shares_outstanding") and fresh:
            out[sym] = int(row["shares_outstanding"])
            continue
        if calls >= max_calls:
            out[sym] = int(row["shares_outstanding"]) if row.get("shares_outstanding") else None
            continue
        calls += 1
        try:
            so = fmp_shares_outstanding(sym)
        except Exception as e:  # noqa: BLE001
            log.warning("fmp profile %s failed: %s", sym, e)
            so = None
        out[sym] = so
        if so:
            try:
                client.table("symbol_stats").update({"shares_outstanding": so, "float_source": "fmp_shares_outstanding",
                                                     "float_as_of": today.isoformat()}).eq("symbol", sym).execute()
            except Exception as e:  # noqa: BLE001
                log.warning("symbol_stats update %s failed: %s", sym, e)
    return out
