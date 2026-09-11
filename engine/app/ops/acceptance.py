"""The gate to real money (plan §17), measured from the database instead of remembered.

Phase 8 is mostly calendar time: four weeks of the whole pipeline running on paper, every day, without
anybody's help. The risk in a criterion like that is not that it fails — it is that four weeks pass, the
owner remembers it "mostly worked", and the memory becomes the evidence. So every criterion here is a
query, every answer carries the number it was computed from, and anything that cannot be computed comes
back `unknown` rather than absent.

Five criteria, §17 verbatim:
  1. all strategies backtested with the four countermeasures, recorded in `backtests` (+ the S6/S7 A/B
     and the IEX↔SIP calibration factor)
  2. ≥ 4 consecutive weeks of full paper operation — daily scans, signals, ≥ 20 journaled paper trades,
     autopsies, zero unhandled execution errors
  3. Guardian verified — a forced red-block per check, a ledger that reconciles to the cent, a tax
     provision that is accumulating
  4. latency verified — Deploy→order ≤ 2s, synthetic stop reaction < 1s
  5. the human steps — funding, W-8BEN, first live strategy chosen small. Never auto-satisfied: this
     one is reported as the owner's, permanently.

`met` is True only when the measurement says so. There is no override parameter, and criterion 5 is
listed as manual so that a scorecard reading "4 of 5, one is yours" cannot be mistaken for a green light.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MIN_PAPER_WEEKS = 4
MIN_PAPER_TRADES = 20
MIN_SCAN_DAYS_PER_WEEK = 4          # five weekdays less one for a market holiday
MAX_DEPLOY_LATENCY_MS = 2_000       # §17.4
MAX_STOP_REACTION_MS = 1_000        # §17.4
UNHANDLED_KINDS = ("failure",)      # what "zero unhandled execution errors" counts
NOT_BACKTESTABLE = {"S2"}           # a briefing has no entry/stop/target to replay (decision #60)
MIN_BASELINE_SPAN_DAYS = 90         # a fortnight is a smoke test; a baseline needs a quarter at least


@dataclass(slots=True)
class Criterion:
    number: int
    title: str
    met: bool | None                 # None = could not be measured
    detail: str
    evidence: dict = field(default_factory=dict)
    manual: bool = False

    @property
    def status(self) -> str:
        if self.manual:
            return "owner"
        return {True: "met", False: "not met", None: "unknown"}[self.met]

    def as_dict(self) -> dict:
        return {"number": self.number, "title": self.title, "status": self.status, "met": self.met,
                "manual": self.manual, "detail": self.detail, "evidence": self.evidence}


@dataclass(slots=True)
class Scorecard:
    criteria: list[Criterion]
    computed_at: str
    window: dict

    @property
    def ready_for_live(self) -> bool:
        """Every measurable criterion met AND the owner's own step acknowledged separately.

        Deliberately False while criterion 5 is outstanding: this function never says yes on its own.
        """
        return all(c.met is True for c in self.criteria if not c.manual) and any(
            c.manual and c.met is True for c in self.criteria)

    def as_dict(self) -> dict:
        return {"computed_at": self.computed_at, "window": self.window,
                "ready_for_live": self.ready_for_live,
                "met": sum(1 for c in self.criteria if c.met is True),
                "measurable": sum(1 for c in self.criteria if not c.manual),
                "criteria": [c.as_dict() for c in self.criteria],
                "note": ("Live trading stays refused in code regardless of this scorecard "
                         "(app/broker/orders.assert_paper_only, docs/DECISIONS.md #37). Lifting it is a "
                         "separate, owner-approved commit.")}


# --------------------------------------------------------------------------- window

def paper_month() -> dict:
    """`app_settings.paper_month` — when the paper run started and what it is aiming at."""
    from app.marketdata import repo as md_repo

    cfg = md_repo.fetch_setting("paper_month", {}) or {}
    return {"started_on": cfg.get("started_on"), "min_weeks": int(cfg.get("min_weeks", MIN_PAPER_WEEKS)),
            "min_trades": int(cfg.get("min_trades", MIN_PAPER_TRADES)), "note": cfg.get("note", "")}


def start_paper_month(day: dt.date | None = None) -> dict:
    """Stamp day zero. Restarting resets the clock — that is the point: §17 asks for four CONSECUTIVE
    weeks, so an outage that breaks the run has to be visible as a restart, not averaged away."""
    from app.db import get_client
    from app.marketdata import alpaca

    day = day or alpaca.now_et().date()
    cfg = paper_month() | {"started_on": day.isoformat()}
    get_client().table("app_settings").update({"value": cfg}).eq("key", "paper_month").execute()
    return cfg


def window(now: dt.datetime | None = None) -> dict:
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    cfg = paper_month()
    started = dt.date.fromisoformat(cfg["started_on"]) if cfg.get("started_on") else None
    return {"started_on": cfg.get("started_on"), "today": now.date().isoformat(),
            "days_elapsed": (now.date() - started).days if started else None,
            "min_weeks": cfg["min_weeks"], "min_trades": cfg["min_trades"]}


def _weeks(started: dt.date, until: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Consecutive 7-day blocks from day zero — not calendar weeks, so a Wednesday start is not
    penalised by a two-day first 'week'."""
    out, cur = [], started
    while cur + dt.timedelta(days=6) <= until:
        out.append((cur, cur + dt.timedelta(days=6)))
        cur += dt.timedelta(days=7)
    return out


