"""Alpaca account activities — fills and the regulatory fees that follow them (Guardian Layer 2, §11).

`alpaca-py` 0.44 dropped its activities helper, so this calls the REST endpoint through the client's
raw `get()`. Verified 2026-09-10.

An honest limitation, discovered the same day and worth stating plainly: **a paper account produces FILL
activities but no SEC/TAF fee activities**, because those are real regulatory charges that only exist on
live money. So the ledger reads real fees when they are there and otherwise computes an ESTIMATE from the
published rates, marked `estimated: true` in the row. It is never presented as an actual charge — plan
§33 forbids the fake, not the estimate.
"""

from __future__ import annotations

import datetime as dt
import logging

from app.config import get_settings

log = logging.getLogger(__name__)

# Published rates. They change (the SEC rate is reset annually), so they live in app_settings.fee_model
# and these are only the fallback defaults.
DEFAULT_SEC_FEE_PER_DOLLAR = 27.80 / 1_000_000      # sells only, on proceeds
DEFAULT_TAF_PER_SHARE = 0.000166                    # sells only
DEFAULT_TAF_MAX = 8.30                              # per trade


def _client():
    from alpaca.trading.client import TradingClient

    s = get_settings()
    return TradingClient(s.alpaca_paper_key_id, s.alpaca_paper_secret_key, paper=True)


def fetch(day: dt.date, activity_types: str | None = None) -> list[dict]:
    """All activities for one day. Returns [] and logs rather than raising — never blocks the EOD job."""
    params: dict = {"date": day.isoformat()}
    if activity_types:
        params["activity_types"] = activity_types
    try:
        rows = _client().get("/account/activities", params)
    except Exception as e:  # noqa: BLE001
        log.error("account activities unavailable for %s: %s", day, e)
        return []
    return rows if isinstance(rows, list) else []


def fills(day: dt.date) -> list[dict]:
    return [a for a in fetch(day) if a.get("activity_type") == "FILL"]


def fee_activities(day: dt.date) -> list[dict]:
    """Real regulatory fees. Empty on a paper account — see the module docstring."""
    return [a for a in fetch(day) if a.get("activity_type") in ("FEE", "SEC", "TAF", "REG")]


def fee_model() -> dict:
    from app.marketdata import repo as md_repo

    cfg = md_repo.fetch_setting("fee_model", {}) or {}
    return {
        "sec_fee_per_dollar": float(cfg.get("sec_fee_per_dollar", DEFAULT_SEC_FEE_PER_DOLLAR)),
        "taf_per_share": float(cfg.get("taf_per_share", DEFAULT_TAF_PER_SHARE)),
        "taf_max": float(cfg.get("taf_max", DEFAULT_TAF_MAX)),
    }


def estimate_fees(*, side: str, qty: int, price: float, model: dict | None = None) -> dict:
    """SEC + TAF for one fill. Both are charged on SELLS only; a buy costs nothing in these terms."""
    m = model or fee_model()
    if side != "sell" or qty <= 0 or price <= 0:
        return {"sec": 0.0, "taf": 0.0, "total": 0.0, "estimated": True,
                "basis": "buys incur no SEC/TAF"}
    proceeds = qty * price
    sec = round(proceeds * m["sec_fee_per_dollar"], 4)
    taf = round(min(qty * m["taf_per_share"], m["taf_max"]), 4)
    return {"sec": sec, "taf": taf, "total": round(sec + taf, 4), "estimated": True,
            "basis": f"SEC {m['sec_fee_per_dollar'] * 1e6:.2f}/$1M of {proceeds:,.2f} proceeds; "
                     f"TAF {m['taf_per_share']}/share x {qty} (cap {m['taf_max']})"}
