"""The Phase 4 orchestrator: scan rows → in-play gate → trade plans + watchlist briefing.

Order matters and is deliberate: the cheap gate (template 04) runs first so a candidate with no real
catalyst never costs a plan call, then template 02 writes plans for the survivors, then template 03
writes the S2 briefing. Every step reports what it did and what it refused to do.

Nothing here places an order. `signals` rows are proposals; the owner approves each one by hand in
Phase 5, behind the Guardian gate and the kill switch.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from app.broker import account as broker_account
from app.db import get_client
from app.llm import LLMError, openrouter
from app.marketdata import alpaca
from app.marketdata import repo as md_repo
from app.models import WatchlistUniverse
from app.notify import send_telegram
from app.ops import preflight as ops_preflight
from app.scanner import run as scan_run
from app.signals import briefing as briefing_mod
from app.signals import context as ctx
from app.signals import gate as gate_mod
from app.signals import plan as plan_mod

log = logging.getLogger(__name__)

DEFAULT_CANDIDATES_PER_STRATEGY = 5
BRIEFING_STRATEGY = "S2"


def config() -> dict:
    cfg = md_repo.fetch_setting("signals", {}) or {}
    return {
        "candidates_per_strategy": int(cfg.get("candidates_per_strategy", DEFAULT_CANDIDATES_PER_STRATEGY)),
        "run_in_play_gate": bool(cfg.get("run_in_play_gate", True)),
        "briefing_strategy": cfg.get("briefing_strategy", BRIEFING_STRATEGY),
        "level_tolerance_pct": float(cfg.get("level_tolerance_pct", plan_mod.LEVEL_TOLERANCE_PCT)),
    }


def load_strategy_rows(codes: list[str] | None = None) -> dict[str, dict]:
    rows = get_client().table("strategy_configs").select("*").execute().data or []
    return {r["code"]: r for r in rows if not codes or r["code"] in codes}


def passed_scans(day: dt.date, cap: int) -> dict[str, list[dict]]:
    """Today's passed morning rows, top `cap` per strategy_id, ranked."""
    rows = (get_client().table("scans").select("*")
            .eq("scan_date", day.isoformat()).eq("run_kind", "morning").eq("passed", True)
            .order("rank", desc=False).execute().data or [])
    out: dict[str, list[dict]] = {}
    for r in rows:
        bucket = out.setdefault(r["strategy_id"], [])
        if len(bucket) < cap:
            bucket.append(r)
    return out


def trades_logged(strategy_id: str) -> int:
    try:
        rows = (get_client().table("journal").select("id").eq("strategy_id", strategy_id)
                .limit(200).execute().data or [])
        return len(rows)
    except Exception:  # noqa: BLE001
        return 0


@dataclass(slots=True)
class SignalsResult:
    trade_date: str
    as_of: str
    strategies: list[dict] = field(default_factory=list)
    signals_written: int = 0
    briefing_written: int = 0
    gate_rejected: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    market_context: str | None = None
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {"trade_date": self.trade_date, "as_of": self.as_of, "strategies": self.strategies,
                "signals_written": self.signals_written, "briefing_written": self.briefing_written,
                "gate_rejected": self.gate_rejected, "market_context": self.market_context,
                "errors": self.errors, "notes": self.notes, "elapsed_s": self.elapsed_s}

    def summary(self) -> str:
        lines = [f"🧠 Plans {self.trade_date} @ {self.as_of} ET"]
        if self.market_context:
            lines.append(self.market_context[:300])
        for s in self.strategies:
            if s.get("kind") == "briefing":
                lines.append(f"{s['code']} briefing: {s.get('rows', 0)} names")
                lines += ["   " + x for x in s.get("top", [])[:5]]
                continue
            lines.append(f"{s['code']}: {s.get('proposed', 0)} proposed / {s.get('evaluated', 0)} evaluated"
                         + (f", {s['gate_rejected']} not in play" if s.get("gate_rejected") else ""))
            for p in s.get("plans", [])[:5]:
                if p.get("status") == "proposed":
                    lines.append(f"   ✅ {p['symbol']} {p.get('direction')} entry {p.get('entry')} "
                                 f"stop {p.get('stop')} size {p.get('size')} conf {p.get('confidence')}")
                else:
                    lines.append(f"   ⛔ {p['symbol']} {p.get('decision') or 'rejected'}: "
                                 f"{(p.get('reject_reason') or '')[:80]}")
        for n in self.notes:
            lines.append(f"ℹ️ {n}")
        if self.errors:
            lines.append(f"⚠️ {len(self.errors)} error(s): {self.errors[0][:140]}")
        lines.append("Proposals only — nothing is executed. Approval is Phase 5.")
        return "\n".join(lines)


