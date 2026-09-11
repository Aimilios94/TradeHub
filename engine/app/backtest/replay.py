"""Scanner replay — the differentiator (plan §13).

A single-chart backtest cannot test a scanner strategy, and is survivorship-biased by construction:
the symbol you chose to chart is one that still exists and that you already know did something. So for
every historical session this module rebuilds the universe the 08:00 screener WOULD have produced from
that morning's information only — prior-day stats computed from bars up to D-1, pre-market volume and
gap from that day's own 04:00-09:30 prints — and hands the survivors to the event loop.

Two of the four mandatory countermeasures live here:

  **1. Survivorship bias.** The replay universe deliberately includes symbols that are no longer active
  (Alpaca lists inactive assets, which is where the delisted low-floats are). Every run reports what
  share of its universe is now inactive; a low-float gapper universe that comes back 100% still-listed
  is not a clean universe, it is a broken one, and `coverage` says so in words.

  **2. Feed mismatch.** The live scanner's relative-volume threshold was calibrated against one feed;
  history is replayed on another. The run carries the median IEX/SIP factor from `v_feed_calibration_30d`
  and scales the threshold rather than comparing a live 5x to a SIP-derived 5x. Where no calibration
  exists yet, the factor is reported as unknown and the threshold is left alone — never quietly assumed
  to be 1.0.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache

import pandas as pd

from app.backtest import bars as bar_store
from app.db import get_client
from app.detectors import get as get_detector
from app.detectors.basic import wilder_atr
from app.models import Condition, ScreenerUniverse, StrategyConfig, WatchlistUniverse
from app.scanner.conditions import evaluate
from app.scanner.run import gap_filter, stage_a_filter

log = logging.getLogger(__name__)

PREMARKET_START = dt.time(4, 0)
PREMARKET_END = dt.time(9, 30)
RELVOL_DETECTORS = frozenset({"relative_volume_premarket"})


@dataclass(slots=True)
class Coverage:
    """Countermeasure 1, reported with every backtest whether or not anyone asked."""

    universe_size: int = 0
    active: int = 0
    inactive: int = 0
    with_daily_bars: int = 0
    without_daily_bars: int = 0
    symbol_days_screened: int = 0
    symbol_days_passed: int = 0
    delisted_pct: float | None = None
    too_clean: bool = False
    warnings: list[str] = field(default_factory=list)

    def finish(self, *, expect_delisted: bool) -> Coverage:
        total = self.active + self.inactive
        self.delisted_pct = round(self.inactive / total * 100, 2) if total else None
        if expect_delisted and (self.delisted_pct or 0) < 1.0:
            self.too_clean = True
            self.warnings.append(
                "the replay universe is almost entirely still-listed symbols. For a low-float strategy "
                "that is not a clean result, it is a missing-data result: the names that went to zero "
                "are the ones absent, so every number below is flattered by survivorship."
            )
        if self.without_daily_bars:
            self.warnings.append(
                f"{self.without_daily_bars} symbol(s) in the universe returned no daily history and were "
                "screened out of every session rather than assumed to pass")
        return self

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class DayCandidate:
    symbol: str
    day: dt.date
    metrics: dict
    day_levels: dict
    atr: float | None
    score: float
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------- assets ----------

@lru_cache(maxsize=1)
def all_assets(include_inactive: bool = True) -> list[dict]:
    """Every US equity Alpaca knows about, active AND inactive. The inactive ones are the delisted
    names whose absence is what makes a low-float backtest lie."""
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.trading.requests import GetAssetsRequest

    from app.scanner.universe import LISTED_EXCHANGES, _trading_client

    client = _trading_client()
    out: list[dict] = []
    wanted = [AssetStatus.ACTIVE] + ([AssetStatus.INACTIVE] if include_inactive else [])
    for status in wanted:
        try:
            assets = client.get_all_assets(GetAssetsRequest(status=status, asset_class=AssetClass.US_EQUITY))
        except Exception as e:  # noqa: BLE001
            log.warning("could not list %s assets: %s", status, e)
            continue
        for a in assets:
            ex = a.exchange.value if a.exchange else ""
            if ex not in LISTED_EXCHANGES:
                continue
            out.append({"symbol": a.symbol, "exchange": ex, "name": a.name,
                        "status": status.value, "active": status == AssetStatus.ACTIVE,
                        "shortable": bool(a.shortable), "easy_to_borrow": bool(a.easy_to_borrow)})
    return out


# ---------- calibration ----------

def feed_calibration(window: str = "premarket") -> dict:
    """Countermeasure 2. The median IEX/SIP volume factor logged nightly since Phase 1."""
    try:
        rows = get_client().table("v_feed_calibration_30d").select("*").execute().data or []
    except Exception as e:  # noqa: BLE001
        return {"factor": None, "source": "unavailable", "note": f"calibration view unreachable: {e}"}
    match = [r for r in rows if (r.get("window") or r.get("window_name")) == window]
    pool = match or rows
    vals = [float(r["median_factor"]) for r in pool if r.get("median_factor") is not None]
    if not vals:
        return {"factor": None, "source": "empty",
                "note": "no IEX/SIP calibration logged yet — the live threshold is used unchanged, "
                        "and the mismatch is a known unquantified risk (plan §13 countermeasure 2)"}
    f = round(sum(vals) / len(vals), 4)
    return {"factor": f, "source": "v_feed_calibration_30d", "window": window, "samples": len(vals),
            "note": f"a live IEX threshold is multiplied by {f} to mean the same thing on SIP history"}


def scale_relvol(conds: list[Condition], factor: float | None) -> tuple[list[Condition], list[str]]:
    """Rescale relative-volume thresholds for the history's feed. No factor → untouched and said so."""
    if not factor or factor <= 0:
        return conds, []
    out, notes = [], []
    for c in conds:
        if c.detector in RELVOL_DETECTORS and isinstance(c.value, (int, float)):
            nc = c.model_copy(update={"value": round(float(c.value) * factor, 4)})
            notes.append(f"{c.detector} {c.op} {c.value} → {nc.value} (IEX↔SIP factor {factor})")
            out.append(nc)
        else:
            out.append(c)
    return out, notes