# --------------------------------------------------------------------------- criteria

def criterion_1_backtests() -> Criterion:
    from app.db import get_client

    try:
        strategies = (get_client().table("strategy_configs").select("id,code,name").execute().data or [])
        runs = (get_client().table("backtests")
                .select("strategy_id,verdict,status,metrics,calibration,ab_against_backtest_id,"
                        "date_from,date_to")
                .eq("status", "done").execute().data or [])
    except Exception as e:  # noqa: BLE001
        return Criterion(1, "All strategies backtested with the four countermeasures", None,
                         f"could not read backtests: {type(e).__name__}: {str(e)[:160]}")

    by_id = {s["id"]: s["code"] for s in strategies}
    expected = {s["code"] for s in strategies} - NOT_BACKTESTABLE

    # A run that produced ZERO trades is not a baseline — it is the finding that the config never fires
    # (decisions #63 and #71, where five strategies were mutually-exclusive by construction for four
    # phases). Counting it would have reported S1 as backtested off a 12-day run with no trades, which
    # is precisely the false green this whole phase exists to prevent.
    def traded(r: dict) -> int:
        return int((r.get("metrics") or {}).get("trades") or 0)

    def span_days(r: dict) -> int:
        try:
            return (dt.date.fromisoformat(r["date_to"]) - dt.date.fromisoformat(r["date_from"])).days
        except (KeyError, TypeError, ValueError):
            return 0

    baselined: dict[str, int] = {}
    empty: dict[str, int] = {}
    for r in runs:
        code = by_id.get(r["strategy_id"])
        if code is None or code not in expected:
            continue
        n = traded(r)
        if n > 0 and span_days(r) >= MIN_BASELINE_SPAN_DAYS:
            baselined[code] = max(baselined.get(code, 0), n)
        else:
            empty[code] = max(empty.get(code, 0), span_days(r))

    missing = sorted(expected - set(baselined))
    zero_only = sorted(c for c in missing if c in empty)
    ab = [r for r in runs if r.get("ab_against_backtest_id")]
    calibrated = [r for r in runs if (r.get("calibration") or {}).get("factor") is not None]

    met = not missing and bool(ab)
    detail = (f"{len(baselined)} of {len(expected)} backtestable strategies have a baseline that "
              f"actually produced trades"
              + (f"; still missing: {', '.join(missing)}" if missing else "")
              + (f" ({', '.join(zero_only)} ran but produced ZERO trades over "
                 f"{'/'.join(str(empty[c]) for c in zero_only)} days — that is a configuration finding, "
                 f"not a baseline)" if zero_only else "")
              + ("; the S6-vs-S7 A/B is recorded" if ab else "; no A/B comparison recorded yet")
              + (f"; IEX↔SIP calibration factor present on {len(calibrated)} run(s)" if calibrated
                 else "; no run carries an IEX↔SIP calibration factor — none has been logged yet")
              + f". {', '.join(sorted(NOT_BACKTESTABLE))} is excused: a watchlist briefing has no entry, "
                "stop or target to replay (decision #60).")
    return Criterion(1, "All strategies backtested with the four countermeasures", met, detail,
                     {"baselined": {c: baselined[c] for c in sorted(baselined)}, "missing": missing,
                      "zero_trade_runs_only": zero_only, "ab_runs": len(ab),
                      "runs_with_calibration": len(calibrated), "excused": sorted(NOT_BACKTESTABLE),
                      "min_baseline_span_days": MIN_BASELINE_SPAN_DAYS})


