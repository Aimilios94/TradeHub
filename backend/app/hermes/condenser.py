"""Template 1, the sentiment condenser: one news item in, one checked JSON object out.

For each shown headline not yet condensed with the current prompt:
    call the cheap tier -> parse JSON -> Pydantic -> menu checks -> number check
An answer that fails any step is logged and the call is made once more with the reason; a second
failure is logged and the item gets no row. Only a fully valid answer is stored. Every call,
valid or not, is a row in llm_traces.

Hermes concludes here (catalyst, sentiment, which tickers, why). It computes nothing: the only
number it returns is its own sentiment score, and any figure in its text must be in the item.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template

from pydantic import ValidationError
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from app.hermes.client import LlmClient, LlmError
from app.hermes.number_check import check_numbers
from app.hermes.schemas import CondenserOutput
from app.hermes.trace import calls_today, cost_this_month_usd, write_trace

TEMPLATE = "condenser"
PROMPT_VERSION = "condenser-v1"
PROMPT_FILE = Path(__file__).with_name("prompts") / "condenser_v1.md"
MAX_ATTEMPTS = 2  # the call, and one retry
MAX_TRANSPORT_ERRORS = 3  # in a row; after that the model is treated as down and the run stops
CONTENT_FAILURES = ("invalid_json", "schema_failed", "number_check_failed")

_PENDING = text(
    """
    SELECT n.id, n.title, n.excerpt, n.published_at,
           (SELECT array_agg(DISTINCT ticker ORDER BY ticker)
            FROM news_items m, unnest(m.source_tickers) AS ticker
            WHERE m.id = n.id OR m.duplicate_of = n.id) AS tickers,
           (SELECT json_agg(json_build_object('driver', e.driver_id, 'direction', e.direction)
                            ORDER BY e.driver_id)
            FROM tagged_events e WHERE e.news_item_id = n.id) AS tags
    FROM news_items n
    WHERE n.duplicate_of IS NULL AND n.processed_at IS NOT NULL AND NOT n.is_demo
      AND n.ingested_at >= :since
      AND NOT EXISTS (
          SELECT 1 FROM llm_traces t
          WHERE t.news_item_id = n.id AND t.template = :template
            AND t.prompt_version = :prompt_version AND t.outcome = 'ok')
      AND (SELECT count(*) FROM llm_traces t
           WHERE t.news_item_id = n.id AND t.template = :template
             AND t.prompt_version = :prompt_version
             AND t.outcome IN ('invalid_json', 'schema_failed', 'number_check_failed')
          ) < :max_attempts
    ORDER BY n.importance_score DESC NULLS LAST, n.id
    """
)
_CANDIDATES = text(
    """
    SELECT t.id, t.symbol, t.display_name, (w.ticker_id IS NOT NULL) AS on_watchlist
    FROM tickers t LEFT JOIN watchlist w ON w.ticker_id = t.id
    WHERE t.is_active AND t.asset_class IN ('EQ', 'ETF')
    ORDER BY t.symbol
    """
)
_TAXONOMY = text("SELECT id, name, plus_one_meaning, minus_one_meaning FROM driver_taxonomy")
_INSERT_SENTIMENT = text(
    """
    INSERT INTO news_sentiment
        (news_item_id, ticker_id, catalyst_type, sentiment_score, driver_direction, key_facts,
         one_sentence_reason, prompt_version, model, llm_trace_id)
    VALUES (:news_item_id, :ticker_id, :catalyst_type, :sentiment_score, :driver_direction,
            :key_facts, :one_sentence_reason, :prompt_version, :model, :llm_trace_id)
    ON CONFLICT (news_item_id, ticker_id, prompt_version) DO NOTHING
    """
).bindparams(bindparam("key_facts", type_=JSONB))
# The condenser may fill a direction only where the code tagger found the topic but no direction.
_FILL_DIRECTION = text(
    """
    UPDATE tagged_events SET direction = :direction, tagged_by = 'hermes'
    WHERE news_item_id = :news_item_id AND driver_id = :driver_id
      AND direction = 0 AND tagged_by = 'code'
    """
)


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user: Template


@dataclass(frozen=True)
class Candidate:
    ticker_id: str
    symbol: str
    name: str
    on_watchlist: bool


@dataclass(frozen=True)
class Driver:
    id: str
    name: str
    plus_one: str
    minus_one: str | None  # None: the tag has no -1 direction


@dataclass(frozen=True)
class Item:
    id: int
    title: str
    excerpt: str | None
    published_at: datetime | None
    ticker_ids: tuple[str, ...]  # attached by the sources, over the item and its duplicates
    driver: Driver | None  # set only when the tagger found exactly one driver


class _ModelDown(Exception):
    """The call itself failed. The item stays pending."""

    def __init__(self, message: str, calls: int):
        super().__init__(message)
        self.calls = calls  # calls made for the item, the failed one included


def load_prompt(path: Path = PROMPT_FILE, version: str = PROMPT_VERSION) -> Prompt:
    content = path.read_text(encoding="utf-8")
    match = re.fullmatch(r"\s*## System\s*\n(.*?)\n## User\s*\n(.*)", content, re.DOTALL)
    if not match:
        raise ValueError(f"{path.name}: expected a '## System' and a '## User' section")
    return Prompt(version, match.group(1).strip(), Template(match.group(2).strip()))


def render_user(prompt: Prompt, item: Item, candidates: list[Candidate]) -> str:
    listed = [c for c in candidates if c.on_watchlist or c.ticker_id in item.ticker_ids]
    driver = "none"
    if item.driver:
        minus = f"-1 means {item.driver.minus_one}" if item.driver.minus_one else "-1 is not used"
        driver = f"{item.driver.name}. +1 means {item.driver.plus_one}, {minus}."
    published = item.published_at.strftime("%Y-%m-%d") if item.published_at else "unknown"
    return prompt.user.substitute(
        title=item.title,
        excerpt=item.excerpt or "(none)",
        published=published,
        candidates=", ".join(f"{c.symbol} ({c.name})" for c in listed),
        driver=driver,
    )


def validate_answer(
    answer: str, item: Item, candidates: list[Candidate]
) -> tuple[str, CondenserOutput | None, str | None]:
    """(outcome, parsed output or None, reason it failed or None)."""
    try:
        data = json.loads(answer)
    except ValueError as exc:
        return "invalid_json", None, f"The answer is not valid JSON: {exc}"
    try:
        output = CondenserOutput.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'answer'}: {error['msg']}"
            for error in exc.errors()
        )
        return "schema_failed", None, problems

    allowed = {c.symbol for c in candidates if c.on_watchlist or c.ticker_id in item.ticker_ids}
    unknown = sorted(set(output.tickers) - allowed)
    if unknown:
        return "schema_failed", None, f"tickers not in the candidate list: {', '.join(unknown)}"
    if output.driver_direction != 0:
        if item.driver is None:
            return "schema_failed", None, "driver_direction must be 0 when no driver is given"
        if output.driver_direction == -1 and item.driver.minus_one is None:
            return "schema_failed", None, f"{item.driver.name} has no -1 direction"

    written = " ".join([*output.key_facts, output.one_sentence_reason])
    given = " ".join(
        part
        for part in (
            item.title,
            item.excerpt,
            item.published_at.strftime("%Y-%m-%d %B %d %Y") if item.published_at else None,
        )
        if part
    )
    invented = check_numbers(written, given)
    if invented:
        return (
            "number_check_failed",
            None,
            f"numbers that are not in the item: {', '.join(invented)}",
        )
    return "ok", output, None


def _store(conn, item: Item, output: CondenserOutput, candidates, trace_id: int, model: str) -> int:
    by_symbol = {c.symbol: c.ticker_id for c in candidates}
    rows = [
        {
            "news_item_id": item.id,
            "ticker_id": by_symbol[symbol],
            "catalyst_type": output.catalyst_type,
            "sentiment_score": round(output.sentiment_score, 3),
            "driver_direction": output.driver_direction,
            "key_facts": output.key_facts,
            "one_sentence_reason": output.one_sentence_reason,
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "llm_trace_id": trace_id,
        }
        for symbol in sorted(set(output.tickers))
    ]
    if rows:
        conn.execute(_INSERT_SENTIMENT, rows)
    if item.driver and output.driver_direction != 0:
        conn.execute(
            _FILL_DIRECTION,
            {
                "news_item_id": item.id,
                "driver_id": item.driver.id,
                "direction": output.driver_direction,
            },
        )
    return len(rows)


def condense_item(
    engine: Engine,
    client: LlmClient,
    prompt: Prompt,
    item: Item,
    candidates: list[Candidate],
    calls_left: int,
) -> dict:
    """Condense one item. Returns {'outcome', 'calls', 'rows', 'cost_usd'}.

    outcome: 'ok', 'failed' (two bad answers), or 'stopped' (the call budget ran out first).
    Raises _ModelDown when the call itself fails.
    """
    base = render_user(prompt, item, candidates)
    schema = CondenserOutput.model_json_schema()
    trace = {
        "template": TEMPLATE,
        "prompt_version": prompt.version,
        "tier": client.tier,
        "model": client.model,
        "news_item_id": item.id,
    }
    calls, cost, reason = 0, 0.0, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if calls >= calls_left:
            return {"outcome": "stopped", "calls": calls, "rows": 0, "cost_usd": cost}
        user = base
        if reason:
            user += (
                f"\n\nYour previous answer was rejected: {reason}\n"
                "Answer again with a corrected JSON object only."
            )
        calls += 1
        try:
            result = client.complete(prompt.system, user, schema)
        except LlmError as exc:
            with engine.begin() as conn:
                write_trace(conn, **trace, attempt=attempt, outcome="error", error=str(exc))
            raise _ModelDown(str(exc), calls) from exc
        cost += result.cost_usd or 0.0
        outcome, output, reason = validate_answer(result.text, item, candidates)
        with engine.begin() as conn:
            trace_id = write_trace(
                conn,
                **trace,
                attempt=attempt,
                outcome=outcome,
                error=reason,
                latency_ms=result.latency_ms,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=result.cost_usd,
            )
            if output is not None:
                rows = _store(conn, item, output, candidates, trace_id, client.model)
                return {"outcome": "ok", "calls": calls, "rows": rows, "cost_usd": cost}
    return {"outcome": "failed", "calls": calls, "rows": 0, "cost_usd": cost}


def _load_pending(conn, prompt_version: str, since: datetime) -> list[Item]:
    drivers = {
        row["id"]: Driver(row["id"], row["name"], row["plus_one_meaning"], row["minus_one_meaning"])
        for row in conn.execute(_TAXONOMY).mappings()
    }
    params = {
        "since": since,
        "template": TEMPLATE,
        "prompt_version": prompt_version,
        "max_attempts": MAX_ATTEMPTS,
    }
    items = []
    for row in conn.execute(_PENDING, params).mappings():
        tags = row["tags"] or []
        items.append(
            Item(
                id=row["id"],
                title=row["title"],
                excerpt=row["excerpt"],
                published_at=row["published_at"],
                ticker_ids=tuple(row["tickers"] or ()),
                driver=drivers[tags[0]["driver"]] if len(tags) == 1 else None,
            )
        )
    return items


def condense_pending(
    engine: Engine,
    client: LlmClient,
    *,
    daily_cap: int,
    run_cap: int,
    monthly_budget: float,
    lookback: timedelta,
    prompt: Prompt | None = None,
    now: datetime | None = None,
) -> dict:
    """Condense pending items, most important first, inside the daily cap and the month's budget.

    The budget is compared with the cost in US dollars as if it were euros. A dollar is worth
    less than a euro, so this stops slightly early, never late.
    """
    now = now or datetime.now(UTC)
    prompt = prompt or load_prompt()
    with engine.connect() as conn:
        candidates = [
            Candidate(row["id"], row["symbol"], row["display_name"], row["on_watchlist"])
            for row in conn.execute(_CANDIDATES).mappings()
        ]
        pending = _load_pending(conn, prompt.version, now - lookback)
        used_today = calls_today(conn, TEMPLATE, now)
        month_cost = cost_this_month_usd(conn, now)

    result = {
        "ok": True,
        "model": client.model,
        "prompt_version": prompt.version,
        "pending": len(pending),
        "condensed": 0,
        "failed": 0,
        "calls": 0,
        "sentiment_rows": 0,
        "capped": None,
        "errors": [],
    }
    transport_errors = 0
    for item in pending:
        calls_left = min(daily_cap - used_today, run_cap) - result["calls"]
        if month_cost >= monthly_budget:
            result["capped"] = f"monthly budget reached ({month_cost:.4f} of {monthly_budget:g})"
        elif daily_cap - used_today - result["calls"] <= 0:
            result["capped"] = f"daily cap of {daily_cap} calls reached"
        elif calls_left <= 0:
            result["capped"] = f"run cap of {run_cap} calls reached"
        if result["capped"]:
            break
        try:
            done = condense_item(engine, client, prompt, item, candidates, calls_left)
        except _ModelDown as exc:
            result["calls"] += exc.calls
            result["errors"].append(str(exc))
            transport_errors += 1
            if transport_errors >= MAX_TRANSPORT_ERRORS:
                result["errors"].append(
                    f"stopped after {transport_errors} failed calls in a row: the model is down"
                )
                break
            continue
        transport_errors = 0
        result["calls"] += done["calls"]
        result["sentiment_rows"] += done["rows"]
        month_cost += done["cost_usd"]
        if done["outcome"] == "ok":
            result["condensed"] += 1
        elif done["outcome"] == "failed":
            result["failed"] += 1

    result["left_pending"] = result["pending"] - result["condensed"] - result["failed"]
    if result["capped"] and result["left_pending"]:
        with engine.begin() as conn:
            write_trace(
                conn,
                template=TEMPLATE,
                prompt_version=prompt.version,
                tier=client.tier,
                model=client.model,
                outcome="capped",
                error=f"{result['capped']}; {result['left_pending']} items left pending",
            )
    result["ok"] = not result["errors"]
    return result
