"""The fill model (plan §13 countermeasure 3, §31) — the part that decides whether a backtest lies.

Three rules, all of them deliberately pessimistic, because every one of them is asymmetric in the
direction that turns a fake-profitable backtest negative:

  1. **No mid-bar magic.** A bar is high/low/close, not a path. When a bar touched BOTH the stop and
     the target, the backtester books the STOP. A coin flip, or "whichever is nearer the open", is a
     free half-R per ambiguous bar, and ambiguous bars cluster on exactly the volatile days a momentum
     strategy lives on.
  2. **Queue position on passive limits** (plan §31). A resting limit is filled only when volume traded
     THROUGH the level — a touch is not a fill — and only when the volume that traded beyond it is a
     conservative multiple of your own size. The asymmetry this removes is the fatal one: without it a
     backtest fills every losing limit (price kept going) and every winning limit (price touched and
     reversed), when in life you get the losers and miss the winners.
  3. **You are not the whole tape.** A fill is capped at `max_participation_pct` of the bar's volume,
     so a 50,000-share idea on a 4,000-share pre-market bar is a partial fill, not a fantasy.

Spread and slippage are explicit inputs, not assumptions buried in code: Alpaca's historical bars carry
no quotes, so the spread is ESTIMATED from `spread_model` and every result reports the parameters used.
An estimate that is labelled is honest; an estimate presented as a measurement is what plan §33 forbids.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import pandas as pd


@dataclass(slots=True)
class FillModel:
    """Every parameter that can move a backtest's P&L. Stored with the run, reported with the result."""

    spread_model: Literal["pct", "range", "band"] = "band"
    spread_pct: float = 0.20                 # used when spread_model == "pct"
    spread_range_fraction: float = 0.10      # used when spread_model == "range": spread ≈ 10% of the bar's range
    # spread_model == "band": a price-band table, because a $3 low-float name is not a $300 mega-cap
    spread_band_pct: dict = field(default_factory=lambda: {"2": 1.20, "5": 0.60, "10": 0.30, "50": 0.10,
                                                           "inf": 0.05})
    slippage_ticks: int = 1
    tick_size: float = 0.01
    stop_slippage_pct: float = 0.15          # a stop is not a limit: it fills THROUGH the level
    limit_requires_through: bool = True      # plan §31 queue rule
    queue_volume_multiple: float = 2.0       # volume through the level must be this × your size
    max_participation_pct: float = 10.0      # of the bar's volume
    commission_per_share: float = 0.0        # Alpaca equities: zero
    apply_regulatory_fees: bool = True       # SEC + TAF on sells, from app_settings.fee_model
    allow_partial_fills: bool = True

    def as_dict(self) -> dict:
        return asdict(self)

    # ---------- spread ----------

    def half_spread(self, price: float, bar: pd.Series | None = None) -> float:
        """Half the estimated bid/ask spread in dollars — what one side of a round trip costs."""
        pct = self.spread_pct
        if self.spread_model == "range" and bar is not None:
            rng = float(bar["high"]) - float(bar["low"])
            return max(rng * self.spread_range_fraction / 2.0, self.tick_size / 2.0)
        if self.spread_model == "band":
            for edge in ("2", "5", "10", "50"):
                if price < float(edge):
                    pct = self.spread_band_pct.get(edge, self.spread_pct)
                    break
            else:
                pct = self.spread_band_pct.get("inf", self.spread_pct)
        return max(price * pct / 100.0 / 2.0, self.tick_size / 2.0)

    def spread_cost(self, price: float, qty: int, bar: pd.Series | None = None) -> float:
        return round(self.half_spread(price, bar) * qty, 4)


@dataclass(slots=True)
class Fill:
    price: float
    qty: int
    kind: str                   # limit | stop | market | close
    reason: str = ""
    partial: bool = False
    requested_qty: int = 0
    spread_cost: float = 0.0
    slippage: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def _cap(fm: FillModel, want: int, bar: pd.Series) -> tuple[int, bool]:
    if not fm.allow_partial_fills:
        return want, False
    cap = int(float(bar["volume"]) * fm.max_participation_pct / 100.0)
    if cap <= 0:
        return 0, True
    return (want, False) if want <= cap else (cap, True)


