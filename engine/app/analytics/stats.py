"""The analytics module (plan §14, the owner's T3 spec). Pandas over `journal`.

Overall performance, breakdowns by strategy / setup / time block / weekday / ticker / regime, and
pattern detection (best and worst hour, best and worst setup, losing streaks). Output feeds the weekly
autopsy (template 06) and, in Phase 9, the strategy-library UI — both read these same numbers.

Two rules run through all of it:

  * **Win rate is displayed, never a gate** (plan §20). A 45% win rate with 3:1 winners beats 85% with
    rare huge losers, so expectancy after costs is the number that decides anything.
  * **Small samples are labelled, not hidden.** Every breakdown carries its trade count, and
    `sufficient` says whether it clears `promotion_criteria.min_trades_*`. With three trades the honest
    answer is "not enough to say", and the weekly review is told so rather than left to infer it.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from app.db import get_client
from app.marketdata import repo as md_repo

log = logging.getLogger(__name__)

NUMERIC = ("pnl_gross", "pnl_net", "fees", "spread_cost", "r_multiple", "qty", "entry_price", "exit_price")


def load(since: dt.date | None = None, until: dt.date | None = None,
         strategy_id: str | None = None) -> pd.DataFrame:
    """Closed journal rows as a DataFrame, ET-localised."""
    q = get_client().table("journal").select("*").not_.is_("exit_time", "null")
    if since:
        q = q.gte("exit_time", f"{since.isoformat()}T00:00:00")
    if until:
        q = q.lte("exit_time", f"{until.isoformat()}T23:59:59")
    if strategy_id:
        q = q.eq("strategy_id", strategy_id)
    rows = q.execute().data or []
    return to_frame(rows)


def to_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in NUMERIC:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("entry_time", "exit_time"):
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_convert("America/New_York")
    if "entry_time" in df:
        df["weekday"] = df["entry_time"].dt.day_name()
        df["trade_date"] = df["entry_time"].dt.date
    return df


def _agg(df: pd.DataFrame) -> dict:
    """The stat block used for the whole set and for every breakdown."""
    if df.empty:
        return {"trades": 0}
    net = df["pnl_net"].dropna()
    wins, losses = net[net > 0], net[net < 0]
    avg_win = round(float(wins.mean()), 4) if len(wins) else 0.0
    avg_loss = round(float(losses.mean()), 4) if len(losses) else 0.0
    gross_win, gross_loss = float(wins.sum()), abs(float(losses.sum()))
    r = df["r_multiple"].dropna()
    return {
        "trades": len(df),
        "winners": len(wins), "losers": len(losses),
        "win_rate_pct": round(len(wins) / len(net) * 100, 2) if len(net) else None,
        "total_pnl": round(float(net.sum()), 4),
        "avg_pnl": round(float(net.mean()), 4) if len(net) else None,
        "avg_win": avg_win, "avg_loss": avg_loss,
        # the largest WIN must come from the winners, not from "the least bad loss"
        "largest_win": round(float(wins.max()), 4) if len(wins) else None,
        "largest_loss": round(float(losses.min()), 4) if len(losses) else None,
        # expectancy in dollars per trade, AFTER every cost: fees are deducted in pnl_net and the
        # spread is already inside the fill prices that produced pnl_gross
        "expectancy": round(float(net.mean()), 4) if len(net) else None,
        "expectancy_r": round(float(r.mean()), 3) if len(r) else None,
        # None, never inf: a sample with no losing trades has no profit factor, and `float("inf")` is
        # not JSON — it silently killed a `backtests` write and would kill a weekly-review prompt the
        # first flawless week. `losers` below already says why it is None.
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "total_r": round(float(r.sum()), 3) if len(r) else None,
        "fees": round(float(df["fees"].fillna(0).sum()), 4),
        "spread_cost": round(float(df["spread_cost"].fillna(0).sum()), 4),
        "max_drawdown": max_drawdown(net),
        "note": "win rate is information, never a promotion gate (plan §20)",
    }


def max_drawdown(net: pd.Series) -> float | None:
    """Worst peak-to-trough of the cumulative net P&L curve, in dollars.

    The curve starts at zero, so a losing first trade counts as a drawdown. Measuring from the first
    trade instead reports 0.00 for an account that has only ever lost money.
    """
    if net.empty:
        return None
    curve = pd.concat([pd.Series([0.0]), net.cumsum().reset_index(drop=True)], ignore_index=True)
    return round(float((curve - curve.cummax()).min()), 4)


def breakdown(df: pd.DataFrame, column: str, min_trades: int) -> list[dict]:
    if df.empty or column not in df:
        return []
    out = []
    for key, group in df.groupby(df[column].fillna("(none)"), dropna=False):
        block = _agg(group) | {column: str(key), "sufficient": len(group) >= min_trades}
        out.append(block)
    return sorted(out, key=lambda b: -(b.get("total_pnl") or 0))


def streaks(df: pd.DataFrame) -> dict:
    """Longest consecutive winning and losing runs, in trade order."""
    if df.empty:
        return {"longest_win_streak": 0, "longest_loss_streak": 0, "current_streak": 0}
    ordered = df.sort_values("entry_time")["pnl_net"].dropna()
    best = worst = run = 0
    for value in ordered:
        step = 1 if value > 0 else (-1 if value < 0 else 0)
        run = step if (run == 0 or (run > 0) != (step > 0)) else run + step
        best, worst = max(best, run), min(worst, run)
    return {"longest_win_streak": int(best), "longest_loss_streak": int(abs(worst)),
            "current_streak": int(run)}


def patterns(report: dict, min_trades: int) -> list[str]:
    """Plain statements a human can act on, only where the sample supports them."""
    flags: list[str] = []

    def best_worst(blocks: list[dict], label: str, key: str):
        usable = [b for b in blocks if b.get("sufficient") and b.get("expectancy") is not None]
        if len(usable) < 2:
            return
        best = max(usable, key=lambda b: b["expectancy"])
        worst = min(usable, key=lambda b: b["expectancy"])
        if best[key] == worst[key]:
            return
        flags.append(f"best {label}: {best[key]} ({best['expectancy']:+.2f}/trade over {best['trades']})")
        if worst["expectancy"] < 0:
            flags.append(f"worst {label}: {worst[key]} ({worst['expectancy']:+.2f}/trade over "
                         f"{worst['trades']}) — consider avoiding it")

    best_worst(report.get("by_time_block", []), "time block", "time_block")
    best_worst(report.get("by_setup", []), "setup", "setup_type")
    best_worst(report.get("by_weekday", []), "weekday", "weekday")
    best_worst(report.get("by_strategy", []), "strategy", "strategy")

    overall = report.get("overall", {})
    if overall.get("trades", 0) < min_trades:
        flags.append(f"only {overall.get('trades', 0)} closed trades — below the {min_trades} needed "
                     f"to judge anything; treat every number here as provisional")
    if (overall.get("expectancy") or 0) < 0 and overall.get("trades", 0) >= min_trades:
        flags.append(f"expectancy is negative after costs ({overall['expectancy']:+.2f}/trade) — the "
                     f"system is losing money net of fees and spread")
    costs = (overall.get("fees") or 0) + (overall.get("spread_cost") or 0)
    gross = overall.get("total_pnl", 0) + costs
    if costs > 0 and gross > 0 and costs > gross * 0.3:
        flags.append(f"costs ({costs:.2f}) are eating {costs / gross * 100:.0f}% of gross profit")
    s = report.get("streaks", {})
    if s.get("longest_loss_streak", 0) >= 4:
        flags.append(f"a {s['longest_loss_streak']}-trade losing streak occurred — check the autopsies "
                     f"around it for tilt")
    return flags


def min_sample() -> tuple[int, int]:
    crit = md_repo.fetch_setting("promotion_criteria", {}) or {}
    return int(crit.get("min_trades_day", 100)), int(crit.get("min_trades_swing", 40))


def report(*, since: dt.date | None = None, until: dt.date | None = None,
           df: pd.DataFrame | None = None) -> dict:
    """The full §14 analytics block. Safe on an empty journal — it says so instead of failing."""
    frame = df if df is not None else load(since, until)
    min_day, _min_swing = min_sample()

    if frame.empty:
        return {"period": {"since": since.isoformat() if since else None,
                           "until": until.isoformat() if until else None},
                "overall": {"trades": 0}, "by_strategy": [], "by_time_block": [], "by_weekday": [],
                "by_setup": [], "by_symbol": [], "by_regime": [], "streaks": {},
                "flags": ["no closed trades in this period"], "min_trades_day": min_day}

    codes = strategy_codes()
    frame = frame.copy()
    frame["strategy"] = frame["strategy_id"].map(codes).fillna("(unlinked)")

    out = {
        "period": {"since": since.isoformat() if since else str(frame["trade_date"].min()),
                   "until": until.isoformat() if until else str(frame["trade_date"].max())},
        "overall": _agg(frame),
        "by_strategy": breakdown(frame, "strategy", min_day),
        "by_time_block": breakdown(frame, "time_block", min_day),
        "by_weekday": breakdown(frame, "weekday", min_day),
        "by_setup": breakdown(frame, "setup_type", min_day),
        "by_symbol": breakdown(frame, "symbol", min_day)[:20],
        "by_regime": breakdown(frame, "market_regime", min_day),
        "streaks": streaks(frame),
        "min_trades_day": min_day,
    }
    out["flags"] = patterns(out, min_day)
    return out


def strategy_codes() -> dict[str, str]:
    try:
        return {r["id"]: r["code"] for r in
                (get_client().table("strategy_configs").select("id,code").execute().data or [])}
    except Exception:  # noqa: BLE001
        return {}


def written_summary(rep: dict) -> str:
    """The prose §14 asks for — what the numbers say, in sentences."""
    o = rep.get("overall", {})
    if not o.get("trades"):
        return "No closed trades in this period."
    lines = [
        (f"{o['trades']} closed trades between {rep['period']['since']} and {rep['period']['until']}: "
         f"{o['winners']} winners, {o['losers']} losers ({o['win_rate_pct']}% win rate — information only)."),
        (f"Net {o['total_pnl']:+.2f} after {o['fees']:.2f} in fees and {o['spread_cost']:.2f} of spread, "
         f"an expectancy of {o['expectancy']:+.2f} per trade"
         + (f" ({o['expectancy_r']:+.2f}R)." if o.get("expectancy_r") is not None else ".")),
    ]
    if o.get("profit_factor") is not None:
        lines.append(f"Profit factor {o['profit_factor']}, worst drawdown {o['max_drawdown']:+.2f}.")
    if rep.get("flags"):
        lines.append("Flags: " + "; ".join(rep["flags"]) + ".")
    return " ".join(lines)