# ---------- per-day stats, computed from information available that morning ----------

def stats_as_of(daily: pd.DataFrame, day: dt.date) -> dict | None:
    """The `symbol_stats` row the nightly job WOULD have written the evening before `day`."""
    hist = daily[daily.index.date < day] if not daily.empty else daily
    if hist is None or len(hist) < 2:
        return None
    last = hist.iloc[-1]
    t20, t60 = hist.tail(20), hist.tail(60)
    return {
        "prev_close": float(last["close"]), "prev_high": float(last["high"]), "prev_low": float(last["low"]),
        "prev_open": float(last["open"]), "prev_volume": int(last["volume"]),
        "adv20": round(float(t20["volume"].mean())), "adv50": round(float(hist.tail(50)["volume"].mean())),
        "atr14": wilder_atr(hist, 14),
        "high_20d": float(t20["high"].max()), "low_20d": float(t20["low"].min()),
        "high_60d": float(t60["high"].max()), "low_60d": float(t60["low"].min()),
        "sessions": len(hist), "tradable": True,
    }


def premarket_baseline(daily_pm: dict[dt.date, int], day: dt.date, n: int = 14) -> tuple[float | None, int]:
    """Average pre-market volume over the previous `n` sessions — the same-feed baseline, rebuilt from
    the replay's own bars rather than from the live table (which only holds recent days)."""
    prior = [v for d, v in sorted(daily_pm.items()) if d < day][-n:]
    if not prior:
        return None, 0
    return round(sum(prior) / len(prior), 2), len(prior)


def day_levels_for(pm: pd.DataFrame | None, stats: dict, session: pd.DataFrame | None,
                   opening_range_minutes: int) -> dict:
    """Levels an entry rule may reference: pre-market extremes, prior day, the opening range."""
    lv: dict = {"prior_high": stats.get("prev_high"), "prior_low": stats.get("prev_low"),
                "prior_close": stats.get("prev_close")}
    if pm is not None and not pm.empty:
        lv["premarket_high"] = float(pm["high"].max())
        lv["premarket_low"] = float(pm["low"].min())
        lv["premarket_volume"] = int(pm["volume"].sum())
        lv["premarket_prints"] = len(pm)
    if session is not None and not session.empty:
        t = pd.Series([x.time() for x in session.index], index=session.index)
        end = (dt.datetime.combine(dt.date(2000, 1, 1), PREMARKET_END)
               + dt.timedelta(minutes=opening_range_minutes)).time()
        win = session[(t >= PREMARKET_END) & (t < end)]
        if not win.empty:
            lv["opening_range"] = {"high": float(win["high"].max()), "low": float(win["low"].min()),
                                   "minutes": opening_range_minutes, "bars": len(win)}
    return lv


# ---------- the screen ----------

