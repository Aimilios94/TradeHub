"""Exit plugins (plan §9) — all nine implemented in app/exits/plugins.py.

Contract: an exit plugin is a small class with
    `initial(position, bars, **params) -> ExitOrder | None`   — stop/target to place at entry
    `on_bar(position, bars, state, **params) -> ExitAction | None` — ratchet / partial / full exit
Deterministic, no I/O. Names must match app/registry.py. The same classes drive both the
live position manager and the backtester, so behaviour cannot diverge between the two.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ExitOrder:
    kind: Literal["stop", "limit", "stop_limit", "trailing"]
    price: float | None = None
    trail_pct: float | None = None
    qty_pct: float = 100.0
    extended_hours: bool = False


@dataclass
class ExitAction:
    action: Literal["move_stop", "partial", "close", "none"]
    price: float | None = None
    qty_pct: float = 100.0
    reason: str = ""


_EXITS: dict[str, type] = {}


def register(name: str):
    from app.registry import EXIT_NAMES

    if name not in EXIT_NAMES:
        raise KeyError(f"'{name}' is not in app/registry.py EXITS")

    def deco(cls: type) -> type:
        _EXITS[name] = cls
        return cls

    return deco


def implemented() -> list[str]:
    return sorted(_EXITS)


def get(name: str) -> type:
    try:
        return _EXITS[name]
    except KeyError as e:
        raise NotImplementedError(f"exit plugin '{name}' is registered but not implemented") from e


# Implementations register themselves on import (kept at the bottom to avoid a circular import).
from app.exits import plugins  # noqa: F401
