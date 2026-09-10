"""The enrichment job (plan Phase 3, §10 template 1, §33).

Morning flow, chained after the 08:00 scan in n8n workflow 02:

  1. pick candidates — rank <= N per strategy from today's passed `scans` rows, deduped
  2. per symbol x enabled source: fetch raw items -> local Hermes 3 8B condenser -> strict JSON
  3. upsert `news_sentiment` (unique on symbol+trade_date+source)
  4. merge the sources into one `catalyst` block and write it back onto today's `scans` rows
  5. Telegram summary

Decision 21: the write-back NEVER changes `scans.passed`, and never touches `pending_gates`. It fills
`metrics.catalyst` and adjusts the score/rank, nothing else.

Decision 22: there is no `news_catalyst` hard gate to clear. Verified against the seeded strategies:
S1 treats the catalyst as a SOFT input ("news catalyst preferred (soft score from news_sentiment)",
plan §7) and S3/F-A feeds it into the Hermes one-sentence in-play test (template 04, hosted tier).
`pending_gates` holds unevaluable HARD conditions plus that Hermes slug — all of them Phase 4's or
Phase 7's to satisfy. So enrichment adds information and leaves every gate exactly as the scan set it;
the catalyst's scan-time job is the soft score the plan asks for.

Decision 23: the score bonus uses catalyst MAGNITUDE, not direction. "In play" (framework F-A, the SMB
Capital stocks-in-play triple filter, plan §8) means a fresh catalyst exists; whether that argues long or
short is judgment, and judgment belongs to Hermes in Phase 4, not to a scalar here.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field

from app.db import get_client
from app.llm import LLMError, complete_json
from app.llm import ollama as ollama_client
from app.llm.prompts import load as load_prompt
from app.marketdata import alpaca
from app.marketdata import repo as md_repo
from app.notify import send_telegram
from app.sentiment import sources as news_sources

log = logging.getLogger(__name__)

TEMPLATE_SLUG = "sentiment_condenser"
DEFAULT_SOURCES = ("finnhub", "yahoo")
DEFAULT_CAP = 5
DEFAULT_LOOKBACK_DAYS = 2
CATALYST_WEIGHT = 2.0          # score bonus per 1.0 of |sentiment_score| on a fresh catalyst


def config() -> dict:
    """`app_settings.sentiment`, with the code defaults as the fallback (Phase 3 seed adds the row)."""
    cfg = md_repo.fetch_setting("sentiment", {}) or {}
    return {
        "sources": tuple(cfg.get("sources") or DEFAULT_SOURCES),
        "candidates_per_strategy": int(cfg.get("candidates_per_strategy", DEFAULT_CAP)),
        "lookback_days": int(cfg.get("lookback_days", DEFAULT_LOOKBACK_DAYS)),
        "reddit_subs": tuple(cfg.get("reddit_subs") or news_sources.DEFAULT_SUBS),
        "max_items_per_source": int(cfg.get("max_items_per_source", news_sources.MAX_ITEMS)),
        "catalyst_weight": float(cfg.get("catalyst_weight", CATALYST_WEIGHT)),
    }


# ---------- candidate selection ----------

def candidates_from_scans(day: dt.date, cap: int, strategy_codes: list[str] | None = None) -> tuple[list[str], dict]:
    """Top `cap` passed symbols per strategy from today's morning scan, deduped across strategies."""
    client = get_client()
    strategies = {r["id"]: r["code"] for r in
                  (client.table("strategy_configs").select("id,code").execute().data or [])}
    q = (client.table("scans").select("strategy_id,symbol,rank,metrics")
         .eq("scan_date", day.isoformat()).eq("run_kind", "morning").eq("passed", True)
         .order("rank", desc=False))
    rows = q.execute().data or []
    per_strategy: dict[str, list[str]] = {}
    for r in rows:
        code = strategies.get(r["strategy_id"], r["strategy_id"])
        if strategy_codes and code not in strategy_codes:
            continue
        bucket = per_strategy.setdefault(code, [])
        if len(bucket) < cap and r["symbol"] not in bucket:
            bucket.append(r["symbol"])
    union = sorted({s for v in per_strategy.values() for s in v})
    return union, per_strategy


# ---------- condensing ----------