def screen_day(cfg: StrategyConfig, symbol: str, day: dt.date, *, daily: pd.DataFrame,
               pm: pd.DataFrame | None, session: pd.DataFrame | None, baseline_avg: float | None,
               baseline_days: int, day_conds: list[Condition], opening_range_minutes: int) -> DayCandidate | None:
    """Run the day-level hard gates for one symbol on one historical morning."""
    stats = stats_as_of(daily, day)
    if stats is None:
        return None
    params = cfg.universe.params if isinstance(cfg.universe, ScreenerUniverse) else {}
    if params:
        kept, _ = stage_a_filter(params, {symbol: stats})
        if not kept:
            return None
    price = float(pm["close"].iloc[-1]) if pm is not None and not pm.empty else stats["prev_close"]
    ctx = {"symbol": symbol, "stats": stats, "daily": daily[daily.index.date < day], "prev_close": stats["prev_close"],
           "price": price}
    results = {}
    for name, extra in (("gap_pct", {}), ("premarket_high_low", {}), ("atr", {"period": 14}),
                        ("adv", {"lookback_days": 20}), ("prior_day_levels", {})):
        try:
            results[name] = get_detector(name)(pm, **ctx, **extra)
        except Exception as e:  # noqa: BLE001
            log.debug("%s failed for %s on %s: %s", name, symbol, day, e)
    gap = results["gap_pct"].value if "gap_pct" in results else None
    if params and not gap_filter(params, gap):
        return None
    atr_val = results["atr"].value if results.get("atr") else stats.get("atr14")

    failed, pending = [], []
    for c in day_conds:
        p = dict(c.params)
        if c.detector in RELVOL_DETECTORS:
            p.update({"baseline_avg": baseline_avg, "baseline_days_used": baseline_days,
                      "baseline_days": p.get("baseline_days", 14), "feed": "sip",
                      "adv_value": results["adv"].value if results.get("adv") else stats.get("adv20")})
        elif c.detector == "distance_to_level_atr":
            levels = {k: stats.get(k) for k in ("prev_high", "prev_low", "prev_close", "high_20d",
                                                "low_20d", "high_60d", "low_60d")}
            p.update({"levels": levels, "atr_value": atr_val, "price": price})
        try:
            res = get_detector(c.detector)(pm, **ctx, **p)
        except Exception as e:  # noqa: BLE001
            log.debug("%s failed for %s on %s: %s", c.detector, symbol, day, e)
            pending.append(c.detector)
            continue
        out = evaluate(c, res)
        if out.status == "fail":
            failed.append(c.label or f"{c.detector} {c.op} {c.value}")
        elif out.status == "pending":
            pending.append(c.label or c.detector)
        if c.detector in RELVOL_DETECTORS:
            results["relative_volume_premarket"] = res
    if failed:
        return None

    rv = results.get("relative_volume_premarket")
    pmhl = results.get("premarket_high_low")
    metrics = {
        "gap_pct": gap, "price": price, "prev_close": stats["prev_close"], "atr14": atr_val,
        "adv20": stats["adv20"], "relvol": rv.value if rv else None,
        "premarket_volume": pmhl.fields.get("volume") if pmhl else 0,
        "premarket_prints": pmhl.fields.get("print_count") if pmhl else 0,
        "baseline_days_used": baseline_days,
    }
    score = (metrics["relvol"] or 0) * 10 + abs(gap or 0)
    return DayCandidate(symbol=symbol, day=day, metrics=metrics, atr=atr_val, score=round(score, 3),
                        pending=pending,
                        day_levels=day_levels_for(pm, stats, session, opening_range_minutes))


def split_session(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pre-market 04:00-09:30, regular 09:30-16:00) for one day's intraday frame."""
    if df is None or df.empty:
        return df, df
    t = pd.Series([x.time() for x in df.index], index=df.index)
    return df[(t >= PREMARKET_START) & (t < PREMARKET_END)], df[t >= PREMARKET_END]


def universe_symbols(cfg: StrategyConfig, *, cap: int, include_inactive: bool) -> tuple[list[str], Coverage]:
    """The symbol pool the replay screens each day, plus the coverage stats that judge it."""
    cov = Coverage()
    if isinstance(cfg.universe, WatchlistUniverse):
        syms = list(cfg.universe.symbols)
        cov.universe_size = cov.active = len(syms)
        return syms, cov
    assets = all_assets(include_inactive)
    cov.universe_size = len(assets)
    cov.active = sum(1 for a in assets if a["active"])
    cov.inactive = sum(1 for a in assets if not a["active"])
    syms = [a["symbol"] for a in assets]
    if cap and len(syms) > cap:
        # keep the delisted names in proportion — capping to "the first N alphabetically" would quietly
        # reintroduce the very bias this universe exists to avoid
        active = [a["symbol"] for a in assets if a["active"]]
        dead = [a["symbol"] for a in assets if not a["active"]]
        share = cov.inactive / cov.universe_size if cov.universe_size else 0
        n_dead = int(cap * share)
        syms = sorted(dead[:n_dead] + active[:cap - n_dead])
        cov.warnings.append(f"universe capped at {cap} of {cov.universe_size} symbols "
                            f"({n_dead} of them inactive, keeping the delisted share intact)")
    return syms, cov


def load_bar_store(symbols: list[str], start: dt.date, end: dt.date, *, timeframe: str,
                   feed: str = "sip", use_cache: bool = True) -> tuple[dict[str, pd.DataFrame], dict]:
    return bar_store.get(symbols, start, end, timeframe=timeframe, feed=feed, use_cache=use_cache)
