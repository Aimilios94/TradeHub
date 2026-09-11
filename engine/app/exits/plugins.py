"""The nine exit plugins (plan §9). Deterministic, no I/O.

Division of labour, and the reason the backtester cannot flatter itself:
  * a plugin declares LEVELS (`initial`) and REACTIONS at a bar close (`on_bar`);
  * the caller decides what an intrabar touch means. In the backtester that is `app/backtest/fills.py`,
    which resolves a bar where both the stop and the target were touched AGAINST the trade.

`on_bar` is handed bars up to and including the current one and never more, so a plugin physically
cannot see the future. State that must survive between bars lives in the `state` dict the caller owns.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from app.detectors.indicators import stoch_frame
from app.exits import ExitAction, ExitOrder, register


@dataclass
class PositionView:
    """What an exit plugin is allowed to know about the open position."""

    symbol: str
    side: Literal["long", "short"]
    entry_price: float
    qty: int
    entry_index: int                      # position of the entry bar inside the frame
    stop: float | None = None             # the plan's stop (plugins may tighten it, never widen it)
    target: float | None = None
    risk_per_share: float | None = None
    partials_taken: int = 0
    opened_at: dt.datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def long(self) -> bool:
        return self.side == "long"

    def tighter(self, old: float | None, new: float) -> float:
        """A stop only ever moves toward price. Plan rule: the Guardian may be tightened, never loosened."""
        if old is None:
            return new
        return max(old, new) if self.long else min(old, new)


def _last(bars: pd.DataFrame) -> pd.Series:
    return bars.iloc[-1]


def _et_time(ts) -> dt.time:
    return ts.time() if hasattr(ts, "time") else dt.time(0, 0)


def _parse_hhmm(v: str) -> dt.time:
    hh, mm = v.split(":")
    return dt.time(int(hh), int(mm))


# ---------- 1. bracket_oco ----------

@register("bracket_oco")
class BracketOCO:
    """Stop-loss + take-profit as one OCO. Regular hours only — Alpaca rejects brackets in extended
    hours, and a stop accepted pre-market silently never triggers (docs/BROKER_RULES.md R2)."""

    extended_hours_allowed = False

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, *, stop: str = "plan", target: str = "plan",
                r_multiple: float = 2.0, atr_multiple: float = 1.0, atr_value: float | None = None,
                **_: Any) -> ExitOrder | None:
        sl = pos.stop
        if stop == "atr_multiple":
            if atr_value is None:
                return None
            sl = pos.entry_price - atr_multiple * atr_value if pos.long else pos.entry_price + atr_multiple * atr_value
        if sl is None:
            return None
        risk = abs(pos.entry_price - sl)
        tp = pos.target
        if target in ("r_multiple", "atr_next_push") or tp is None:
            if target == "atr_next_push":
                if atr_value is None:
                    tp = None
                else:
                    step = atr_multiple * atr_value
                    tp = pos.entry_price + step if pos.long else pos.entry_price - step
            elif risk > 0:
                tp = pos.entry_price + r_multiple * risk if pos.long else pos.entry_price - r_multiple * risk
        return ExitOrder(kind="stop", price=round(sl, 4), qty_pct=100.0,
                         extended_hours=False) if tp is None else ExitOrder(
            kind="stop_limit", price=round(sl, 4), qty_pct=100.0, extended_hours=False)

    @staticmethod
    def levels(pos: PositionView, bars: pd.DataFrame, *, stop: str = "plan", target: str = "plan",
               r_multiple: float = 2.0, atr_multiple: float = 1.0, atr_value: float | None = None,
               **_: Any) -> dict:
        """The (stop, target) pair the OCO rests at — what the fill model checks each bar."""
        sl = pos.stop
        if stop == "atr_multiple" and atr_value is not None:
            sl = pos.entry_price - atr_multiple * atr_value if pos.long else pos.entry_price + atr_multiple * atr_value
        tp = pos.target
        risk = abs(pos.entry_price - sl) if sl is not None else None
        if tp is None and target == "r_multiple" and risk:
            tp = pos.entry_price + r_multiple * risk if pos.long else pos.entry_price - r_multiple * risk
        if target == "atr_next_push" and atr_value is not None:
            step = atr_multiple * atr_value
            tp = pos.entry_price + step if pos.long else pos.entry_price - step
        return {"stop": sl, "target": tp}

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, **_: Any) -> ExitAction | None:
        return None   # a resting OCO does nothing at a bar close; the fill model owns the touch


# ---------- 2. trailing_pct ----------

@register("trailing_pct")
class TrailingPct:
    """Percentage trail off the high-water mark reached since entry. Never moves against the trade."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, *, trail_pct: float = 4.5, **_: Any) -> ExitOrder | None:
        return ExitOrder(kind="trailing", trail_pct=trail_pct, qty_pct=100.0)

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, *, trail_pct: float = 4.5,
               **_: Any) -> ExitAction | None:
        seg = bars.iloc[pos.entry_index:]
        if seg.empty:
            return None
        if pos.long:
            hw = float(seg["high"].max())
            new = hw * (1 - trail_pct / 100.0)
        else:
            hw = float(seg["low"].min())
            new = hw * (1 + trail_pct / 100.0)
        state["high_water"] = hw
        tight = pos.tighter(pos.stop, new)
        if pos.stop is not None and tight == pos.stop:
            return None
        return ExitAction(action="move_stop", price=round(tight, 4),
                          reason=f"{trail_pct}% trail off {hw:.4f}")