def limit_fill(fm: FillModel, bar: pd.Series, *, side: Literal["buy", "sell"], price: float,
               qty: int) -> Fill | None:
    """A passive limit resting at `price`. Fills only if the tape traded THROUGH it with enough volume."""
    lo, hi = float(bar["low"]), float(bar["high"])
    if side == "buy":
        touched, through = lo <= price, lo < price
    else:
        touched, through = hi >= price, hi > price
    if not touched:
        return None
    if fm.limit_requires_through and not through:
        return None
    # The participation cap is applied FIRST, then the queue rule is checked against the size you could
    # actually have got. Checking the queue against the full order instead would refuse any large order
    # outright, which sounds conservative but is really just a different wrong answer: in life a big
    # order on a thin bar gets a partial, and a partial is what the result should show.
    got, partial = _cap(fm, qty, bar)
    if got <= 0:
        return None
    if fm.limit_requires_through:
        # conservative queue estimate: how much of the bar's volume printed beyond your level
        rng = hi - lo
        beyond = (price - lo) / rng if side == "buy" else (hi - price) / rng
        vol_through = float(bar["volume"]) * max(beyond, 0.0)
        if vol_through < fm.queue_volume_multiple * got:
            return None
    return Fill(price=round(price, 4), qty=got, kind="limit", partial=partial, requested_qty=qty,
                reason="filled through the level" if fm.limit_requires_through else "touched",
                spread_cost=0.0)


def stop_fill(fm: FillModel, bar: pd.Series, *, side: Literal["buy", "sell"], stop: float,
              qty: int) -> Fill | None:
    """A stop (or stop-entry). Triggers on a touch and fills THROUGH the level, never at it."""
    lo, hi = float(bar["low"]), float(bar["high"])
    triggered = hi >= stop if side == "buy" else lo <= stop
    if not triggered:
        return None
    slip = stop * fm.stop_slippage_pct / 100.0 + fm.slippage_ticks * fm.tick_size
    px = stop + slip if side == "buy" else stop - slip
    # a gap beyond the stop fills at the open, not at the stop: the market never offered your level
    open_px = float(bar["open"])
    px = max(px, open_px) if side == "buy" else min(px, open_px)
    px = min(px, hi) if side == "buy" else max(px, lo)
    got, partial = _cap(fm, qty, bar)
    if got <= 0:
        return None
    return Fill(price=round(px, 4), qty=got, kind="stop", partial=partial, requested_qty=qty,
                slippage=round(abs(px - stop), 4),
                reason="gapped through the stop" if abs(px - open_px) < 1e-9 else "stop triggered",
                spread_cost=fm.spread_cost(px, got, bar))


def market_fill(fm: FillModel, bar: pd.Series, *, side: Literal["buy", "sell"], qty: int,
                at: str = "close") -> Fill | None:
    """A marketable order at the bar's open or close, paying half the spread plus slippage."""
    px = float(bar[at if at in ("open", "close") else "close"])
    hs = fm.half_spread(px, bar)
    slip = fm.slippage_ticks * fm.tick_size
    fill_px = px + hs + slip if side == "buy" else px - hs - slip
    got, partial = _cap(fm, qty, bar)
    if got <= 0:
        return None
    return Fill(price=round(fill_px, 4), qty=got, kind="market", partial=partial, requested_qty=qty,
                slippage=round(slip, 4), reason=f"marketable at the {at}",
                spread_cost=round(hs * got, 4))


def resolve_bar(bar: pd.Series, *, long: bool, stop: float | None, target: float | None) -> str:
    """Which resting level a bar hit: 'stop' | 'target' | 'both' | 'none'.

    'both' exists so the caller can SEE the ambiguity and count it; `intrabar_outcome` then resolves it
    against the trade. A run that is mostly 'both' bars is telling you the timeframe is too coarse for
    the strategy, which is information worth having rather than silently averaging away.
    """
    lo, hi = float(bar["low"]), float(bar["high"])
    hit_stop = stop is not None and (lo <= stop if long else hi >= stop)
    hit_target = target is not None and (hi >= target if long else lo <= target)
    if hit_stop and hit_target:
        return "both"
    if hit_stop:
        return "stop"
    if hit_target:
        return "target"
    return "none"


def intrabar_outcome(bar: pd.Series, *, long: bool, stop: float | None, target: float | None) -> str:
    """Same as `resolve_bar`, with 'both' decided AGAINST the trade. No mid-bar magic."""
    r = resolve_bar(bar, long=long, stop=stop, target=target)
    return "stop" if r == "both" else r


def regulatory_fees(fee_model: dict[str, Any], *, side: Literal["buy", "sell"], price: float,
                    qty: int) -> float:
    """SEC + TAF, charged on SELLS only. Same rates the live ledger uses (app_settings.fee_model), so a
    backtest and the journal cost a share the same amount."""
    if side != "sell":
        return 0.0
    sec = price * qty * float(fee_model.get("sec_fee_per_dollar", 0.0000278))
    taf = min(qty * float(fee_model.get("taf_per_share", 0.000166)), float(fee_model.get("taf_max", 8.30)))
    return round(sec + taf, 6)


DEFAULT_FILL_MODEL = FillModel()