@dataclass(slots=True)
class SourceOutcome:
    source: str
    status: str                      # ok | skipped | failed
    payload: dict | None = None
    items: int = 0
    error: str | None = None
    attempts: int = 0

    def as_dict(self) -> dict:
        d = {"source": self.source, "status": self.status, "items": self.items}
        if self.payload:
            d |= {"catalyst_type": self.payload.get("catalyst_type"),
                  "sentiment_score": self.payload.get("sentiment_score"),
                  "freshness": self.payload.get("freshness")}
        if self.error:
            d["error"] = self.error
        return d


def condense_symbol_source(symbol: str, source: str, day: dt.date,
                           cfg: dict) -> tuple[SourceOutcome, list[news_sources.NewsItem]]:
    """Fetch one source for one symbol and condense it. Never raises — the outcome carries the truth.

    Returns the outcome plus the raw items that fed it, so the caller can persist them in `raw_items`.
    """
    res = news_sources.fetch(source, symbol, day, lookback_days=cfg["lookback_days"],
                             subs=cfg["reddit_subs"])
    if res.status == "failed":
        return SourceOutcome(source, "failed", error=res.error), []
    if res.status == "skipped":
        return SourceOutcome(source, "skipped", error=res.error), []
    if not res.items:
        # A source that genuinely has nothing is not a failure, but it is not a catalyst either.
        return SourceOutcome(source, "skipped", items=0, error="no items returned for this symbol"), []

    items = res.items[: cfg["max_items_per_source"]]
    spec = load_prompt(TEMPLATE_SLUG)
    try:
        payload, meta = complete_json(spec, {
            "symbol": symbol, "source": source, "trade_date": day.isoformat(),
            "raw_items": news_sources.format_items(items),
        })
    except LLMError as e:
        return SourceOutcome(source, "failed", items=len(items), error=str(e)[:300]), items
    return SourceOutcome(source, "ok", payload=payload, items=len(items), attempts=meta["attempts"]), items


def news_row(symbol: str, day: dt.date, outcome: SourceOutcome, raw: list[news_sources.NewsItem],
             model: str) -> dict:
    """One `news_sentiment` row. `freshness` has no column, so it travels inside raw_items."""
    p = outcome.payload or {}
    return {
        "symbol": symbol, "trade_date": day.isoformat(), "source": outcome.source,
        "catalyst_type": p.get("catalyst_type"), "sentiment_score": p.get("sentiment_score"),
        "key_facts": p.get("key_facts") or [], "one_sentence_reason": p.get("one_sentence_reason"),
        "raw_items": {"freshness": p.get("freshness"), "item_count": len(raw),
                      "items": [i.as_dict() for i in raw[:10]]},
        "model": model,
    }


# ---------- merging sources into one catalyst block ----------

def merge_catalyst(outcomes: list[SourceOutcome]) -> dict | None:
    """One catalyst per symbol from N sources.

    Type: the most common non-'none' type, ties broken by the strongest |score|.
    Score: mean of the contributing sources. Reason: from the strongest-scoring source.
    """
    usable = [o for o in outcomes if o.status == "ok" and o.payload]
    if not usable:
        return None
    scored = [(o, o.payload.get("catalyst_type") or "none", float(o.payload.get("sentiment_score") or 0.0))
              for o in usable]
    real = [(o, t, s) for o, t, s in scored if t != "none"]
    pool = real or scored

    counts: dict[str, int] = {}
    strongest: dict[str, float] = {}
    for _, t, s in pool:
        counts[t] = counts.get(t, 0) + 1
        strongest[t] = max(strongest.get(t, 0.0), abs(s))
    best_type = min(counts, key=lambda t: (-counts[t], -strongest[t]))

    contributing = [(o, t, s) for o, t, s in pool if t == best_type]
    mean_score = round(sum(s for _, _, s in contributing) / len(contributing), 3)
    lead = max(contributing, key=lambda x: abs(x[2]))[0]
    freshness = [o.payload.get("freshness") for o, _, _ in contributing]

    return {
        "catalyst_type": best_type,
        "sentiment_score": mean_score,
        "one_sentence_reason": lead.payload.get("one_sentence_reason"),
        "freshness": "fresh" if "fresh" in freshness else ("stale" if "stale" in freshness else "unknown"),
        "sources": [o.source for o, _, _ in contributing],
        "sources_attempted": [o.as_dict() for o in outcomes],
        "key_facts": (lead.payload.get("key_facts") or [])[:5],
    }