def criterion_2_paper_month(now: dt.datetime | None = None) -> Criterion:
    from app.db import get_client
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    cfg = paper_month()
    if not cfg.get("started_on"):
        return Criterion(2, "≥ 4 consecutive weeks of full paper operation", False,
                         "the paper month has not been started — POST /ops/paper-month/start stamps day zero",
                         {"started_on": None})

    started = dt.date.fromisoformat(cfg["started_on"])
    blocks = _weeks(started, now.date())
    try:
        scans = (get_client().table("scans").select("run_at")
                 .gte("run_at", f"{started.isoformat()}T00:00:00").limit(50_000).execute().data or [])
        signals = (get_client().table("signals").select("created_at")
                   .gte("created_at", f"{started.isoformat()}T00:00:00").limit(10_000).execute().data or [])
        trades = (get_client().table("journal").select("id,exit_time,autopsy_at")
                  .gte("entry_time", f"{started.isoformat()}T00:00:00").limit(10_000).execute().data or [])
        errors = (get_client().table("compliance_events").select("id,event_time,reason,acknowledged")
                  .in_("kind", list(UNHANDLED_KINDS))
                  .gte("event_time", f"{started.isoformat()}T00:00:00").limit(5_000).execute().data or [])
    except Exception as e:  # noqa: BLE001
        return Criterion(2, "≥ 4 consecutive weeks of full paper operation", None,
                         f"could not read the paper-month tables: {type(e).__name__}: {str(e)[:160]}",
                         {"started_on": cfg["started_on"]})

    scan_days = {r["run_at"][:10] for r in scans}
    signal_days = {r["created_at"][:10] for r in signals}
    closed = [t for t in trades if t.get("exit_time")]
    autopsied = [t for t in closed if t.get("autopsy_at")]
    unacked = [e for e in errors if not e.get("acknowledged")]

    week_rows, consecutive = [], 0
    for i, (a, b) in enumerate(blocks, start=1):
        days = {(a + dt.timedelta(days=d)).isoformat() for d in range(7)}
        ok = len(scan_days & days) >= MIN_SCAN_DAYS_PER_WEEK
        week_rows.append({"week": i, "from": a.isoformat(), "to": b.isoformat(),
                          "scan_days": len(scan_days & days), "signal_days": len(signal_days & days),
                          "complete": ok})
        consecutive = consecutive + 1 if ok else 0

    enough_weeks = consecutive >= cfg["min_weeks"]
    enough_trades = len(closed) >= cfg["min_trades"]
    clean = not unacked
    met = enough_weeks and enough_trades and clean and bool(autopsied or not closed)

    detail = (f"{consecutive} consecutive complete week(s) of {cfg['min_weeks']} needed "
              f"({len(blocks)} elapsed since {started}); {len(closed)} closed paper trade(s) of "
              f"{cfg['min_trades']}; {len(autopsied)} autopsied; "
              + (f"{len(unacked)} unacknowledged execution failure(s) — criterion 2 requires zero"
                 if unacked else "zero unacknowledged execution failures"))
    return Criterion(2, "≥ 4 consecutive weeks of full paper operation", met, detail,
                     {"weeks": week_rows, "consecutive_complete_weeks": consecutive,
                      "closed_trades": len(closed), "autopsied": len(autopsied),
                      "scan_days": len(scan_days), "signal_days": len(signal_days),
                      "unacknowledged_failures": [{"at": e["event_time"], "reason": e["reason"][:200]}
                                                  for e in unacked[:10]]})


