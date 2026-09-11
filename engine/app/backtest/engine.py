"""The event loop: one symbol, one session, bar by bar (plan §13).

Custom pandas event loop rather than vectorbt / backtesting.py (decision #2): neither handles a
multi-symbol scanner-universe replay with an explicit fill model and a plugin exit chain.

The no-lookahead guarantee is structural, not a promise. Every decision at bar *i* is taken from
`bars.iloc[:i + 1]` — the slice is what detectors and exit plugins receive, so a detector that wanted
tomorrow's bar physically cannot reach it. Detectors that need future bars to confirm a swing point
(divergence, double top/bottom) pay their confirmation lag inside the slice, exactly as they do live.

Order of business within a bar, which is also the order of pessimism:
  1. if in a position, resting levels are checked against the bar's high/low FIRST — a bar that
     touched both the stop and the target books the stop (`fills.intrabar_outcome`);
  2. then exit plugins react to the bar's CLOSE (ratchets, partials, time exits);
  3. only then may a new entry trigger.
So an exit always beats an entry inside the same bar, and a stop always beats a target.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field

import pandas as pd

from app.backtest import fills as F
from app.backtest.fills import FillModel
from app.detectors import get as get_detector
from app.detectors.indicators import QUAD_BANDS, stoch_frame
from app.exits import ExitAction
from app.exits import get as get_exit
from app.exits.plugins import PositionView
from app.models import BacktestSpec, Condition, StrategyConfig
from app.scanner.conditions import evaluate

log = logging.getLogger(__name__)

# Which conditions the replay answers once per symbol-day, and which the loop must answer per bar.
DAY_LEVEL_DETECTORS = frozenset({
    "gap_pct", "float_shares", "adv", "atr", "relative_volume_premarket", "premarket_high_low",
    "prior_day_levels", "distance_to_level_atr", "breakout_52w_base", "sector_relative_strength",
    "nbar_high_low",
})
BAR_LEVEL_DETECTORS = frozenset({
    "ema", "rsi", "vwap", "stochastic", "stoch_quad_extreme", "stoch_divergence", "stoch_embedded",
    "opening_range", "double_top_bottom", "flagpole_steepness", "cvd", "volume_profile",
})


def _hhmm(v: str) -> dt.time:
    hh, mm = v.split(":")
    return dt.time(int(hh), int(mm))


def _window(spec: BacktestSpec) -> tuple[dt.time, dt.time]:
    lo, hi = spec.entry_window.split("-")
    return _hhmm(lo), _hhmm(hi)


@dataclass
class BacktestTrade:
    """One round trip, shaped like a `journal` row so backtest and live analytics share one code path."""

    symbol: str
    strategy: str
    direction: str
    entry_time: str
    entry_price: float
    exit_time: str | None
    exit_price: float | None
    qty: int
    pnl_gross: float
    fees: float
    spread_cost: float
    pnl_net: float
    r_multiple: float | None
    planned_stop: float | None
    planned_target: float | None
    exit_reason: str
    setup_type: str
    bars_held: int
    partials: int = 0
    ambiguous_bars: int = 0
    partial_fill: bool = False
    quality_flags: list[str] = field(default_factory=list)
    legs: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SymbolDayResult:
    symbol: str
    trades: list[BacktestTrade] = field(default_factory=list)
    entries_considered: int = 0
    entries_armed: int = 0
    entries_missed_queue: int = 0
    bars: int = 0
    notes: list[str] = field(default_factory=list)


# ---------- conditions ----------

def split_conditions(cfg: StrategyConfig) -> tuple[list[Condition], list[Condition], list[Condition]]:
    """(day-level, bar-level, unsupported) hard gates. An unsupported gate is REPORTED, never dropped."""
    day, bar, other = [], [], []
    for c in cfg.entry_conditions.all:
        (day if c.detector in DAY_LEVEL_DETECTORS else
         bar if c.detector in BAR_LEVEL_DETECTORS else other).append(c)
    return day, bar, other


def bar_gates_pass(conds: list[Condition], window: pd.DataFrame, ctx: dict, *, state: dict,
                   index: int) -> tuple[bool, list[str]]:
    """Evaluate the bar-level hard gates on `window` — bars up to and including the current one.

    A condition carrying `within_bars` is a SETUP, not a trigger: it is still evaluated on every bar
    (so `state` records when it last held), but it passes while that last hit is recent enough. That is
    what makes a multi-stage pattern expressible — the quad reaching under 20, then, some bars later,
    the 9-3 turning back up through it.
    """
    failed: list[str] = []
    for n, c in enumerate(conds):
        key = f"{n}:{c.detector}"
        try:
            res = get_detector(c.detector)(window, **{**ctx, **c.params})
        except Exception as e:  # noqa: BLE001
            log.debug("detector %s failed: %s", c.detector, e)
            failed.append(f"{c.detector}: error")
            continue
        ok = evaluate(c, res).status == "pass"
        if ok:
            state[key] = index
        if c.within_bars:
            last = state.get(key)
            ok = last is not None and (index - last) < c.within_bars
        if not ok:
            failed.append(c.label or f"{c.detector} {c.op} {c.value}")
    return (not failed), failed



def stoch_cache_for(bar_conds: list[Condition], steps, bars: pd.DataFrame) -> dict:
    """Pre-compute, once per symbol-day, the stochastic bands this config actually asks for.

    Safe because every window in `stoch_frame` is rolling: bar i's value depends only on bars at or
    before i, so slicing the session-wide frame is identical to recomputing on the prefix. Without it
    a quad gate recomputes four stochastics from scratch on a growing slice for every bar of every
    session — the same numbers, O(n²) times.
    """
    wanted: set[tuple[int, int, int]] = set()
    for c in bar_conds:
        p = c.params
        if c.detector == "stoch_quad_extreme":
            for k, d in (p.get("bands") or QUAD_BANDS):
                wanted.add((int(k), int(d), 1))
        elif c.detector in ("stochastic", "stoch_embedded", "stoch_divergence"):
            wanted.add((int(p.get("k", 9)), int(p.get("d", 3)),
                        int(p.get("smooth_k", 1)) if c.detector == "stochastic" else 1))
    for st in steps or []:
        if st.plugin in ("stoch_rotation_partial", "rotation_complete_exit"):
            wanted.add((int(st.params.get("k", 9)), int(st.params.get("d", 3)), 1))
    if bars is None or bars.empty:
        return {}
    return {key: stoch_frame(bars, key[0], key[1], key[2]) for key in wanted}


# ---------- entry / stop / target rules ----------

def entry_trigger(spec: BacktestSpec, window: pd.DataFrame, *, long: bool, day_levels: dict) -> dict | None:
    """The price and order type an entry would rest at, given the rule. None = no level yet."""
    off = spec.entry_offset_pct / 100.0
    rule = spec.entry_rule
    if rule in ("signal_bar_close", "next_bar_open"):
        return {"kind": "market", "at": "close" if rule == "signal_bar_close" else "open", "price": None}
    if rule == "premarket_high_break":
        lvl = day_levels.get("premarket_high") if long else day_levels.get("premarket_low")
    elif rule == "prior_day_high_break":
        lvl = day_levels.get("prior_high") if long else day_levels.get("prior_low")
    elif rule == "opening_range_break":
        orr = day_levels.get("opening_range") or {}
        lvl = orr.get("high") if long else orr.get("low")
    else:
        return None
    if lvl is None:
        return None
    price = lvl * (1 + off) if long else lvl * (1 - off)
    return {"kind": "stop", "price": round(float(price), 4), "level": float(lvl)}


def initial_stop(spec: BacktestSpec, window: pd.DataFrame, *, long: bool, entry_price: float,
                 atr_value: float | None, day_levels: dict) -> tuple[float | None, str]:
    n = spec.stop_lookback_bars
    tail = window.tail(n)
    if spec.stop_rule in ("pattern_low", "entry_bar_low"):
        seg = window.tail(1) if spec.stop_rule == "entry_bar_low" else tail
        if seg.empty:
            return None, "no bars for the stop"
        px = float(seg["low"].min()) - 0.02 if long else float(seg["high"].max()) + 0.02
        return px, f"{'low' if long else 'high'} of the last {len(seg)} bar(s)"
    if spec.stop_rule == "atr_multiple":
        if not atr_value:
            return None, "no ATR available"
        d = spec.stop_atr_multiple * atr_value
        return (entry_price - d if long else entry_price + d), f"{spec.stop_atr_multiple}x ATR"
    if spec.stop_rule == "percent":
        d = entry_price * spec.stop_pct / 100.0
        return (entry_price - d if long else entry_price + d), f"{spec.stop_pct}%"
    if spec.stop_rule == "opening_range_opposite":
        orr = day_levels.get("opening_range") or {}
        lvl = orr.get("low") if long else orr.get("high")
        return (float(lvl) if lvl is not None else None), "opposite side of the opening range"
    return None, "unknown stop rule"


def initial_target(spec: BacktestSpec, *, long: bool, entry_price: float, stop: float | None,
                   atr_value: float | None) -> float | None:
    if spec.target_rule == "none":
        return None
    if spec.target_rule == "atr_multiple":
        if not atr_value:
            return None
        d = spec.target_atr_multiple * atr_value
        return entry_price + d if long else entry_price - d
    if stop is None:
        return None
    risk = abs(entry_price - stop)
    if risk <= 0:
        return None
    return entry_price + spec.target_r * risk if long else entry_price - spec.target_r * risk


def size_for(risk_dollars: float, entry_price: float, stop: float | None, *, max_notional: float) -> int:
    """Fixed fractional risk. No stop means no size — a position whose risk cannot be measured is not taken."""
    if stop is None:
        return 0
    per_share = abs(entry_price - stop)
    if per_share <= 0:
        return 0
    qty = int(risk_dollars // per_share)
    if max_notional and entry_price > 0:
        qty = min(qty, int(max_notional // entry_price))
    return max(qty, 0)


# ---------- the loop ----------

def run_symbol_day(cfg: StrategyConfig, symbol: str, bars: pd.DataFrame, *, spec: BacktestSpec,
                   fill_model: FillModel, fee_model: dict, day_levels: dict, risk_dollars: float,
                   max_notional: float = 100_000.0, atr_value: float | None = None,
                   warmup: int = 0) -> SymbolDayResult:
    """Replay one symbol for one session. `bars` includes `warmup` bars of context before the window."""
    res = SymbolDayResult(symbol=symbol, bars=len(bars))
    if bars is None or bars.empty:
        res.notes.append("no bars")
        return res
    long = cfg.entry_conditions.direction != "short"
    _day_conds, bar_conds, unsupported = split_conditions(cfg)
    if unsupported:
        res.notes.append("gates the backtester cannot evaluate: " + ", ".join(c.detector for c in unsupported))
    w_lo, w_hi = _window(spec)
    steps = [s for s in cfg.exit_logic]
    ctx = {"symbol": symbol, "daily": None, "feed": "sip",
           "stoch_cache": stoch_cache_for(bar_conds, cfg.exit_logic, bars)}

    pos: PositionView | None = None
    stop: float | None = None
    target: float | None = None
    entry_fill: F.Fill | None = None
    plugin_state: dict[str, dict] = {}
    trades_today = 0
    cooldown_until = -1
    pending: dict | None = None          # a level armed on a previous bar, waiting to trigger
    gate_state: dict = {}                # per-condition memory for `within_bars` setup gates
    ambiguous = 0
    legs: list[dict] = []
    partials = 0
    realized: list[tuple[float, int, float, float]] = []   # (price, qty, fees, spread)

    def _close_out(exit_px: float, exit_qty: int, ts, reason: str, kind: str, fee: float, spread: float):
        nonlocal pos, stop, target, partials
        realized.append((exit_px, exit_qty, fee, spread))
        legs.append({"side": "sell" if pos.long else "buy", "price": exit_px, "qty": exit_qty,
                     "ts": str(ts), "reason": reason, "kind": kind})

    for i in range(len(bars)):
        bar = bars.iloc[i]
        ts = bars.index[i]
        t = ts.time()
        window = bars.iloc[:i + 1]

        # ---- 1. resting levels, checked against the bar's range, stop wins ambiguity ----
        if pos is not None:
            r = F.resolve_bar(bar, long=pos.long, stop=stop, target=target)
            if r == "both":
                ambiguous += 1
            outcome = "stop" if r == "both" else r
            if outcome in ("stop", "target"):
                side = "sell" if pos.long else "buy"
                level = stop if outcome == "stop" else target
                if outcome == "stop":
                    f = F.stop_fill(fill_model, bar, side=side, stop=float(level), qty=pos.qty)
                else:
                    # the take-profit is a passive limit: the queue rule may refuse it, and then it
                    # simply stays resting. Assuming it filled is the classic backtest overstatement.
                    f = F.limit_fill(fill_model, bar, side=side, price=float(level), qty=pos.qty)
                if f is not None:
                    fee = F.regulatory_fees(fee_model, side=side, price=f.price, qty=f.qty) \
                        if fill_model.apply_regulatory_fees else 0.0
                    _close_out(f.price, f.qty, ts, f"{outcome} hit ({f.reason})", outcome, fee,
                               f.spread_cost or fill_model.spread_cost(f.price, f.qty, bar))
                    if f.qty >= pos.qty:
                        res.trades.append(_finish(cfg, spec, symbol, pos, entry_fill, realized, legs, ts, i,
                                                  f"{outcome} hit", ambiguous, partials))
                        pos, stop, target, entry_fill = None, None, None, None
                        realized, legs, partials, ambiguous = [], [], 0, 0
                        trades_today += 1
                        cooldown_until = i + cfg.reentry.cooldown_bars
                        continue
                    pos.qty -= f.qty

        # ---- 2. exit plugins react to the bar CLOSE ----
        if pos is not None:
            for step in steps:
                if step.when == "after_partial" and pos.partials_taken < 1:
                    continue
                if step.when == "premarket" and not (dt.time(4, 0) <= t < dt.time(9, 30)):
                    continue
                if step.when == "regular" and not (dt.time(9, 30) <= t < dt.time(16, 0)):
                    continue
                cls = get_exit(step.plugin)
                st = plugin_state.setdefault(f"{step.plugin}:{step.label or ''}", {})
                try:
                    act: ExitAction | None = cls.on_bar(pos, window, st, **step.params)
                except Exception as e:  # noqa: BLE001
                    log.debug("exit %s failed: %s", step.plugin, e)
                    continue
                if act is None or act.action == "none":
                    continue
                if act.action == "move_stop" and act.price is not None:
                    new = pos.tighter(stop, act.price)
                    if new != stop:
                        stop = new
                        pos.stop = new
                elif act.action == "partial":
                    qty = max(int(pos.qty * act.qty_pct / 100.0), 1)
                    qty = min(qty, pos.qty - 1) if pos.qty > 1 else 0
                    if qty <= 0:
                        continue
                    side = "sell" if pos.long else "buy"
                    f = F.market_fill(fill_model, bar, side=side, qty=qty, at="close")
                    if f is None:
                        continue
                    fee = F.regulatory_fees(fee_model, side=side, price=f.price, qty=f.qty) \
                        if fill_model.apply_regulatory_fees else 0.0
                    _close_out(f.price, f.qty, ts, act.reason, "partial", fee, f.spread_cost)
                    pos.qty -= f.qty
                    pos.partials_taken += 1
                    partials += 1
                elif act.action == "close":
                    side = "sell" if pos.long else "buy"
                    f = F.market_fill(fill_model, bar, side=side, qty=pos.qty, at="close")
                    if f is None:
                        continue
                    fee = F.regulatory_fees(fee_model, side=side, price=f.price, qty=f.qty) \
                        if fill_model.apply_regulatory_fees else 0.0
                    _close_out(f.price, f.qty, ts, act.reason, "plugin", fee, f.spread_cost)
                    res.trades.append(_finish(cfg, spec, symbol, pos, entry_fill, realized, legs, ts, i,
                                              act.reason, ambiguous, partials))
                    pos, stop, target, entry_fill = None, None, None, None
                    realized, legs, partials, ambiguous = [], [], 0, 0
                    trades_today += 1
                    cooldown_until = i + cfg.reentry.cooldown_bars
                    break

        # ---- 3. the bar-level gates, evaluated on EVERY bar ----
        # Not inside the entry block below: a `within_bars` setup gate needs unbroken memory of when it
        # last held, and the bars skipped while a position is open or outside the entry window are
        # exactly the ones where a multi-stage setup forms.
        gates_ok, _failed = (bar_gates_pass(bar_conds, window, ctx, state=gate_state, index=i)
                             if bar_conds else (True, []))

        # ---- 4. entries, last, so an exit always wins the bar ----
        if pos is not None or trades_today >= spec.max_trades_per_day or i <= cooldown_until:
            continue
        if trades_today >= 1 and not cfg.reentry.allowed:
            continue
        if trades_today >= cfg.reentry.max_entries:
            continue
        if not (w_lo <= t < w_hi):
            pending = None
            continue

        # a pending stop-entry armed on an earlier bar may trigger on this one
        if pending is not None:
            f = F.stop_fill(fill_model, bar, side="buy" if long else "sell",
                            stop=pending["price"], qty=pending["qty"])
            if f is not None:
                pos, stop, target, entry_fill = _open(cfg, symbol, f, pending, ts, i, fill_model, fee_model,
                                                      spec=spec, atr_value=atr_value)
                legs = [{"side": "buy" if long else "sell", "price": f.price, "qty": f.qty, "ts": str(ts),
                         "reason": pending["reason"], "kind": "stop"}]
                realized, partials, ambiguous = [], 0, 0
                plugin_state = {}
                pending = None
                _place_initial(steps, pos, window)
                continue

        res.entries_considered += 1
        if not gates_ok:
            continue
        trig = entry_trigger(spec, window, long=long, day_levels=day_levels)
        if trig is None:
            continue
        # provisional entry price → stop → size, all from information available at this bar's close
        provisional = float(bar["close"]) if trig["price"] is None else float(trig["price"])
        sl, _why = initial_stop(spec, window, long=long, entry_price=provisional, atr_value=atr_value,
                                day_levels=day_levels)
        qty = size_for(risk_dollars, provisional, sl, max_notional=max_notional)
        if qty <= 0:
            continue
        res.entries_armed += 1
        if trig["kind"] == "market":
            src = bar if trig["at"] == "close" else (bars.iloc[i + 1] if i + 1 < len(bars) else None)
            if src is None:
                continue
            fill_ts = ts if trig["at"] == "close" else bars.index[i + 1]
            f = F.market_fill(fill_model, src, side="buy" if long else "sell", qty=qty, at=trig["at"])
            if f is None:
                continue
            meta = {"reason": f"{spec.entry_rule} ({cfg.code})", "qty": qty, "stop": sl, "atr": atr_value,
                    "pattern_index": i}
            pos, stop, target, entry_fill = _open(cfg, symbol, f, meta, fill_ts, i, fill_model, fee_model,
                                                  spec=spec, atr_value=atr_value)
            legs = [{"side": "buy" if long else "sell", "price": f.price, "qty": f.qty, "ts": str(fill_ts),
                     "reason": meta["reason"], "kind": "market"}]
            realized, partials, ambiguous = [], 0, 0
            plugin_state = {}
            _place_initial(steps, pos, window)
        else:
            pending = {"price": trig["price"], "qty": qty, "stop": sl, "atr": atr_value,
                       "reason": f"{spec.entry_rule} above {trig.get('level')}" if long
                                 else f"{spec.entry_rule} below {trig.get('level')}",
                       "pattern_index": i}
            f = F.stop_fill(fill_model, bar, side="buy" if long else "sell", stop=trig["price"], qty=qty)
            if f is not None:
                pos, stop, target, entry_fill = _open(cfg, symbol, f, pending, ts, i, fill_model, fee_model,
                                                      spec=spec, atr_value=atr_value)
                legs = [{"side": "buy" if long else "sell", "price": f.price, "qty": f.qty, "ts": str(ts),
                         "reason": pending["reason"], "kind": "stop"}]
                realized, partials, ambiguous = [], 0, 0
                plugin_state = {}
                pending = None
                _place_initial(steps, pos, window)

    # ---- end of the window: anything still open is marked out at the last close ----
    if pos is not None:
        bar = bars.iloc[-1]
        side = "sell" if pos.long else "buy"
        f = F.market_fill(fill_model, bar, side=side, qty=pos.qty, at="close")
        if f is not None:
            fee = F.regulatory_fees(fee_model, side=side, price=f.price, qty=f.qty) \
                if fill_model.apply_regulatory_fees else 0.0
            realized.append((f.price, f.qty, fee, f.spread_cost))
            legs.append({"side": side, "price": f.price, "qty": f.qty, "ts": str(bars.index[-1]),
                         "reason": "end of session", "kind": "eod"})
            res.trades.append(_finish(cfg, spec, symbol, pos, entry_fill, realized, legs, bars.index[-1],
                                      len(bars) - 1, "end of session", ambiguous, partials))
    return res


def _open(cfg: StrategyConfig, symbol: str, f: F.Fill, meta: dict, ts, i: int, fill_model: FillModel,
          fee_model: dict, *, spec: BacktestSpec | None = None,
          atr_value: float | None = None) -> tuple[PositionView, float | None, float | None, F.Fill]:
    long = cfg.entry_conditions.direction != "short"
    sl = meta.get("stop")
    tgt = initial_target(spec, long=long, entry_price=f.price, stop=sl, atr_value=atr_value) if spec else None
    pos = PositionView(symbol=symbol, side="long" if long else "short", entry_price=f.price, qty=f.qty,
                       entry_index=i, stop=sl, target=tgt,
                       risk_per_share=abs(f.price - sl) if sl is not None else None,
                       opened_at=ts, meta={"pattern_index": meta.get("pattern_index"),
                                           "entry_reason": meta.get("reason", ""),
                                           "partial_fill": f.partial})
    return pos, sl, tgt, f


def _place_initial(steps, pos: PositionView, window: pd.DataFrame) -> None:
    """Let each exit plugin tighten the stop at entry. A plugin may never widen it (plan §11)."""
    for step in steps:
        if step.when == "after_partial":
            continue
        cls = get_exit(step.plugin)
        try:
            order = cls.initial(pos, window, **step.params)
        except Exception as e:  # noqa: BLE001
            log.debug("exit %s initial() failed: %s", step.plugin, e)
            continue
        if order is None or order.kind not in ("stop", "stop_limit") or order.price is None:
            continue
        pos.stop = pos.tighter(pos.stop, order.price) if pos.stop is not None else order.price


def _finish(cfg: StrategyConfig, spec: BacktestSpec, symbol: str, pos: PositionView, entry: F.Fill,
            realized: list[tuple[float, int, float, float]], legs: list[dict], ts, i: int,
            reason: str, ambiguous: int, partials: int) -> BacktestTrade:
    """Book the round trip.

    P&L convention, identical to the live journal after the Phase 7 fix: `pnl_gross` comes from the
    ACTUAL fill prices, which already contain the spread paid, so `spread_cost` is reported as an
    attribution — how much of the gross the spread ate — and is not deducted a second time.
    """
    qty = sum(q for _p, q, _f, _s in realized)
    entry_notional = entry.price * qty
    exit_notional = sum(p * q for p, q, _f, _s in realized)
    gross = round((exit_notional - entry_notional) if pos.long else (entry_notional - exit_notional), 4)
    fees = round(sum(f for _p, _q, f, _s in realized), 6)
    spread = round(sum(s for _p, _q, _f, s in realized) + (entry.spread_cost or 0.0), 4)
    net = round(gross - fees, 4)
    r = None
    if pos.risk_per_share and qty:
        r = round(net / (pos.risk_per_share * qty), 3)
    flags: list[str] = []
    if entry.partial:
        flags.append(f"partial entry fill: {entry.qty} of {entry.requested_qty} shares")
    if ambiguous:
        flags.append(f"{ambiguous} bar(s) touched both the stop and the target — booked as stops")
    avg_exit = round(exit_notional / qty, 4) if qty else None
    return BacktestTrade(
        symbol=symbol, strategy=cfg.code, direction=pos.side,
        entry_time=str(pos.opened_at), entry_price=entry.price,
        exit_time=str(ts), exit_price=avg_exit, qty=qty,
        pnl_gross=gross, fees=fees, spread_cost=spread, pnl_net=net, r_multiple=r,
        planned_stop=pos.stop, planned_target=pos.target, exit_reason=reason,
        setup_type=f"{cfg.code}:{spec.entry_rule}", bars_held=i - pos.entry_index,
        partials=partials, ambiguous_bars=ambiguous, partial_fill=entry.partial,
        quality_flags=flags, legs=legs,
    )
