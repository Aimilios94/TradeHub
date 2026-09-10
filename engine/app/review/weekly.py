"""Template 06 — the Sunday review: the week's trades + analytics → lessons and `hypotheses` (plan §20).

The loop the whole system is built around: the journal records experience, this proposes testable
changes, the Phase 7 backtester grades them, and the owner approves or rejects. **Nothing is ever applied
automatically** — a hypothesis is a row with a config diff, cited evidence and a status, and it starts
at `proposed`.

Three guards live here rather than in the prompt, because a prompt is a request and these are rules:

  * previously rejected hypotheses are passed in so the model cannot re-propose them blind, and any that
    come back anyway are dropped in code (`_is_rejected_repeat`)
  * a proposal that names a detector, exit plugin or strategy that does not exist is discarded — the
    registry is the authority on what is expressible
  * a proposal that would loosen a Guardian risk limit is discarded outright (§20)

The degradation trigger is the defensive twin: a strategy whose recent expectancy has gone negative over
a sufficient sample raises a `review` hypothesis by itself, with no model involved.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field

from app.analytics import stats as analytics
from app.db import get_client
from app.llm import LLMError, complete_json
from app.llm.prompts import load as load_prompt
from app.marketdata import repo as md_repo
from app.notify import send_telegram
from app.registry import DETECTOR_NAMES, EXIT_NAMES
from app.review import autopsy as autopsy_mod

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "weekly_autopsy_hypotheses"
RISK_KEYS_THAT_MAY_NOT_LOOSEN = ("risk_per_trade_pct", "daily_loss_limit_r", "max_concurrent",
                                 "spread_ceiling_pct", "buying_power_buffer_pct")


def week_bounds(day: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The completed week ending last Saturday, for a Sunday run."""
    day = day or dt.datetime.now(tz=dt.UTC).date()
    end = day - dt.timedelta(days=(day.weekday() + 1) % 7 or 7) if day.weekday() != 6 else day - dt.timedelta(days=1)
    return end - dt.timedelta(days=6), end


def available_plugins() -> dict:
    from app.registry import ALL_PLUGINS

    return {"detectors": sorted(DETECTOR_NAMES), "exits": sorted(EXIT_NAMES),
            "params": {p["name"]: p.get("params", {}) for p in ALL_PLUGINS
                       if p["status"] == "implemented"}}