def catalyst_bonus(block: dict | None, weight: float = CATALYST_WEIGHT) -> float:
    """Magnitude, not direction (decision 23). Only a FRESH, identified catalyst scores."""
    if not block or block.get("catalyst_type") in (None, "none"):
        return 0.0
    if block.get("freshness") != "fresh":
        return 0.0
    return round(weight * abs(float(block.get("sentiment_score") or 0.0)), 3)


# ---------- write-back onto scans ----------

def write_back(day: dt.date, blocks: dict[str, dict],
               weight: float = CATALYST_WEIGHT) -> tuple[int, int]:
    """Attach the catalyst to today's morning `scans` rows and re-rank.

    `passed` and `pending_gates` are never touched (decisions 21, 22). Re-running is idempotent: the
    previous `catalyst_bonus` is backed out of the score before the new one is applied.

    Returns (rows updated, strategies re-ranked).
    """
    if not blocks:
        return 0, 0
    client = get_client()
    rows = (client.table("scans").select("*")
            .eq("scan_date", day.isoformat()).eq("run_kind", "morning")
            .in_("symbol", sorted(blocks)).execute().data or [])
    if not rows:
        return 0, 0

    touched: list[dict] = []
    for r in rows:
        block = blocks.get(r["symbol"])
        if not block:
            continue
        metrics = dict(r.get("metrics") or {})
        metrics["catalyst"] = block
        # pending_gates is deliberately NOT modified (decision 22): nothing in it is ours to satisfy.
        bonus = catalyst_bonus(block, weight)
        base = float(metrics.get("score") or 0.0) - float(metrics.get("catalyst_bonus") or 0.0)
        metrics["catalyst_bonus"] = bonus
        metrics["score"] = round(base + bonus, 3)
        touched.append({"strategy_id": r["strategy_id"], "scan_date": r["scan_date"], "run_kind": r["run_kind"],
                        "symbol": r["symbol"], "run_at": r["run_at"], "passed": r["passed"],
                        "reject_reason": r.get("reject_reason"), "rank": r.get("rank"), "metrics": metrics})

    # re-rank the passed set per strategy on the adjusted score; rejected rows keep rank NULL
    by_strategy: dict[str, list[dict]] = {}
    for t in touched:
        if t["passed"]:
            by_strategy.setdefault(t["strategy_id"], []).append(t)
    reranked = 0
    for sid, group in by_strategy.items():
        # rows for this strategy that we did NOT touch still hold ranks; fetch them to rank the whole set
        others = [r for r in rows if r["strategy_id"] == sid and r["passed"]
                  and r["symbol"] not in {t["symbol"] for t in group}]
        full = group + [{"symbol": o["symbol"], "metrics": o.get("metrics") or {}, "_readonly": True} for o in others]
        full.sort(key=lambda x: -float((x.get("metrics") or {}).get("score") or 0.0))
        for i, row in enumerate(full, start=1):
            if not row.get("_readonly"):
                row["rank"] = i
        reranked += 1

    for i in range(0, len(touched), 500):
        client.table("scans").upsert(touched[i:i + 500],
                                     on_conflict="strategy_id,scan_date,symbol,run_kind").execute()
    return len(touched), reranked


# ---------- the run ----------

@dataclass(slots=True)
class EnrichResult:
    trade_date: str
    as_of: str
    sources: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    per_strategy: dict = field(default_factory=dict)
    detail: list[dict] = field(default_factory=list)
    news_rows_written: int = 0
    scan_rows_updated: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def as_dict(self) -> dict:
        return {"trade_date": self.trade_date, "as_of": self.as_of, "sources": self.sources,
                "symbols": self.symbols, "per_strategy": self.per_strategy, "detail": self.detail,
                "news_rows_written": self.news_rows_written, "scan_rows_updated": self.scan_rows_updated,
                "errors": self.errors, "notes": self.notes, "elapsed_s": self.elapsed_s}

    def summary(self) -> str:
        lines = [f"📰 Catalysts {self.trade_date} @ {self.as_of} ET — {len(self.symbols)} symbol(s)"]
        for d in self.detail:
            c = d.get("catalyst")
            if not c:
                lines.append(f"{d['symbol']}: no catalyst found")
                continue
            score = c.get("sentiment_score")
            sign = f"{score:+.2f}" if isinstance(score, (int, float)) else "?"
            reason = (c.get("one_sentence_reason") or "").strip()
            lines.append(f"{d['symbol']}: {c.get('catalyst_type')} {sign} ({c.get('freshness')}) — {reason[:110]}")
        for n in self.notes:
            lines.append(f"ℹ️ {n}")
        if self.errors:
            lines.append(f"⚠️ {len(self.errors)} error(s): {self.errors[0][:120]}")
        return "\n".join(lines)


