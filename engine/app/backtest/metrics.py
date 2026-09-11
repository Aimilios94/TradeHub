"""Backtest metrics, the promotion verdict, and the overfitting guards (plan §13, §20).

Backtest trades are shaped like `journal` rows on purpose, so the numbers come out of
`app.analytics.stats` — the same expectancy, profit factor and drawdown the live journal reports.
A backtest expectancy and a paper expectancy are then the same measurement, which is the only way
the promotion workflow (§13) means anything.

Countermeasure 4, overfitting guards, lives here:
  * **Minimum sample sizes.** Below `promotion_criteria.min_trades_*` the verdict is `inconclusive`.
    Not "fail" — an untested strategy has not failed — and never `pass`.
  * **Walk-forward split.** The range is cut in two; parameters that only work in the first half are
    visible as a collapse in the second. A result whose out-of-sample half disagrees with the
    in-sample half is flagged, in words, on the result itself.
  * **Win rate is never a gate** (§20). It is computed, displayed, and ignored by `verdict`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from app.analytics import stats as A


def to_frame(trades: list[dict]) -> pd.DataFrame:
    """Backtest trades → the journal-shaped frame `app.analytics.stats` consumes."""
    if not trades:
        return pd.DataFrame()
    df = pd.DataFrame(trades)
    for col in ("pnl_gross", "pnl_net", "fees", "spread_cost", "r_multiple", "qty", "entry_price",
                "exit_price"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("entry_time", "exit_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_convert("America/New_York")
    df["weekday"] = df["entry_time"].dt.day_name()
    df["trade_date"] = df["entry_time"].dt.date
    df["year"] = df["entry_time"].dt.year
    df["month"] = df["entry_time"].dt.strftime("%Y-%m")
    return df.sort_values("entry_time").reset_index(drop=True)


def compute(trades: list[dict], *, min_trades: int, regimes: dict[dt.date, str] | None = None) -> dict:
    df = to_frame(trades)
    if df.empty:
        return {"trades": 0, "note": "no trades were taken — see `diagnostics` for where the funnel stopped"}
    if regimes:
        df["market_regime"] = df["trade_date"].map(lambda d: regimes.get(d, "unknown"))
    out: dict[str, Any] = A._agg(df)
    out |= {
        "sufficient": len(df) >= min_trades,
        "min_trades_required": min_trades,
        "by_year": A.breakdown(df, "year", min_trades),
        "by_month": A.breakdown(df, "month", min_trades),
        "by_weekday": A.breakdown(df, "weekday", min_trades),
        "by_symbol": A.breakdown(df, "symbol", min_trades),
        "by_exit_reason": A.breakdown(df, "exit_reason", min_trades),
        "by_direction": A.breakdown(df, "direction", min_trades),
        "streaks": A.streaks(df),
        "avg_bars_held": round(float(df["bars_held"].mean()), 2) if "bars_held" in df else None,
        "partial_fills": int(df["partial_fill"].sum()) if "partial_fill" in df else 0,
        "ambiguous_bars": int(df["ambiguous_bars"].sum()) if "ambiguous_bars" in df else 0,
        "first_trade": str(df["entry_time"].iloc[0]), "last_trade": str(df["entry_time"].iloc[-1]),
    }
    if regimes:
        out["by_regime"] = A.breakdown(df, "market_regime", min_trades)
    if out["ambiguous_bars"]:
        out.setdefault("flags", []).append(
            f"{out['ambiguous_bars']} bar(s) touched the stop and the target in the same bar and were "
            "booked as stops. A large count here means the timeframe is too coarse for this strategy, "
            "not that the strategy is bad.")
    if not out["sufficient"]:
        out.setdefault("flags", []).append(
            f"{len(df)} trades is below the {min_trades} this style needs before any conclusion "
            "(plan §20). Treat every number here as provisional.")
    return out


# ---------- walk-forward ----------

def walk_forward(trades: list[dict], *, min_trades: int, splits: int = 2) -> dict:
    """Cut the trade sequence into equal halves (or `splits` parts) by time and compare.

    This is the cheap, honest version of the guard: it cannot prove a parameter was not tuned on the
    whole range, but it does show when performance lived entirely in one half.
    """
    df = to_frame(trades)
    if df.empty or len(df) < splits * 2:
        return {"splits": splits, "usable": False,
                "note": "too few trades to split — a walk-forward on a handful of trades says nothing"}
    parts = []
    size = len(df) // splits
    for i in range(splits):
        lo = i * size
        hi = len(df) if i == splits - 1 else (i + 1) * size
        seg = df.iloc[lo:hi]
        block = A._agg(seg)
        parts.append({"part": i + 1, "from": str(seg["entry_time"].iloc[0]), "to": str(seg["entry_time"].iloc[-1]),
                      "trades": len(seg), "expectancy": block["expectancy"],
                      "profit_factor": block["profit_factor"], "win_rate_pct": block["win_rate_pct"],
                      "total_pnl": block["total_pnl"], "sufficient": len(seg) >= min_trades // splits})
    exps = [p["expectancy"] for p in parts if p["expectancy"] is not None]
    consistent = bool(exps) and all(e > 0 for e in exps)
    flags: list[str] = []
    if exps and any(e > 0 for e in exps) and any(e <= 0 for e in exps):
        good = [p["part"] for p in parts if (p["expectancy"] or 0) > 0]
        flags.append(f"positive expectancy only in part(s) {good} of {splits} — the edge did not hold "
                     "across the whole range, which is what an over-fitted parameter looks like")
    return {"splits": splits, "usable": True, "parts": parts, "consistent": consistent, "flags": flags}


# ---------- the verdict ----------

def verdict(metrics: dict, criteria: dict, *, style: str, ab: dict | None = None,
            walk: dict | None = None) -> tuple[str, dict]:
    """pass | fail | inconclusive against `promotion_criteria` (plan §20). Win rate is not consulted."""
    min_trades = int(criteria.get("min_trades_swing" if style == "swing" else "min_trades_day", 100))
    checks: list[dict] = []

    def check(name: str, ok: bool | None, detail: str) -> None:
        checks.append({"check": name, "result": "unknown" if ok is None else ("pass" if ok else "fail"),
                       "detail": detail})

    n = metrics.get("trades", 0)
    # Deliberately `unknown`, not `fail`, when the sample is short. An untested strategy has not failed
    # — plan §20 says no conclusion below the minimum, and "not enough evidence" is a different
    # statement from "this loses money". It still cannot be promoted: only `pass` promotes.
    check("sample size", True if n >= min_trades else None,
          f"{n} trades vs {min_trades} required for a {style} strategy"
          + ("" if n >= min_trades else " — too few to conclude anything (plan §20)"))

    exp = metrics.get("expectancy")
    if criteria.get("require_positive_expectancy_after_costs", True):
        check("expectancy after costs", None if exp is None else exp > 0,
              "no trades" if exp is None else f"{exp:+.4f} per trade, net of fees")

    pf = metrics.get("profit_factor")
    min_pf = float(criteria.get("min_profit_factor", 1.5))
    if pf is None and metrics.get("losers") == 0 and metrics.get("winners", 0) > 0:
        # No losing trades at all: the ratio is undefined, but the criterion ("at least min_pf") is
        # satisfied a fortiori. Calling it unknown would make a flawless sample unpassable, which is a
        # different kind of wrong answer — though a real strategy with zero losers over a sufficient
        # sample is far more likely to be a data problem, and the sample-size check still applies.
        check("profit factor", True, f"no losing trades in {n}, so the ratio is undefined but clears {min_pf}")
    else:
        check("profit factor", None if pf is None else pf >= min_pf,
              "not measurable — no trades" if pf is None else f"{pf} vs {min_pf} required")

    dd = metrics.get("max_drawdown")
    max_dd_pct = float(criteria.get("max_drawdown_pct", 15.0))
    equity = float(criteria.get("_equity", 0) or 0)
    if dd is None or not equity:
        check("max drawdown", None, "needs the account equity the run was sized against")
    else:
        dd_pct = abs(dd) / equity * 100
        check("max drawdown", dd_pct <= max_dd_pct, f"{dd_pct:.2f}% of equity vs {max_dd_pct}% allowed")

    if criteria.get("must_beat_current_config_ab", True):
        if ab and ab.get("expectancy_delta") is not None:
            check("beats the config it is compared against", ab["expectancy_delta"] > 0,
                  f"expectancy {ab['expectancy_delta']:+.4f} vs the comparison run")
        else:
            check("beats the config it is compared against", None,
                  "no A/B comparison was run — pass `ab_against` to settle this")

    if walk and walk.get("usable"):
        check("holds across the range (walk-forward)", walk.get("consistent"),
              "; ".join(walk.get("flags") or []) or "expectancy positive in every part")

    fails = [c for c in checks if c["result"] == "fail"]
    unknowns = [c for c in checks if c["result"] == "unknown"]
    # Precedence: too few trades beats everything. Plan §20 forbids a conclusion below the minimum
    # sample, and that includes the conclusion "this fails" — ten losing trades is noise, not evidence.
    if n < min_trades:
        v = "inconclusive"
    elif fails:
        v = "fail"
    elif unknowns:
        v = "inconclusive"
    else:
        v = "pass"
    return v, {
        "verdict": v, "checks": checks,
        "failed": [c["check"] for c in fails], "unknown": [c["check"] for c in unknowns],
        "win_rate_pct": metrics.get("win_rate_pct"),
        "note": "win rate is reported for information and is never a gate (plan §20). "
                "Unknown is never green: an unanswered check makes the verdict inconclusive, not pass.",
    }


# ---------- A/B ----------

def compare(a: dict, b: dict, *, label_a: str, label_b: str) -> dict:
    """Head-to-head on the numbers that decide promotion — not on win rate."""
    def g(m: dict, k: str):
        v = m.get(k)
        return None if v is None else float(v)

    ea, eb = g(a, "expectancy"), g(b, "expectancy")
    pa, pb = g(a, "profit_factor"), g(b, "profit_factor")
    winner = None
    if ea is not None and eb is not None and ea != eb:
        winner = label_a if ea > eb else label_b
    both_thin = (a.get("trades", 0) < 30) or (b.get("trades", 0) < 30)
    return {
        "a": label_a, "b": label_b,
        "trades": {label_a: a.get("trades", 0), label_b: b.get("trades", 0)},
        "expectancy": {label_a: ea, label_b: eb},
        "expectancy_delta": None if (ea is None or eb is None) else round(ea - eb, 4),
        "profit_factor": {label_a: pa, label_b: pb},
        "total_pnl": {label_a: g(a, "total_pnl"), label_b: g(b, "total_pnl")},
        "win_rate_pct": {label_a: g(a, "win_rate_pct"), label_b: g(b, "win_rate_pct")},
        "winner": winner,
        "confident": bool(winner) and not both_thin,
        "note": "expectancy after costs decides; win rate is shown but not used. "
                + ("Both samples are small — this is a hint, not a verdict." if both_thin else ""),
    }


# ---------- market regime ----------

def regime_map(spy_daily: pd.DataFrame, *, fast: int = 20, slow: int = 50) -> dict[dt.date, str]:
    """Tag each session uptrend / downtrend / chop from SPY's own moving averages (plan §14, §30).

    Deliberately crude and deliberately deterministic: the point is to see whether a strategy only
    works in one market, not to classify regimes well.
    """
    if spy_daily is None or spy_daily.empty:
        return {}
    c = spy_daily["close"]
    f, s = c.ewm(span=fast, adjust=False).mean(), c.ewm(span=slow, adjust=False).mean()
    spread = (f - s) / s * 100
    out: dict[dt.date, str] = {}
    for ts, v in spread.items():
        d = ts.date() if hasattr(ts, "date") else ts
        out[d] = "uptrend" if v > 0.5 else ("downtrend" if v < -0.5 else "chop")
    return out