def rejected_hypotheses(limit: int = 50) -> list[dict]:
    try:
        rows = (get_client().table("hypotheses").select("title,why,proposed_change,decision_note")
                .eq("status", "rejected").order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.warning("rejected hypotheses unavailable: %s", e)
        return []
    return rows


def trades_with_autopsies(since: dt.date, until: dt.date, limit: int = 60) -> list[dict]:
    rows = (get_client().table("journal").select("*")
            .gte("exit_time", f"{since.isoformat()}T00:00:00")
            .lte("exit_time", f"{until.isoformat()}T23:59:59")
            .order("entry_time", desc=False).limit(limit).execute().data or [])
    codes = analytics.strategy_codes()
    out = []
    for r in rows:
        a = r.get("autopsy") or {}
        out.append({
            "journal_id": r["id"], "strategy_code": codes.get(r.get("strategy_id"), "(unlinked)"),
            "symbol": r["symbol"], "direction": r["direction"],
            "entry_time": str(r["entry_time"]), "r_multiple": r.get("r_multiple"),
            "pnl_net": r.get("pnl_net"), "setup_type": r.get("setup_type"),
            "time_block": r.get("time_block"),
            "autopsy": {"violations": a.get("violations") or a.get("rule_violations"),
                        "behavioral_flags": a.get("behavioral_flags") or a.get("pattern"),
                        "lesson": a.get("lesson") or a.get("focus") or a.get("one_thing")},
        })
    return out


def live_strategy_configs() -> list[dict]:
    rows = (get_client().table("strategy_configs").select("*")
            .in_("status", ["paper", "live"]).execute().data or [])
    return [{k: r[k] for k in ("code", "name", "status", "universe", "entry_conditions", "exit_logic",
                               "risk") if k in r} for r in rows]


# ---------- code-side guards on what comes back ----------

def _loosens_risk(diff: dict, current: dict) -> str | None:
    risk = (diff or {}).get("risk") or {}
    now = (current or {}).get("risk") or {}
    for key in RISK_KEYS_THAT_MAY_NOT_LOOSEN:
        if key not in risk or now.get(key) is None:
            continue
        try:
            proposed, existing = float(risk[key]), float(now[key])
        except (TypeError, ValueError):
            continue
        if proposed > existing:      # every one of these is "lower is stricter"
            return f"would raise {key} from {existing} to {proposed}"
    return None


def _names_unknown_plugin(diff: dict) -> str | None:
    text = json.dumps(diff or {})
    for block in ("entry_conditions", "exit_logic"):
        for item in (diff or {}).get(block, []) or []:
            name = item.get("detector") or item.get("plugin")
            if name and name not in DETECTOR_NAMES and name not in EXIT_NAMES:
                return f"names '{name}', which is not in the plugin registry"
    if '"plugin"' in text or '"detector"' in text:
        return None
    return None


def _is_rejected_repeat(title: str, rejected: list[dict]) -> bool:
    t = (title or "").strip().lower()
    return any((r.get("title") or "").strip().lower() == t for r in rejected)


def vet(hypothesis: dict, configs: dict[str, dict], rejected: list[dict]) -> str | None:
    """Returns the reason this proposal must be discarded, or None if it may be stored."""
    code = hypothesis.get("strategy_code") or hypothesis.get("strategy")
    if not code or code not in configs:
        return f"names an unknown strategy {code!r}"
    diff = hypothesis.get("proposed_change") or hypothesis.get("config_diff") or {}
    if hypothesis.get("kind") in ("observe", "review"):
        return None
    if not diff:
        return "a parameter change with no config diff cannot be tested"
    loose = _loosens_risk(diff, configs[code])
    if loose:
        return f"loosens a risk limit ({loose}) — never allowed (plan §20)"
    unknown = _names_unknown_plugin(diff)
    if unknown:
        return unknown
    if _is_rejected_repeat(hypothesis.get("title", ""), rejected):
        return "was already proposed and rejected; needs new evidence and a new framing"
    return None


# ---------- degradation trigger ----------

def degradation_flags(report: dict, min_trades: int) -> list[dict]:
    """A strategy losing money over a sufficient sample raises its own review, with no model involved."""
    out = []
    for block in report.get("by_strategy", []):
        exp = block.get("expectancy")
        if exp is None or block.get("strategy") == "(unlinked)":
            continue
        enough = block["trades"] >= max(5, min_trades // 10)   # a tenth of the promotion bar, floor 5
        if enough and exp < 0:
            out.append({"strategy_code": block["strategy"], "kind": "review",
                        "title": f"Review {block['strategy']}: negative expectancy over "
                                 f"{block['trades']} trades",
                        "why": f"expectancy {exp:+.2f} per trade after costs across {block['trades']} "
                               f"trades (profit factor {block.get('profit_factor')}, drawdown "
                               f"{block.get('max_drawdown')}). Raised automatically by the degradation "
                               f"trigger, not by Hermes.",
                        "evidence": block, "source": "degradation_alert"})
    return out


# ---------- the run ----------

@dataclass(slots=True)
class WeeklyResult:
    week_start: str
    week_end: str
    autopsies: dict = field(default_factory=dict)
    analytics: dict = field(default_factory=dict)
    lessons: list[str] = field(default_factory=list)
    hypotheses_written: int = 0
    proposals: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"week_start": self.week_start, "week_end": self.week_end, "autopsies": self.autopsies,
                "analytics": self.analytics, "lessons": self.lessons,
                "hypotheses_written": self.hypotheses_written, "proposals": self.proposals,
                "discarded": self.discarded, "errors": self.errors, "notes": self.notes}

    def summary(self) -> str:
        lines = [f"🗓️ Weekly review {self.week_start} → {self.week_end}"]
        o = (self.analytics or {}).get("overall", {})
        if o.get("trades"):
            lines.append(f"{o['trades']} trades · {o['total_pnl']:+.2f} net · "
                         f"expectancy {o['expectancy']:+.2f}/trade · win rate {o['win_rate_pct']}%")
        else:
            lines.append("no closed trades this week")
        for x in self.lessons[:5]:
            lines.append(f"   • {x}")
        for p in self.proposals[:5]:
            lines.append(f"   💡 {p.get('strategy_code')}: {p.get('title')}")
        for d in self.discarded[:3]:
            lines.append(f"   🚫 discarded: {d.get('title')} — {d.get('reason')}")
        for n in self.notes:
            lines.append(f"ℹ️ {n}")
        if self.errors:
            lines.append(f"⚠️ {len(self.errors)} error(s): {self.errors[0][:120]}")
        lines.append("Proposals only — each needs a backtest and your approval before anything changes.")
        return "\n".join(lines)


def store(proposals: list[dict], configs_by_code: dict[str, dict], source: str) -> int:
    ids = {c["code"]: c["id"] for c in
           (get_client().table("strategy_configs").select("id,code").execute().data or [])}
    rows = []
    for p in proposals:
        code = p.get("strategy_code") or p.get("strategy")
        sid = ids.get(code)
        if not sid:
            continue
        rows.append({
            "strategy_id": sid, "title": (p.get("title") or "untitled")[:300],
            "proposed_change": p.get("proposed_change") or p.get("config_diff") or {},
            "why": p.get("why") or p.get("reasoning") or "",
            "evidence": p.get("evidence") or {"journal_ids": p.get("journal_ids") or []},
            "source": p.get("source", source), "status": "proposed",
        })
    if not rows:
        return 0
    try:
        get_client().table("hypotheses").insert(rows).execute()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.error("hypotheses write failed: %s", e)
        return 0


def run_weekly(*, week_start: dt.date | None = None, week_end: dt.date | None = None,
               run_autopsies: bool = True, notify: bool = True, dry_run: bool = False) -> WeeklyResult:
    if week_start is None or week_end is None:
        week_start, week_end = week_bounds()
    res = WeeklyResult(week_start=week_start.isoformat(), week_end=week_end.isoformat())

    if run_autopsies:
        try:
            res.autopsies = autopsy_mod.run_batch(since=week_start, until=week_end)
            res.errors += res.autopsies.get("errors", [])
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"autopsy batch: {type(e).__name__}: {str(e)[:160]}")

    res.analytics = analytics.report(since=week_start, until=week_end)
    min_day = res.analytics.get("min_trades_day", 100)

    auto = degradation_flags(res.analytics, min_day)
    if auto:
        res.notes.append(f"{len(auto)} strategy review(s) raised automatically by the degradation trigger")

    if not res.analytics.get("overall", {}).get("trades"):
        res.notes.append("no closed trades — nothing for Hermes to review this week")
        if not dry_run and auto:
            res.hypotheses_written += store(auto, {}, "degradation_alert")
        if notify:
            send_telegram(res.summary())
        return res

    configs = live_strategy_configs()
    by_code = {c["code"]: c for c in configs}
    rejected = rejected_hypotheses()
    crit = md_repo.fetch_setting("promotion_criteria", {}) or {}

    try:
        spec = load_prompt(TEMPLATE_SLUG)
        payload, meta = complete_json(spec, {
            "week_start": res.week_start, "week_end": res.week_end,
            "analytics_json": json.dumps(res.analytics | {"degradation_flags": auto}, default=str),
            "trades_json": json.dumps(trades_with_autopsies(week_start, week_end), default=str),
            "strategy_configs_json": json.dumps(configs, default=str),
            "available_plugins_json": json.dumps(available_plugins(), default=str),
            "rejected_hypotheses_json": json.dumps(rejected, default=str),
            "min_trades_day": crit.get("min_trades_day", 100),
            "min_trades_swing": crit.get("min_trades_swing", 40),
        })
    except LLMError as e:
        res.errors.append(f"weekly review: {str(e)[:200]}")
        if not dry_run and auto:
            res.hypotheses_written += store(auto, by_code, "degradation_alert")
        if notify:
            send_telegram(res.summary())
        return res

    res.lessons = [str(x) for x in (payload.get("lessons") or [])]
    for h in payload.get("hypotheses") or []:
        reason = vet(h, by_code, rejected)
        if reason:
            res.discarded.append({"title": h.get("title"), "reason": reason})
            continue
        res.proposals.append(h | {"source": "weekly_autopsy"})

    res.notes.append(f"model {meta.get('model')}, {meta.get('attempts')} attempt(s)")
    if dry_run:
        res.notes.append("dry_run: nothing written to hypotheses")
    else:
        res.hypotheses_written = store(res.proposals + auto, by_code, "weekly_autopsy")
    if notify:
        send_telegram(res.summary())
    return res
