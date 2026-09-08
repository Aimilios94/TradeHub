"""Detectors / indicators (plan §9, §27) — implemented in Phase 7, referenced from Phase 2.

Contract: every detector is a pure function `run(bars: pd.DataFrame, **params) -> DetectorResult`
where `bars` is an OHLCV frame indexed by ET timestamp for one symbol (plus context frames
where needed, e.g. daily bars for ADV). No I/O inside detectors — the scanner fetches data,
detectors compute. Names must match app/registry.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd


@dataclass
class DetectorResult:
    name: str
    value: Any                          # primary scalar (or dict for multi-output detectors)
    fields: dict[str, Any] = field(default_factory=dict)  # sub-outputs addressable via Condition.field
    series: pd.Series | pd.DataFrame | None = None        # optional full series for charts/backtests
    quality: str = "exact"              # exact | approximate (e.g. IEX-only volume) | degraded
    notes: str = ""


DetectorFn = Callable[..., DetectorResult]
_DETECTORS: dict[str, DetectorFn] = {}


def register(name: str):
    """Decorator: bind an implementation to a registry name. Unknown names are rejected at import time."""
    from app.registry import DETECTOR_NAMES

    if name not in DETECTOR_NAMES:
        raise KeyError(f"'{name}' is not in app/registry.py DETECTORS")

    def deco(fn: DetectorFn) -> DetectorFn:
        _DETECTORS[name] = fn
        return fn

    return deco


def implemented() -> list[str]:
    return sorted(_DETECTORS)


def get(name: str) -> DetectorFn:
    try:
        return _DETECTORS[name]
    except KeyError as e:
        raise NotImplementedError(f"detector '{name}' is registered but not implemented yet (Phase 7)") from e