def criterion_3_guardian(now: dt.datetime | None = None) -> Criterion:
    from app.ops import drill

    parts, evidence = [], {}
    try:
        last = drill.latest()
    except Exception as e:  # noqa: BLE001
        return Criterion(3, "Guardian verified end to end", None,
                         f"could not read the drill history: {type(e).__name__}: {str(e)[:160]}")

    drill_ok = False
    if not last:
        parts.append("no Guardian drill has ever run (POST /ops/drill)")
    else:
        d = last.get("detail") or {}
        drill_ok = bool(d.get("complete"))
        evidence["drill"] = {"ran_at": last.get("event_time"), "complete": d.get("complete"),
                             "proven": d.get("checks_proven"), "total": d.get("checks_total"),
                             "missing": d.get("missing", [])}
        parts.append(f"drill {last.get('event_time', '')[:10]}: {d.get('checks_proven')}/{d.get('checks_total')} "
                     f"checks proven able to block"
                     + (f" — not proven: {', '.join(d.get('missing') or [])}" if not drill_ok else ""))

    rec = reconcile_ledger()
    evidence["ledger"] = rec
    parts.append(rec["detail"])

    tax_ok, tax_detail = _tax_provision()
    evidence["tax_provision"] = tax_detail
    parts.append(tax_detail["detail"])

    met = drill_ok and rec["reconciled"] is True and tax_ok
    return Criterion(3, "Guardian verified end to end", met if rec["reconciled"] is not None else None,
                     "; ".join(parts), evidence)


def reconcile_ledger(day: dt.date | None = None, *, days: int = 30) -> dict:
    """§17.3: "ledger reconciles against Alpaca activities to the cent".

    A paper account is never charged SEC/TAF (decision #46), so on paper the honest reconciliation is:
    every fee row the ledger holds is either backed by a real Alpaca activity id, or explicitly marked
    `estimated`. An unmarked, unbacked fee row is the failure — that would be an invented charge.
    """
    from app.db import get_client
    from app.marketdata import alpaca

    until = day or alpaca.now_et().date()
    since = until - dt.timedelta(days=days)
    try:
        rows = (get_client().table("guardian_ledger").select("kind,amount,alpaca_activity_id,detail,event_time")
                .gte("event_time", f"{since.isoformat()}T00:00:00").limit(10_000).execute().data or [])
    except Exception as e:  # noqa: BLE001
        return {"reconciled": None, "detail": f"ledger unreadable: {type(e).__name__}: {str(e)[:140]}"}

    fees = [r for r in rows if r["kind"].startswith("fee_")]
    backed = [r for r in fees if r.get("alpaca_activity_id")]
    estimated = [r for r in fees if (r.get("detail") or {}).get("estimated") is True]
    unexplained = [r for r in fees if not r.get("alpaca_activity_id")
                   and not (r.get("detail") or {}).get("estimated")]
    reconciled = not unexplained
    detail = (f"ledger {since}→{until}: {len(fees)} fee row(s) — {len(backed)} matched to an Alpaca "
              f"activity id, {len(estimated)} labelled estimated"
              + (f", {len(unexplained)} NEITHER — an unbacked, unlabelled fee is an invented charge"
                 if unexplained else ", none unaccounted for"))
    return {"reconciled": reconciled, "detail": detail, "fee_rows": len(fees), "backed": len(backed),
            "estimated": len(estimated), "unexplained": len(unexplained)}


def _tax_provision() -> tuple[bool, dict]:
    from app.db import get_client

    try:
        rows = (get_client().table("guardian_ledger").select("running_tax_provision,event_time")
                .not_.is_("running_tax_provision", "null")
                .order("event_time", desc=True).limit(1).execute().data or [])
    except Exception as e:  # noqa: BLE001
        return False, {"detail": f"tax provision unreadable: {str(e)[:120]}", "value": None}
    if not rows:
        return False, {"detail": "no tax provision recorded yet (no closed trades with realized gains)",
                       "value": None}
    v = float(rows[0]["running_tax_provision"])
    return True, {"detail": f"tax provision accumulating: {v:,.2f} USD (display estimate — confirm with an "
                            f"accountant)", "value": v, "as_of": rows[0]["event_time"]}


