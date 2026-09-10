"""Template 04 — the one-sentence in-play test (F-A "Reason" gate, plan §8).

"SYMBOL is in play today because ___". PASS needs a fresh, specific, revaluation-forcing catalyst;
"moved yesterday", "looks interesting", sympathy with no own news and stale headlines are rejects.

Run before template 02 so a rejected candidate never costs a plan call — that is the whole reason the
spec keeps this separate from template 02, which applies the same test internally (template 04 Notes).

Decision 29 (owner's call): a reject records its verdict in `scans.metrics.in_play` and does NOT flip
`scans.passed`. `passed` keeps meaning "the code-based filters passed", so where code and Hermes
disagree stays visible — which is exactly the record needed to judge whether the gate can be trusted.
"""

from __future__ import annotations

import datetime as dt
import logging

from app.db import get_client
from app.llm import LLMError, complete_json
from app.llm.prompts import load as load_prompt

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "one_sentence_in_play_test"


def gate_payload(candidates: list[dict]) -> list[dict]:
    """Template 04 asks for a deliberately thin object: symbol, gap, catalysts. Nothing else is its business."""
    return [{"symbol": c["symbol"], "gap_pct": c["metrics"].get("gap_pct"), "catalysts": c["catalysts"]}
            for c in candidates]


def auto_reject_no_catalyst(candidates: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Split off candidates with no catalyst at all — code rejects those, the model never sees them.

    "X is in play today because ___" cannot be completed from an empty catalyst list, so asking a paid
    model to say so is both wasteful and unanswerable: with nothing to weigh it cannot report a
    `strength`, and the answer fails schema validation (observed repeatedly on 2026-09-10). Deciding it
    here is deterministic, free, and gives a clearer reason than the model would.
    """
    asked, rejected = [], {}
    for c in candidates:
        if c.get("catalysts"):
            asked.append(c)
        else:
            rejected[c["symbol"]] = {
                "symbol": c["symbol"], "verdict": "reject", "sentence": "",
                "catalyst_type": "none", "strength": "weak",
                "reject_reason": "no catalyst found by any enabled news source",
                "decided_by": "code",
            }
    return asked, rejected


def run_gate(candidates: list[dict], day: dt.date) -> tuple[dict[str, dict], dict]:
    """Returns ({symbol: verdict_obj}, meta). Raises LLMError — the caller decides what a failure means."""
    if not candidates:
        return {}, {"skipped": "no candidates"}
    asked, verdicts = auto_reject_no_catalyst(candidates)
    if not asked:
        return verdicts, {"skipped": "every candidate lacked a catalyst; no model call made",
                          "auto_rejected": len(verdicts)}

    spec = load_prompt(TEMPLATE_SLUG)
    import json

    payload, meta = complete_json(spec, {
        "trade_date": day.isoformat(),
        "candidates_json": json.dumps(gate_payload(asked), separators=(",", ":"), default=str),
    })
    meta["auto_rejected"] = len(verdicts)
    by_symbol = dict(verdicts)          # the code-decided rejections stand
    for row in payload.get("results", []):
        sym = (row.get("symbol") or "").upper()
        if sym:
            by_symbol[sym] = row | {"decided_by": "hermes"}
    # A candidate the model silently dropped is NOT a pass — record it as unknown, never assume.
    for c in candidates:
        by_symbol.setdefault(c["symbol"], {
            "symbol": c["symbol"], "verdict": "reject", "sentence": "",
            "catalyst_type": None, "strength": "weak",
            "reject_reason": "the gate returned no verdict for this symbol",
            "decided_by": "code",
        })
    return by_symbol, meta


def write_verdicts(day: dt.date, verdicts: dict[str, dict], strategy_id: str | None = None) -> int:
    """Record each verdict in `scans.metrics.in_play`. `passed` is never modified (decision 29)."""
    if not verdicts:
        return 0
    client = get_client()
    q = (client.table("scans").select("*").eq("scan_date", day.isoformat()).eq("run_kind", "morning")
         .in_("symbol", sorted(verdicts)))
    if strategy_id:
        q = q.eq("strategy_id", strategy_id)
    rows = q.execute().data or []
    touched = []
    for r in rows:
        v = verdicts.get(r["symbol"])
        if not v:
            continue
        metrics = dict(r.get("metrics") or {})
        metrics["in_play"] = {"verdict": v.get("verdict"), "sentence": v.get("sentence"),
                              "catalyst_type": v.get("catalyst_type"), "strength": v.get("strength"),
                              "reject_reason": v.get("reject_reason"),
                              "decided_by": v.get("decided_by")}
        touched.append({"strategy_id": r["strategy_id"], "scan_date": r["scan_date"], "run_kind": r["run_kind"],
                        "symbol": r["symbol"], "run_at": r["run_at"], "passed": r["passed"],
                        "rank": r.get("rank"), "reject_reason": r.get("reject_reason"), "metrics": metrics})
    for i in range(0, len(touched), 500):
        client.table("scans").upsert(touched[i:i + 500],
                                     on_conflict="strategy_id,scan_date,symbol,run_kind").execute()
    return len(touched)


def survivors(candidates: list[dict], verdicts: dict[str, dict]) -> list[dict]:
    return [c for c in candidates if (verdicts.get(c["symbol"], {}).get("verdict") == "pass")]


def safe_run_gate(candidates: list[dict], day: dt.date) -> tuple[dict[str, dict], dict, str | None]:
    """Gate wrapper that turns an LLM failure into an explicit error instead of an implicit pass."""
    try:
        verdicts, meta = run_gate(candidates, day)
        return verdicts, meta, None
    except LLMError as e:
        return {}, {}, f"in-play gate failed: {str(e)[:240]}"
