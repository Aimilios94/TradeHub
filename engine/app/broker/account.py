"""Account + positions snapshot from Alpaca (paper). Read-only — see `app/broker/__init__.py`.

Feeds the ACCOUNT block of prompt template 02 and the advisory pre-check. Every call fails SOFT: a
broker outage must not stop the morning plan, it must be reported as unknown so Hermes lowers
confidence (template 02 system rule 8) rather than planning against invented buying power.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import get_settings

log = logging.getLogger(__name__)


def _trading_client():
    from alpaca.trading.client import TradingClient

    s = get_settings()
    if not s.alpaca_configured:
        raise RuntimeError("Alpaca keys are not configured")
    return TradingClient(s.alpaca_paper_key_id, s.alpaca_paper_secret_key, paper=s.paper_mode)


@dataclass(slots=True)
class AccountSnapshot:
    ok: bool
    equity: float | None = None
    buying_power: float | None = None
    cash: float | None = None
    daytrade_count: int | None = None
    pattern_day_trader: bool | None = None
    trading_blocked: bool | None = None
    account_blocked: bool | None = None
    positions: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def open_positions(self) -> int:
        return len(self.positions)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "equity": self.equity, "buying_power": self.buying_power, "cash": self.cash,
                "open_positions": self.open_positions, "positions": self.positions,
                "daytrade_count": self.daytrade_count, "pattern_day_trader": self.pattern_day_trader,
                "trading_blocked": self.trading_blocked, "account_blocked": self.account_blocked,
                "error": self.error}


def snapshot() -> AccountSnapshot:
    """Equity, buying power and open positions. Never raises.

    Retried (Phase 8, decision #79): this is a GET, so it is idempotent, and it is the most frequently
    made external call in the system — the stop monitor runs it every 30 seconds, and every Guardian
    gate evaluation runs it once. Without a retry a single dropped keep-alive or a momentary DNS
    failure became a recorded `failure` row at red severity; over the first eleven days of the paper
    month that produced seven of them, every one self-healed on the next cycle (decision #97).
    """
    from app.ops import resilience

    try:
        client = _trading_client()
        acct = resilience.call("alpaca_trading", client.get_account, what="get_account")
        positions = resilience.call("alpaca_trading", client.get_all_positions, what="get_all_positions")
    except Exception as e:  # noqa: BLE001
        log.warning("broker snapshot failed after retries: %s", e)
        return AccountSnapshot(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")

    def num(v):
        return float(v) if v is not None else None

    return AccountSnapshot(
        ok=True,
        equity=num(getattr(acct, "equity", None)),
        buying_power=num(getattr(acct, "buying_power", None)),
        cash=num(getattr(acct, "cash", None)),
        daytrade_count=getattr(acct, "daytrade_count", None),
        pattern_day_trader=getattr(acct, "pattern_day_trader", None),
        trading_blocked=getattr(acct, "trading_blocked", None),
        account_blocked=getattr(acct, "account_blocked", None),
        positions=[{
            "symbol": p.symbol,
            "qty": num(p.qty),
            "side": getattr(p.side, "value", str(p.side)),
            "avg_entry_price": num(p.avg_entry_price),
            "unrealized_pl": num(getattr(p, "unrealized_pl", None)),
            "market_value": num(getattr(p, "market_value", None)),
        } for p in positions],
    )