def _template_id(slug: str) -> str | None:
    try:
        rows = get_client().table("prompt_templates").select("id").eq("slug", slug).execute().data or []
        return rows[0]["id"] if rows else None
    except Exception:  # noqa: BLE001
        return None


def run_signals(*, now: dt.datetime | None = None, date: dt.date | None = None,
                strategy_codes: list[str] | None = None, cap: int | None = None,
                skip_gate: bool = False, briefing: bool = True, notify: bool = True,
                dry_run: bool = False) -> SignalsResult:
    t0 = time.time()
    now = now or alpaca.now_et()
    day = date or now.date()
    cfg = config()
    if cap is not None:
        cfg["candidates_per_strategy"] = cap
    res = SignalsResult(trade_date=day.isoformat(), as_of=now.strftime("%H:%M"))

    # Plan §33, checked before a single token is spent: a plan written on a stale price is a plan for a
    # market that has moved on, and it would look exactly like a good one. The reference is SPY on IEX —
    # a symbol that always trades, on the only feed the free plan serves in real time.
    try:
        ops_preflight.assert_data_fresh(now=now)
    except ops_preflight.PreflightBlocked as e:
        res.errors.append(f"data feed: {e}")
        res.notes.append("no plans were generated — §33 refuses to plan on stale prices")
        res.elapsed_s = round(time.time() - t0, 1)
        if notify:
            send_telegram(f"🧠 Plans {day.isoformat()}: ABORTED — the price feed is stale ({e}). "
                          f"No plans were written; none were guessed.")
        return res

    ready, detail = openrouter.available()
    if not ready:
        res.errors.append(f"openrouter: {detail}")
        res.elapsed_s = round(time.time() - t0, 1)
        if notify:
            send_telegram(f"🧠 Plans {day.isoformat()}: ABORTED — {detail}")
        return res

    kill = ctx.kill_switch_engaged()
    if kill:
        res.notes.append("kill switch is ENGAGED — plans are still written, but nothing could be executed")
    acct = broker_account.snapshot()
    if not acct.ok:
        res.notes.append(f"broker unreachable ({acct.error}) — sizing and position checks are unknown")
    account_block = ctx.account_block(acct)
    guardian_defaults = md_repo.fetch_setting("guardian_defaults", {}) or {}
    events = ctx.economic_events(day)
    lessons = ctx.journal_lessons()
    if not lessons:
        res.notes.append("no journal lessons yet — they start feeding the prompt in Phase 6")

    strategy_rows = load_strategy_rows(strategy_codes)
    by_strategy = passed_scans(day, cfg["candidates_per_strategy"])
    if not by_strategy:
        res.notes.append("no passed candidates in today's morning scan — nothing to plan")
        res.elapsed_s = round(time.time() - t0, 1)
        if notify:
            send_telegram(res.summary())
        return res

    id_to_code = {r["id"]: r["code"] for r in strategy_rows.values()}
    phase = "premarket" if now.time() < dt.time(9, 30) else "regular"
    plan_template_id = _template_id(plan_mod.TEMPLATE_SLUG)
    signal_rows: list[dict] = []

    for strategy_id, scan_rows in by_strategy.items():
        code = id_to_code.get(strategy_id)
        if not code or code not in strategy_rows:
            continue
        cfg_row = strategy_rows[code]
        risk = cfg_row.get("risk") or {}
        is_briefing = code == cfg["briefing_strategy"]

        try:
            candidates = ctx.build_candidates(scan_rows, day=day, risk=risk, acct=acct, kill_switch=kill)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"{code} context: {type(e).__name__}: {str(e)[:160]}")
            continue

        if is_briefing:
            if not briefing:
                continue
            try:
                payload, meta, inputs = briefing_mod.run_briefing(
                    day=day, as_of_et=now.strftime("%H:%M"), candidates=candidates, events=events)
            except LLMError as e:
                res.errors.append(f"{code} briefing: {str(e)[:200]}")
                continue
            if not dry_run:
                try:
                    res.briefing_written += briefing_mod.write_briefing(
                        day=day, as_of_et=now.strftime("%H:%M"), payload=payload, meta=meta, inputs=inputs,
                        strategy_id=strategy_id, template_id=_template_id(briefing_mod.TEMPLATE_SLUG))
                except Exception as e:  # noqa: BLE001
                    res.errors.append(f"{code} briefing write: {type(e).__name__}: {str(e)[:160]}")
            res.market_context = res.market_context or payload.get("market_context")
            res.strategies.append({"code": code, "kind": "briefing", "rows": len(payload.get("rows") or []),
                                   "top": briefing_mod.summary_lines(payload)})
            continue

        # --- screener strategies: gate, then plan ---
        gate_rejected = 0
        survivors = candidates
        gate_meta: dict = {}
        if cfg["run_in_play_gate"] and not skip_gate:
            verdicts, gate_meta, gate_err = gate_mod.safe_run_gate(candidates, day)
            if gate_err:
                res.errors.append(f"{code} {gate_err}")
                res.notes.append(f"{code}: in-play gate failed, so no plans were written for it "
                                 f"(a gate failure is never treated as a pass)")
                res.strategies.append({"code": code, "evaluated": len(candidates), "proposed": 0,
                                       "gate_rejected": 0, "plans": [], "error": gate_err})
                continue
            if not dry_run:
                try:
                    gate_mod.write_verdicts(day, verdicts, strategy_id)
                except Exception as e:  # noqa: BLE001
                    res.errors.append(f"{code} gate write: {type(e).__name__}: {str(e)[:160]}")
            survivors = gate_mod.survivors(candidates, verdicts)
            gate_rejected = len(candidates) - len(survivors)
            res.gate_rejected += gate_rejected

        if not survivors:
            res.strategies.append({"code": code, "evaluated": len(candidates), "proposed": 0,
                                   "gate_rejected": gate_rejected, "plans": [],
                                   "note": "every candidate failed the in-play test"})
            continue

        try:
            payload, meta = plan_mod.run_plan(day=day, phase=phase, cfg_row=cfg_row, candidates=survivors,
                                              account=account_block, guardian_defaults=guardian_defaults,
                                              events=events, lessons=lessons)
        except LLMError as e:
            res.errors.append(f"{code} plan: {str(e)[:200]}")
            res.strategies.append({"code": code, "evaluated": len(candidates), "proposed": 0,
                                   "gate_rejected": gate_rejected, "plans": [], "error": str(e)[:200]})
            continue

        res.market_context = res.market_context or payload.get("market_context")
        by_symbol = {c["symbol"]: c for c in survivors}
        scan_by_symbol = {r["symbol"]: r for r in scan_rows}
        logged = trades_logged(strategy_id)
        plans_out = []
        for p in payload.get("plans", []):
            sym = (p.get("symbol") or "").upper()
            cand = by_symbol.get(sym)
            if not cand:
                res.errors.append(f"{code}: plan returned for {sym!r}, which was not a candidate — discarded")
                continue
            problems = plan_mod.validate_plan(p, cand, risk=risk, trades_logged=logged,
                                              tolerance_pct=cfg["level_tolerance_pct"])
            row = plan_mod.signal_row(plan=p, candidate=cand, strategy_id=strategy_id, day=day,
                                      scan_id=(scan_by_symbol.get(sym) or {}).get("id"),
                                      template_id=plan_template_id, problems=problems, meta=meta,
                                      raw=payload)
            signal_rows.append(row)
            plans_out.append({"symbol": sym, "status": row["status"], "decision": p.get("decision"),
                              "direction": p.get("direction"), "entry": p.get("entry"), "stop": p.get("stop"),
                              "target": p.get("target"), "size": p.get("size"),
                              "confidence": p.get("confidence"), "reject_reason": row["reject_reason"]})

        res.strategies.append({
            "code": code, "evaluated": len(candidates), "gate_rejected": gate_rejected,
            "planned": len(plans_out),
            "proposed": sum(1 for p in plans_out if p["status"] == "proposed"),
            "rejected": sum(1 for p in plans_out if p["status"] == "rejected"),
            "plans": plans_out, "gate_model": gate_meta.get("model"), "plan_model": meta.get("model"),
        })

    if dry_run:
        res.signals_written = len(signal_rows)
        res.notes.append("dry_run: nothing written to signals or briefings")
    elif signal_rows:
        try:
            for i in range(0, len(signal_rows), 200):
                get_client().table("signals").upsert(
                    signal_rows[i:i + 200], on_conflict="strategy_id,signal_date,symbol").execute()
            res.signals_written = len(signal_rows)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"signals write: {type(e).__name__}: {str(e)[:200]}")

    res.elapsed_s = round(time.time() - t0, 1)
    if notify:
        send_telegram(res.summary())
    return res


def scannable_watchlist_symbols(code: str) -> list[str]:
    """S2's configured watchlist, for diagnostics when no scan row exists."""
    for _sid, cfg in scan_run.load_strategies(("draft", "backtesting", "paper", "live")):
        if cfg.code == code and isinstance(cfg.universe, WatchlistUniverse):
            return list(cfg.universe.symbols)
    return []