def criterion_4_latency(*, days: int = 30) -> Criterion:
    """Deploy→order ≤ 2s and synthetic stop reaction < 1s, from what the live paths recorded."""
    from app.db import get_client
    from app.marketdata import alpaca

    since = (alpaca.now_et().date() - dt.timedelta(days=days)).isoformat()
    try:
        orders = (get_client().table("orders").select("created_at,raw_response,source")
                  .gte("created_at", f"{since}T00:00:00").limit(2_000).execute().data or [])
        fires = (get_client().table("compliance_events").select("event_time,detail")
                 .eq("kind", "synthetic_stop").gte("event_time", f"{since}T00:00:00")
                 .limit(500).execute().data or [])
    except Exception as e:  # noqa: BLE001
        return Criterion(4, "Latency verified (deploy ≤ 2s, synthetic stop < 1s)", None,
                         f"could not read latency samples: {type(e).__name__}: {str(e)[:160]}")

    deploy_ms = [float((o.get("raw_response") or {}).get("deploy_latency_ms"))
                 for o in orders if (o.get("raw_response") or {}).get("deploy_latency_ms") is not None]
    stop_ms = [float((f.get("detail") or {}).get("reaction_ms"))
               for f in fires if (f.get("detail") or {}).get("reaction_ms") is not None]

    def worst(xs):
        return max(xs) if xs else None

    d_worst, s_worst = worst(deploy_ms), worst(stop_ms)
    if not deploy_ms:
        return Criterion(4, "Latency verified (deploy ≤ 2s, synthetic stop < 1s)", None,
                         f"no deploy latency samples in the last {days} days — the figure is measured on "
                         f"real taps, so it stays unknown until the paper month produces some",
                         {"deploy_samples": 0, "stop_samples": len(stop_ms)})

    deploy_ok = d_worst <= MAX_DEPLOY_LATENCY_MS
    stop_ok = None if s_worst is None else s_worst < MAX_STOP_REACTION_MS
    met = deploy_ok and stop_ok is True
    detail = (f"deploy→order: {len(deploy_ms)} sample(s), worst {d_worst:.0f}ms "
              f"(limit {MAX_DEPLOY_LATENCY_MS}ms) — {'ok' if deploy_ok else 'OVER'}; "
              + (f"synthetic stop: {len(stop_ms)} sample(s), worst {s_worst:.0f}ms "
                 f"(limit {MAX_STOP_REACTION_MS}ms) — {'ok' if stop_ok else 'OVER'}"
                 if s_worst is not None else
                 "synthetic stop: never fired in this window, so its reaction time is unverified — it "
                 "cannot be assumed from the fact that nothing went wrong"))
    return Criterion(4, "Latency verified (deploy ≤ 2s, synthetic stop < 1s)",
                     met if stop_ok is not None else None, detail,
                     {"deploy_samples": len(deploy_ms), "deploy_worst_ms": d_worst,
                      "deploy_median_ms": sorted(deploy_ms)[len(deploy_ms) // 2],
                      "stop_samples": len(stop_ms), "stop_worst_ms": s_worst})


def criterion_5_owner() -> Criterion:
    """Funding, the W-8BEN, and choosing the first small live config. Never measured — never assumed."""
    from app.marketdata import repo as md_repo

    cfg = md_repo.fetch_setting("go_live_checklist", {}) or {}
    items = {"funded": "account funded modestly", "w8ben": "W-8BEN filed with Alpaca",
             "first_strategy": "first live strategy chosen (single config, small size)",
             "accountant": "tax treatment confirmed with an accountant"}
    done = [k for k in items if cfg.get(k) is True]
    return Criterion(5, "Owner's own steps before real money", len(done) == len(items),
                     ("all confirmed" if len(done) == len(items)
                      else "outstanding: " + ", ".join(items[k] for k in items if k not in done))
                     + ". Nothing in the engine can tick these off.",
                     {"confirmed": done, "outstanding": [k for k in items if k not in done]}, manual=True)


def scorecard(now: dt.datetime | None = None) -> Scorecard:
    from app.marketdata import alpaca

    now = now or alpaca.now_et()
    return Scorecard(criteria=[criterion_1_backtests(), criterion_2_paper_month(now),
                               criterion_3_guardian(now), criterion_4_latency(), criterion_5_owner()],
                     computed_at=now.isoformat(), window=window(now))


def written_summary(card: Scorecard) -> str:
    """The plain-language version for Telegram and the weekly review."""
    lines = [f"📋 Go-live scorecard ({card.computed_at[:10]})"]
    for c in card.criteria:
        icon = {"met": "✅", "not met": "⬜", "unknown": "❓", "owner": "🙋"}[c.status]
        lines.append(f"{icon} {c.number}. {c.title}\n    {c.detail}")
    lines.append("Live trading stays refused in code until a separate, owner-approved commit lifts it.")
    return "\n".join(lines)
