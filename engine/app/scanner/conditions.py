"""Evaluate a strategy's `Condition` list against detector results — pure, no I/O (plan §6).

A condition is *evaluable* when its detector is implemented AND the detector produced a value.
Hard gates (`entry_conditions.all`) that are NOT evaluable at scan time (e.g. detectors slated for Phase 7,
the Hermes one-sentence gate, news-based conditions) are reported as `pending`, never silently passed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.detectors import DetectorResult
from app.models import Condition


@dataclass(slots=True)
class ConditionOutcome:
    detector: str
    field: str | None
    op: str
    value: Any
    observed: Any
    passed: bool | None          # None = not evaluable
    status: str                  # pass | fail | pending
    quality: str = "exact"
    label: str | None = None
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _observed(res: DetectorResult, field: str | None):
    if field:
        return res.fields.get(field)
    return res.value


def compare(op: str, observed: Any, value: Any) -> bool:
    if op == "true":
        return bool(observed) if observed is not None else False
    if op == "false":
        return not bool(observed)
    if observed is None:
        return False
    if op == ">":
        return observed > value
    if op == ">=":
        return observed >= value
    if op == "<":
        return observed < value
    if op == "<=":
        return observed <= value
    if op == "==":
        return observed == value
    if op == "between":
        lo, hi = value
        return lo <= observed <= hi
    if op == "in":
        return observed in value
    raise ValueError(f"unknown op {op!r}")


def evaluate(cond: Condition, res: DetectorResult | None) -> ConditionOutcome:
    """`res` is None when the detector is not implemented / could not run for this symbol."""
    base = dict(detector=cond.detector, field=cond.field, op=cond.op, value=cond.value, label=cond.label)
    if res is None:
        return ConditionOutcome(**base, observed=None, passed=None, status="pending", quality="degraded",
                                notes="detector not available at scan time")
    obs = _observed(res, cond.field)
    if obs is None and cond.op not in ("true", "false"):
        return ConditionOutcome(**base, observed=None, passed=None, status="pending", quality=res.quality,
                                notes=res.notes or "no value")
    ok = compare(cond.op, obs, cond.value)
    return ConditionOutcome(**base, observed=obs, passed=ok, status="pass" if ok else "fail", quality=res.quality,
                            notes=res.notes)


def summarize(outcomes: list[ConditionOutcome]) -> dict:
    """Hard-gate verdict for a list of `all` conditions."""
    failed = [o for o in outcomes if o.status == "fail"]
    pending = [o for o in outcomes if o.status == "pending"]
    return {
        "passed": not failed,
        "failed": [o.label or f"{o.detector}{'.' + o.field if o.field else ''} {o.op} {o.value}" for o in failed],
        "pending": [o.label or o.detector for o in pending],
        "approximate": [o.detector for o in outcomes if o.status == "pass" and o.quality != "exact"],
    }