# ---------- 3. nbar_trailing_stop ----------

@register("nbar_trailing_stop")
class NBarTrailingStop:
    """Owner's T1: initial stop = the lowest low of the past N bars; it ratchets to the new N-bar low
    every time the trade makes a new N-bar high, and never moves down."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, *, n: int = 2, **_: Any) -> ExitOrder | None:
        win = bars.iloc[max(0, pos.entry_index - n + 1):pos.entry_index + 1]
        if win.empty:
            return None
        px = float(win["low"].min()) if pos.long else float(win["high"].max())
        return ExitOrder(kind="stop", price=round(px, 4), qty_pct=100.0)

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, *, n: int = 2, **_: Any) -> ExitAction | None:
        if len(bars) < n + 1:
            return None
        win = bars.tail(n)
        prev = bars.iloc[-(n + 1):-1]
        if pos.long:
            made_new_extreme = float(bars["high"].iloc[-1]) >= float(prev["high"].max())
            candidate = float(win["low"].min())
        else:
            made_new_extreme = float(bars["low"].iloc[-1]) <= float(prev["low"].min())
            candidate = float(win["high"].max())
        if not made_new_extreme:
            return None
        tight = pos.tighter(pos.stop, candidate)
        if pos.stop is not None and tight == pos.stop:
            return None
        return ExitAction(action="move_stop", price=round(tight, 4), reason=f"new {n}-bar extreme")


# ---------- 4. pattern_low_stop ----------

@register("pattern_low_stop")
class PatternLowStop:
    """Stop a couple of ticks under the divergence / pattern candle (over it, for a short).

    The pattern candle is whichever bar the entry logic recorded in `pos.meta['pattern_index']`;
    without one it falls back to the extreme of the `lookback_bars` up to and including the entry bar,
    which is the same candle a human would point at."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, *, ticks: int = 2, tick_size: float = 0.01,
                lookback_bars: int = 3, **_: Any) -> ExitOrder | None:
        i = pos.meta.get("pattern_index")
        if i is None:
            win = bars.iloc[max(0, pos.entry_index - lookback_bars + 1):pos.entry_index + 1]
        else:
            win = bars.iloc[int(i):int(i) + 1]
        if win.empty:
            return None
        pad = ticks * tick_size
        px = float(win["low"].min()) - pad if pos.long else float(win["high"].max()) + pad
        return ExitOrder(kind="stop", price=round(px, 4), qty_pct=100.0)

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, **_: Any) -> ExitAction | None:
        return None   # a fixed pattern stop does not move; scale_out_breakeven is what tightens it


# ---------- 5. stoch_rotation_partial ----------