def run_enrich(*, now: dt.datetime | None = None, date: dt.date | None = None, symbols: list[str] | None = None,
               strategy_codes: list[str] | None = None, source_names: list[str] | None = None,
               cap: int | None = None, notify: bool = True, dry_run: bool = False) -> EnrichResult:
    t0 = time.time()
    now = now or alpaca.now_et()
    day = date or now.date()
    cfg = config()
    if source_names:
        cfg["sources"] = tuple(source_names)
    if cap is not None:
        cfg["candidates_per_strategy"] = cap

    res = EnrichResult(trade_date=day.isoformat(), as_of=now.strftime("%H:%M"), sources=list(cfg["sources"]))

    ready, detail = ollama_client.available()
    if not ready:
        res.errors.append(f"ollama: {detail}")
        res.elapsed_s = round(time.time() - t0, 1)
        if notify:
            send_telegram(f"📰 Catalysts {day.isoformat()}: ABORTED — {detail}")
        return res

    if symbols:
        res.symbols = sorted({s.strip().upper() for s in symbols if s.strip()})
        res.per_strategy = {"(explicit)": res.symbols}
    else:
        try:
            res.symbols, res.per_strategy = candidates_from_scans(day, cfg["candidates_per_strategy"],
                                                                  strategy_codes)
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"candidates: {type(e).__name__}: {str(e)[:160]}")
            res.elapsed_s = round(time.time() - t0, 1)
            return res
    if not res.symbols:
        res.notes.append("no passed candidates in today's morning scan — nothing to enrich")
        res.elapsed_s = round(time.time() - t0, 1)
        if notify:
            send_telegram(res.summary())
        return res

    model = load_prompt(TEMPLATE_SLUG).model
    blocks: dict[str, dict] = {}
    news_rows: list[dict] = []

    for symbol in res.symbols:
        outcomes: list[SourceOutcome] = []
        for source in cfg["sources"]:
            outcome, items = condense_symbol_source(symbol, source, day, cfg)
            if outcome.status == "ok":
                news_rows.append(news_row(symbol, day, outcome, items, model))
            elif outcome.status == "failed":
                res.errors.append(f"{symbol}/{source}: {(outcome.error or '')[:160]}")
            outcomes.append(outcome)

        block = merge_catalyst(outcomes)
        if block:
            block["model"] = model
            blocks[symbol] = block
        res.detail.append({"symbol": symbol, "catalyst": block,
                           "sources": [o.as_dict() for o in outcomes]})

    if dry_run:
        res.news_rows_written = len(news_rows)
        res.notes.append("dry_run: nothing written to news_sentiment or scans")
    else:
        if news_rows:
            try:
                for i in range(0, len(news_rows), 200):
                    get_client().table("news_sentiment").upsert(
                        news_rows[i:i + 200], on_conflict="symbol,trade_date,source").execute()
                res.news_rows_written = len(news_rows)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"news_sentiment write: {type(e).__name__}: {str(e)[:160]}")
        try:
            updated, _ = write_back(day, blocks, cfg["catalyst_weight"])
            res.scan_rows_updated = updated
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"scans write-back: {type(e).__name__}: {str(e)[:160]}")

    skipped_sources = sorted({o["source"] for d in res.detail for o in d["sources"] if o["status"] == "skipped"
                              and "credentials" in (o.get("error") or "")})
    for s in skipped_sources:
        res.notes.append(f"{s} skipped: no credentials configured")

    res.elapsed_s = round(time.time() - t0, 1)
    if notify:
        send_telegram(res.summary())
    return res
