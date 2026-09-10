"""Template 02 — the morning trade plan, plus the code re-validation that guards it.

Hermes returns judgment; code checks the arithmetic. Template 02's Notes are explicit: every price must
sit near a level that was supplied, size may only be kept or reduced, and a plan that fails validation is
stored `rejected` with a reason — never silently corrected. `validate_plan` is that check, and it is the
reason a hallucinated price cannot become a signal.

Nothing here places an order. A `signals` row is a proposal the owner approves by hand (Phase 5).
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from app.llm import complete_json
from app.llm.prompts import load as load_prompt

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "morning_trade_plan"
LEVEL_TOLERANCE_PCT = 3.0        # template 02 Notes: prices must sit within ±3% of a supplied level
CONFIDENCE_CAP_UNPROVEN = 85     # system rule 7: never above 85 below 100 logged trades
LEVEL_KEYS = ("premarket_high", "premarket_low", "prior_high", "prior_low", "prior_close",
              "high_20d", "low_20d", "high_60d", "low_60d", "vwap", "poc", "vah", "val")


def supplied_levels(candidate: dict) -> list[float]:
    """Every number Hermes was allowed to build a price from, including the live price itself."""
    lv = candidate.get("levels") or {}
    out = [float(lv[k]) for k in LEVEL_KEYS if lv.get(k) is not None]
    out += [float(x) for x in (lv.get("round_numbers") or [])]
    out += [float(v) for v in (lv.get("atr_extensions") or {}).values() if v is not None]
    price = (candidate.get("metrics") or {}).get("price")
    if price is not None:
        out.append(float(price))
    return out


def near_a_level(value: float, levels: list[float], tolerance_pct: float = LEVEL_TOLERANCE_PCT) -> bool:
    return any(abs(value - lv) <= abs(lv) * tolerance_pct / 100.0 for lv in levels if lv)


def validate_plan(plan: dict, candidate: dict, *, risk: dict, trades_logged: int = 0,
                  tolerance_pct: float = LEVEL_TOLERANCE_PCT) -> list[str]:
    """Return the list of reasons this plan is not executable. Empty list = clean."""
    problems: list[str] = []
    decision = plan.get("decision")
    if decision != "trade":
        return problems              # 'watch' and 'skip' carry no prices to check

    direction = plan.get("direction")
    if direction not in ("long", "short"):
        problems.append(f"decision 'trade' without a valid direction (got {direction!r})")

    entry, stop, target = plan.get("entry"), plan.get("stop"), plan.get("target")
    for name, value in (("entry", entry), ("stop", stop)):
        if value is None:
            problems.append(f"decision 'trade' without {name}")
    if problems:
        return problems

    levels = supplied_levels(candidate)
    if not levels:
        problems.append("no levels were supplied for this candidate; no price can be validated")
    else:
        for name, value in (("entry", entry), ("stop", stop), ("target", target)):
            if value is None:
                continue
            if not near_a_level(float(value), levels, tolerance_pct):
                problems.append(f"{name} {value} is not within {tolerance_pct}% of any supplied level")

    if direction == "long":
        if stop is not None and entry is not None and float(stop) >= float(entry):
            problems.append(f"long stop {stop} is not below entry {entry}")
        if target is not None and entry is not None and float(target) <= float(entry):
            problems.append(f"long target {target} is not above entry {entry}")
    elif direction == "short":
        if stop is not None and entry is not None and float(stop) <= float(entry):
            problems.append(f"short stop {stop} is not above entry {entry}")
        if target is not None and entry is not None and float(target) >= float(entry):
            problems.append(f"short target {target} is not below entry {entry}")

    # Size is checked against the stop Hermes ACTUALLY chose, not the 1-ATR estimate it was shown
    # before choosing. A tighter stop legitimately allows more shares; capping against the stale
    # estimate would reject correct plans, and capping only by it would pass over-risked ones.
    size = plan.get("size")
    if size is not None:
        if size <= 0:
            problems.append(f"size {size} is not positive")
        risk_dollars = (candidate.get("max_size_by_risk") or {}).get("risk_dollars")
        if risk_dollars and entry is not None and stop is not None:
            per_share = abs(float(entry) - float(stop))
            if per_share > 0 and size * per_share > risk_dollars * 1.05:   # 5% tolerance for rounding
                allowed = int(risk_dollars // per_share)
                problems.append(f"size {size} x stop distance {per_share:.4f} risks "
                                f"{size * per_share:.2f}, above the {risk_dollars:.2f} budget "
                                f"(max {allowed} shares for this stop)")

    allowed = risk.get("order_types") or []
    entry_type = plan.get("entry_type")
    if allowed and entry_type:
        mapped = "limit" if entry_type == "limit" else ("stop_limit" if entry_type == "stop_limit" else entry_type)
        if mapped not in allowed and entry_type not in allowed:
            problems.append(f"entry_type {entry_type!r} is not in the strategy's allowed order types {allowed}")

    confidence = plan.get("confidence")
    if confidence is not None and trades_logged < 100 and confidence > CONFIDENCE_CAP_UNPROVEN:
        problems.append(f"confidence {confidence} exceeds the {CONFIDENCE_CAP_UNPROVEN} cap for a strategy "
                        f"with {trades_logged} logged trades")
    return problems


def strategy_rules_text(cfg_row: dict) -> str:
    """The playbook as configured, handed to Hermes verbatim (template 02 asks for exactly this)."""
    parts = []
    if cfg_row.get("notes"):
        parts.append(str(cfg_row["notes"]))
    ec = cfg_row.get("entry_conditions") or {}
    hard = ec.get("all") or []
    soft = ec.get("soft") or []
    if hard:
        parts.append("HARD entry conditions (already verified by code for every candidate shown):")
        parts += [f"  - {c.get('label') or ''} [{c.get('detector')} {c.get('op')} {c.get('value')}]".rstrip()
                  for c in hard]
    if soft:
        parts.append("SOFT conditions (scoring only):")
        parts += [f"  - {c.get('label') or ''} [{c.get('detector')} {c.get('op')} {c.get('value')}]".rstrip()
                  for c in soft]
    exits = cfg_row.get("exit_logic") or []
    if exits:
        parts.append("EXIT logic configured for this strategy:")
        parts += [f"  - {e.get('plugin')} {e.get('params') or {}}" for e in exits]
    risk = cfg_row.get("risk") or {}
    if risk:
        # These two constraints are the ones plans most often break, so they are stated in the open
        # rather than left for the model to dig out of the JSON blob below.
        parts.append(
            "A COMPLETE PLAN — if `decision` is \"trade\" you MUST also give `direction`, `entry_type`, "
            "`entry`, `stop`, `target` and `size`. The schema marks them optional because \"watch\" and "
            "\"skip\" do not use them; for a \"trade\" they are all mandatory. If you cannot supply every "
            "one of them, answer \"watch\" or \"skip\" instead — an incomplete trade plan is rejected.")
        allowed = risk.get("order_types") or []
        if allowed:
            parts.append(f"ALLOWED ORDER TYPES — `entry_type` MUST be one of: {', '.join(allowed)}. "
                         f"Any other entry_type makes the plan invalid and it will be rejected.")
        parts.append(
            "STOP AND SIZE — each candidate carries `stop_options`: every valid stop level, already "
            "paired with the maximum shares the risk budget allows for it. Pick ONE row; copy its "
            "`stop` into `stop` and use at most its `max_shares` for `size`. Do not compute a stop "
            "yourself and do not take a size from a different row — that is what makes a plan invalid.")
        parts.append(
            "PRICES — entry and target must each sit within 3% of a level supplied in that candidate's "
            "`levels` (which includes `atr_extensions` for measured-move targets, and `round_numbers`). "
            "The stop must come from `stop_options`. A price that references no supplied level makes the "
            "plan invalid.")
        parts.append(f"RISK block: {json.dumps(risk, separators=(',', ':'))}")
    return "\n".join(parts) if parts else "(no playbook text configured)"


def run_plan(*, day: dt.date, phase: str, cfg_row: dict, candidates: list[dict], account: dict,
             guardian_defaults: dict, events: list[dict], lessons: list[str]) -> tuple[dict, dict]:
    """Call template 02 for one strategy. Raises LLMError; the caller decides what a failure means."""
    spec = load_prompt(TEMPLATE_SLUG)
    risk = cfg_row.get("risk") or {}
    payload, meta = complete_json(spec, {
        "trade_date": day.isoformat(),
        "phase": phase,
        "strategy_code": cfg_row.get("code"),
        "strategy_name": cfg_row.get("name"),
        "automation_mode": cfg_row.get("automation_mode"),
        "strategy_rules": strategy_rules_text(cfg_row),
        "buying_power": account.get("buying_power"),
        "equity": account.get("equity"),
        "open_positions": account.get("open_positions"),
        "daily_pnl_r": account.get("daily_pnl_r"),
        "spread_ceiling_pct": risk.get("spread_ceiling_pct", guardian_defaults.get("spread_ceiling_pct")),
        "risk_per_trade_pct": risk.get("risk_per_trade_pct", guardian_defaults.get("risk_per_trade_pct_default")),
        "daily_loss_limit_r": risk.get("daily_loss_limit_r", guardian_defaults.get("daily_loss_limit_r")),
        "economic_events": json.dumps(events, default=str) if events
                           else "(none recorded — economic_events fills in Phase 6)",
        "journal_lessons": "\n".join(f"- {x}" for x in lessons) if lessons
                           else "(no closed trades yet — the journal fills from Phase 6)",
        "candidates_json": json.dumps(candidates, separators=(",", ":"), default=str),
    })
    return payload, meta


def signal_row(*, plan: dict, candidate: dict, strategy_id: str, day: dt.date, scan_id: str | None,
               template_id: str | None, problems: list[str], meta: dict, raw: dict) -> dict:
    """One `signals` row. A plan that failed validation is stored rejected, with the reasons."""
    decision = plan.get("decision")
    status = "proposed" if decision == "trade" and not problems else "rejected"
    reject_reason = None
    if problems:
        reject_reason = "llm_validation_failed: " + "; ".join(problems)
    elif decision != "trade":
        reject_reason = f"hermes decision '{decision}'"

    return {
        "strategy_id": strategy_id,
        "signal_date": day.isoformat(),
        "scan_id": scan_id,
        "symbol": candidate["symbol"],
        "direction": plan.get("direction") or "long",
        "decision": decision,
        "entry_price": plan.get("entry"),
        "entry_type": plan.get("entry_type"),
        "stop": plan.get("stop"),
        "target": plan.get("target"),
        "trail_pct": plan.get("trail_pct"),
        "size": plan.get("size"),
        "confidence": plan.get("confidence"),
        "reasoning": plan.get("reasoning"),
        "evidence": {
            "in_play_sentence": plan.get("in_play_sentence"),
            "entry_basis": plan.get("entry_basis"), "stop_basis": plan.get("stop_basis"),
            "target_basis": plan.get("target_basis"),
            "event_adjustment": plan.get("event_adjustment"),
            "lessons_applied": plan.get("lessons_applied") or [],
            "metrics": candidate.get("metrics"), "levels": candidate.get("levels"),
            "catalysts": candidate.get("catalysts"),
            "max_size_by_risk": candidate.get("max_size_by_risk"),
            "scan_rank": candidate.get("rank"), "scan_score": candidate.get("scan_score"),
            "validation_problems": problems,
        },
        "guardian_precheck": candidate.get("guardian_precheck"),
        "status": status,
        "reject_reason": reject_reason,
        "prompt_template_id": template_id,
        "raw_llm_response": {"plan": plan, "model": meta.get("model"), "attempts": meta.get("attempts"),
                             "market_context": raw.get("market_context")},
    }