@register("stoch_rotation_partial")
class StochRotationPartial:
    """Take a partial when the fast stochastic rotates back to its extreme: %K crossing UP through
    `level` for a long, DOWN through it for a short. Fires once per position."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, **_: Any) -> ExitOrder | None:
        return None

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, *, k: int = 9, d: int = 3,
               level: float = 80, partial_pct: float = 50.0, **_: Any) -> ExitAction | None:
        if state.get("fired") or len(bars) < 2:
            return None
        kk = stoch_frame(bars.iloc[pos.entry_index:] if pos.entry_index < len(bars) - 1 else bars, k, d)["k"]
        if len(kk) < 2:
            return None
        prev, now = float(kk.iloc[-2]), float(kk.iloc[-1])
        crossed = (prev <= level < now) if pos.long else (prev >= level > now)
        if not crossed:
            return None
        state["fired"] = True
        return ExitAction(action="partial", qty_pct=partial_pct,
                          reason=f"{k}-{d} rotated {'up through' if pos.long else 'down through'} {level:g}")


# ---------- 6. scale_out_breakeven ----------

@register("scale_out_breakeven")
class ScaleOutBreakeven:
    """Once a partial is booked, the remainder rides with its stop at entry. Fires once."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, **_: Any) -> ExitOrder | None:
        return None

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, *, offset_ticks: int = 0,
               tick_size: float = 0.01, **_: Any) -> ExitAction | None:
        if state.get("fired") or pos.partials_taken < 1:
            return None
        pad = offset_ticks * tick_size
        be = pos.entry_price + pad if pos.long else pos.entry_price - pad
        tight = pos.tighter(pos.stop, be)
        if pos.stop is not None and tight == pos.stop:
            state["fired"] = True
            return None
        state["fired"] = True
        return ExitAction(action="move_stop", price=round(tight, 4), reason="breakeven after first partial")


# ---------- 7. time_exit ----------

@register("time_exit")
class TimeExit:
    """Flat by HH:MM ET. Also the intraday-only enforcement for leveraged ETFs (plan §9)."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, **_: Any) -> ExitOrder | None:
        return None

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, *, flat_by: str = "15:55",
               no_entries_after: str | None = None, **_: Any) -> ExitAction | None:
        t = _et_time(bars.index[-1])
        if t >= _parse_hhmm(flat_by):
            return ExitAction(action="close", qty_pct=100.0, reason=f"time exit {flat_by} ET")
        return None

    @staticmethod
    def blocks_entry(ts, *, flat_by: str = "15:55", no_entries_after: str | None = None, **_: Any) -> bool:
        t = _et_time(ts)
        cutoff = _parse_hhmm(no_entries_after) if no_entries_after else _parse_hhmm(flat_by)
        return t >= cutoff


# ---------- 8. synthetic_premarket_stop ----------

@register("synthetic_premarket_stop")
class SyntheticPremarketStop:
    """The pre-market stop the broker will not honour. Live: a websocket watcher in its own container
    (app/monitor/synthetic_stop.py) firing a bounded closing LIMIT. Here it supplies the same level so
    a backtest of a pre-market strategy is stopped out exactly where the live system would be —
    plus the slip through the stop that the bounded limit costs."""

    extended_hours_allowed = True

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, *, stop: str = "plan", limit_offset_pct: float = 0.5,
                active_window: str = "04:00-09:30", **_: Any) -> ExitOrder | None:
        if pos.stop is None:
            return None
        return ExitOrder(kind="stop", price=round(pos.stop, 4), qty_pct=100.0, extended_hours=True)

    @staticmethod
    def levels(pos: PositionView, bars: pd.DataFrame, *, active_window: str = "04:00-09:30",
               **_: Any) -> dict:
        return {"stop": pos.stop, "target": None, "window": active_window}

    @staticmethod
    def active_at(ts, *, active_window: str = "04:00-09:30", **_: Any) -> bool:
        lo, hi = active_window.split("-")
        return _parse_hhmm(lo) <= _et_time(ts) < _parse_hhmm(hi)

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, **_: Any) -> ExitAction | None:
        return None


# ---------- 9. rotation_complete_exit ----------

@register("rotation_complete_exit")
class RotationCompleteExit:
    """S7's "must take it off": cover the whole position the moment the fast stochastic completes its
    rotation back — a momentum-cycle exit, not a price target."""

    @staticmethod
    def initial(pos: PositionView, bars: pd.DataFrame, **_: Any) -> ExitOrder | None:
        return None

    @staticmethod
    def on_bar(pos: PositionView, bars: pd.DataFrame, state: dict, *, k: int = 9, d: int = 3,
               complete_level: float = 20, **_: Any) -> ExitAction | None:
        if len(bars) < 2:
            return None
        kk = stoch_frame(bars, k, d)["k"]
        prev, now = float(kk.iloc[-2]), float(kk.iloc[-1])
        # short: entered on a rotation UP to 80, exit when %K has come back DOWN through complete_level
        done = (prev >= complete_level > now) if not pos.long else (prev <= complete_level < now)
        if not done:
            return None
        return ExitAction(action="close", qty_pct=100.0,
                          reason=f"{k}-{d} rotation complete at {complete_level:g}")
