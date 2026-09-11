"""Grading hypotheses — where "Hermes proposes, the backtester disposes" actually happens (plan §20).

A `hypotheses` row carries a config diff. Grading it means running the SAME range, the SAME universe
and the SAME fill model twice — once on the live config, once with the diff applied — and comparing
expectancy after costs. The row then becomes `validated` or `rejected` and points at the backtest that
decided it.

Three things this deliberately does not do:
  * it never applies the diff to `strategy_configs`. A validated hypothesis is evidence for a change
    the owner still has to approve (plan §20, decision #52);
  * it never re-checks the proposal's *idea*, only its numbers. The judgement guards — does it loosen a
    risk limit, does it name a plugin that exists, has it been rejected before — already ran in code at
    proposal time (`app/review/weekly.vet`), and they are re-run here because a row can be inserted by
    hand as well as by the weekly job;
  * it never rejects for a low win rate. Win rate is displayed and is not a gate.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from app.backtest import metrics as M
from app.backtest import run as run_mod
from app.db import get_client
from app.marketdata import alpaca
from app.marketdata import repo as md_repo
from app.notify import send_telegram
from app.review.weekly import _loosens_risk, _names_unknown_plugin

log = logging.getLogger(__name__)


def load_hypothesis(hypothesis_id: str) -> tuple[dict, dict]:
    rows = get_client().table("hypotheses").select("*").eq("id", hypothesis_id).execute().data or []
    if not rows:
        raise KeyError(f"no hypothesis {hypothesis_id}")
    h = rows[0]
    sc = (get_client().table("strategy_configs").select("*").eq("id", h["strategy_id"])
          .execute().data or [])
    if not sc:
        raise KeyError(f"hypothesis {hypothesis_id} points at a strategy that no longer exists")
    return h, sc[0]


def testable(h: dict, strategy_row: dict) -> str | None:
    """The reason this row cannot be graded, or None. Re-run here because rows can arrive by hand."""
    diff = h.get("proposed_change") or {}
    if not diff:
        return "no config diff — there is nothing to test"
    if h.get("status") not in ("proposed", "backtesting"):
        return f"status is '{h.get('status')}' — only a proposed hypothesis is graded"
    loose = _loosens_risk(diff, strategy_row)
    if loose:
        return f"loosens a risk limit ({loose}) — never allowed (plan §20)"
    unknown = _names_unknown_plugin(diff)
    if unknown:
        return unknown
    return None


def grade(hypothesis_id: str, date_from: dt.date, date_to: dt.date, *, equity: float = run_mod.DEFAULT_EQUITY,
          universe_cap: int = 1500, persist: bool = True) -> dict:
    h, sc = load_hypothesis(hypothesis_id)
    code = sc["code"]
    why = testable(h, sc)
    if why:
        if persist:
            _decide(hypothesis_id, "rejected", why, None)
        return {"hypothesis_id": hypothesis_id, "strategy": code, "status": "rejected", "reason": why,
                "note": "rejected rows are kept forever as anti-re-suggestion memory (plan §20)"}

    if persist:
        get_client().table("hypotheses").update({"status": "backtesting"}).eq("id", hypothesis_id).execute()

    diff = h["proposed_change"]
    baseline = run_mod.run_backtest(code, date_from, date_to, equity=equity, universe_cap=universe_cap,
                                    persist=persist, label=f"{code} (current)")
    proposed = run_mod.run_backtest(code, date_from, date_to, equity=equity, universe_cap=universe_cap,
                                    persist=persist, config_override=diff, hypothesis_id=hypothesis_id,
                                    label=f"{code} (proposed)")
    if baseline.status != "done" or proposed.status != "done":
        reason = baseline.error or proposed.error or "a run did not complete"
        if persist:
            _decide(hypothesis_id, "proposed", f"not graded: {reason}", None)
        return {"hypothesis_id": hypothesis_id, "strategy": code, "status": "proposed",
                "reason": f"could not grade: {reason}",
                "note": "the hypothesis stays proposed — a failed run is not a verdict"}

    cmp = M.compare(proposed.metrics, baseline.metrics, label_a="proposed", label_b="current")
    criteria = dict(md_repo.fetch_setting("promotion_criteria", {}) or {})
    criteria["_equity"] = equity
    style = str(sc.get("style") or "day")
    v, detail = M.verdict(proposed.metrics, criteria, style=style, ab=cmp, walk=proposed.walk_forward)

    status = "validated" if v == "pass" else "rejected"
    note = _note(v, cmp, detail)
    if persist:
        _decide(hypothesis_id, status, note, proposed.backtest_id)
    return {"hypothesis_id": hypothesis_id, "strategy": code, "status": status, "verdict": v,
            "comparison": cmp, "verdict_detail": detail, "note": note,
            "backtest_id": proposed.backtest_id, "baseline_backtest_id": baseline.backtest_id,
            "applied": False,
            "next_step": ("nothing is applied automatically. Approve it with "
                          f"POST /review/hypotheses/{hypothesis_id}/decide to record the promotion."),
            }


def _note(v: str, cmp: dict, detail: dict) -> str:
    d = cmp.get("expectancy_delta")
    head = {"pass": "validated", "fail": "rejected", "inconclusive": "not settled"}[v]
    bits = [f"{head} by backtest"]
    if d is not None:
        bits.append(f"expectancy {d:+.4f}/trade vs the current config")
    if detail.get("failed"):
        bits.append("failed: " + ", ".join(detail["failed"]))
    if detail.get("unknown"):
        bits.append("unanswered: " + ", ".join(detail["unknown"]))
    if not cmp.get("confident"):
        bits.append("sample is small — treat the comparison as a hint")
    return "; ".join(bits)


def _decide(hypothesis_id: str, status: str, note: str, backtest_id: str | None) -> None:
    payload: dict[str, Any] = {"status": status, "decision_note": note,
                               "decided_at": dt.datetime.now(dt.UTC).isoformat()}
    if backtest_id:
        payload["backtest_id"] = backtest_id
    get_client().table("hypotheses").update(payload).eq("id", hypothesis_id).execute()


def run_queue(*, limit: int = 5, days: int = 365, notify: bool = True) -> dict:
    """Grade the proposed hypotheses that carry a config diff. Everything else is reported untouched."""
    rows = (get_client().table("hypotheses").select("*").eq("status", "proposed")
            .order("created_at").limit(limit * 3).execute().data or [])
    to_date = alpaca.now_et().date() - dt.timedelta(days=1)
    from_date = to_date - dt.timedelta(days=days)
    graded, skipped = [], []
    for h in rows:
        if len(graded) >= limit:
            break
        if not (h.get("proposed_change") or {}):
            skipped.append({"id": h["id"], "title": h.get("title"),
                            "reason": "no config diff — an observation, not a testable change"})
            continue
        try:
            graded.append(grade(h["id"], from_date, to_date))
        except Exception as e:  # noqa: BLE001
            log.warning("grading %s failed: %s", h["id"], e)
            skipped.append({"id": h["id"], "title": h.get("title"), "reason": f"error: {e}"})
    out = {"from": from_date.isoformat(), "to": to_date.isoformat(), "graded": graded,
           "skipped": skipped, "queue_seen": len(rows)}
    if notify:
        send_telegram(_summary(out))
    return out


def _summary(out: dict) -> str:
    lines = [f"🧪 Hypothesis grading {out['from']}→{out['to']}"]
    if not out["graded"]:
        lines.append("nothing testable in the queue" + (f" ({len(out['skipped'])} skipped)" if out["skipped"] else ""))
    for g in out["graded"]:
        lines.append(f"{g['strategy']}: {g['status']} — {g.get('note', '')[:140]}")
    if out["graded"]:
        lines.append("Nothing was applied. Validated ideas wait for your approval.")
    return "\n".join(lines)
