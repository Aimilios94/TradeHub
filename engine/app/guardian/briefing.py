"""Guardian Layer 3 — the on-demand briefing (plan §11, template 07).

Gathers what the account actually is right now — equity, buying power, open positions, ledger totals,
YTD tax provision, recent compliance events, rules freshness — and asks Hermes to say it in plain
language. Conversational: an optional question rides along.

The hard boundary is in the prompt and repeated here because it matters: **this is a monitor, not an
advisor.** It computes nothing beyond restating supplied figures, the tax line always carries its
"confirm with an accountant" disclaimer, and anything missing is called missing rather than glossed over.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from app.broker import account as broker_account
from app.config import get_settings
from app.db import get_client, kill_switch_engaged
from app.guardian import rules as guardian_rules
from app.journal import ledger as jledger
from app.llm import LLMError, complete_json
from app.llm.prompts import load as load_prompt
from app.marketdata import alpaca
from app.notify import send_telegram

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "guardian_briefing"
TAX_DISCLAIMER = ("This is a display estimate at the Portuguese flat 28% rate; "
                  "confirm with an accountant.")


def recent_compliance(days: int = 14, limit: int = 40) -> list[dict]:
    since = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=days)).isoformat()
    try:
        rows = (get_client().table("compliance_events").select("event_time,kind,verdict,symbol,reason")
                .gte("event_time", since).order("event_time", desc=True).limit(limit).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("compliance events unavailable: %s", e)
        return []
    return rows


def previous_briefing() -> dict | None:
    try:
        rows = (get_client().table("briefings").select("briefing_date,market_context,rows")
                .eq("kind", "guardian").order("briefing_date", desc=True).limit(1).execute().data or [])
    except Exception:  # noqa: BLE001
        return None
    return rows[0] if rows else None


def ledger_block() -> dict:
    """Month-to-date and last 365 days, so "costs this month" and the YTD provision are both answerable."""
    now = alpaca.now_et()
    month_days = max(now.day, 1)
    return {"month_to_date": jledger.summary(days=month_days),
            "last_365_days": jledger.summary(days=365),
            "tax_note": TAX_DISCLAIMER}


def gather(question: str = "") -> dict:
    """Everything the briefing is shown. Every figure is computed by code."""
    s = get_settings()
    acct = broker_account.snapshot()
    return {
        "as_of": alpaca.now_et().isoformat(),
        "paper_or_live": "PAPER" if s.paper_mode else "LIVE",
        "kill_switch": kill_switch_engaged(),
        "account": acct.as_dict() | {"intraday_margin_call_events": []},
        "ledger": ledger_block(),
        "compliance_events": recent_compliance(),
        "portfolio_risk_summary": None,      # E1 (the Portfolio Risk module, plan §22) — later phase
        "rules_file": guardian_rules.rules_summary(),
        "previous_briefing": previous_briefing(),
        "user_question": question,
    }


def run_briefing(*, question: str = "", notify: bool = True, store: bool = True) -> dict:
    """Layer 3. Returns the briefing payload plus the inputs it was based on."""
    inputs = gather(question)
    spec = load_prompt(TEMPLATE_SLUG)
    try:
        payload, meta = complete_json(spec, {
            "as_of": inputs["as_of"], "paper_or_live": inputs["paper_or_live"],
            "kill_switch": inputs["kill_switch"],
            "account_json": json.dumps(inputs["account"], default=str),
            "ledger_json": json.dumps(inputs["ledger"], default=str),
            "compliance_events_json": json.dumps(inputs["compliance_events"], default=str),
            "portfolio_risk_summary": json.dumps(inputs["portfolio_risk_summary"]),
            "rules_file_summary": json.dumps(inputs["rules_file"], default=str),
            "previous_briefing_summary": json.dumps(inputs["previous_briefing"], default=str),
            "user_question": question or "",
        })
    except LLMError as e:
        out = {"error": str(e)[:300], "inputs": inputs,
               "note": "the briefing could not be generated; the figures above are still accurate"}
        if notify:
            send_telegram(f"🛡️ Guardian briefing FAILED: {str(e)[:200]}\n"
                          f"Figures are unaffected — check /analytics/ledger directly.")
        return out

    # The disclaimer is not left to the model's discretion.
    tax_line = str(payload.get("tax_line") or "")
    if "accountant" not in tax_line.lower():
        payload["tax_line"] = (tax_line + " " + TAX_DISCLAIMER).strip()

    result = {"as_of": inputs["as_of"], "model": meta.get("model"), "briefing": payload,
              "inputs": inputs, "boundary": "monitor, not an advisor — no tax, legal or investment advice"}

    if store:
        try:
            get_client().table("briefings").upsert([{
                "briefing_date": alpaca.now_et().date().isoformat(), "kind": "guardian",
                "as_of_et": alpaca.now_et().strftime("%H:%M"),
                "market_context": payload.get("summary"),
                "rows": payload.get("warnings") or [],
                "inputs": inputs, "model": meta.get("model"), "raw_llm_response": payload,
                "usage": {"attempts": meta.get("attempts"), "tier": meta.get("tier")},
            }], on_conflict="briefing_date,kind").execute()
        except Exception as e:  # noqa: BLE001
            log.warning("guardian briefing not stored: %s", e)

    if notify:
        send_telegram(telegram_text(payload, inputs))
    return result


def telegram_text(payload: dict, inputs: dict) -> str:
    lines = [f"🛡️ Guardian briefing — {inputs['as_of'][:16]} ET ({inputs['paper_or_live']})"]
    if inputs.get("kill_switch"):
        lines.append("🛑 KILL SWITCH ENGAGED")
    lines.append((payload.get("summary") or "")[:600])
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append("\nWarnings:")
        for w in warnings[:5]:
            lines.append(f"   • {w if isinstance(w, str) else json.dumps(w)[:160]}")
    costs = payload.get("costs")
    if costs:
        lines.append(f"\nCosts: {costs if isinstance(costs, str) else json.dumps(costs)[:200]}")
    if payload.get("tax_line"):
        lines.append(f"\n{payload['tax_line'][:300]}")
    if payload.get("rules_status"):
        lines.append(f"\nRules: {str(payload['rules_status'])[:200]}")
    for s in (payload.get("suggestions") or [])[:3]:
        lines.append(f"   💡 {s if isinstance(s, str) else json.dumps(s)[:180]}")
    return "\n".join(lines)
