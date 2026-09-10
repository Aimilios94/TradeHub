"""The monthly rules-freshness check (plan §10, §11).

The engine relies on a handful of broker rules being true — extended-hours order types, no brackets
pre-market, PDT retired, SIP delay, sell-side fees, shorting requirements. `docs/BROKER_RULES.md` states
each one and where it came from. This job watches those source pages and raises a `compliance_events`
row when one changes.

Detection is deterministic, not a judgement call: fetch each page, normalise it, hash it, compare against
the hash stored in `app_settings.rules_file`. A changed hash means the page moved — the job says which
rule depends on it and asks for a human read. It does NOT decide the rule changed, and it never edits the
rules file.

Why this exists at all: the PDT retirement is the worked example. A rule that quietly *disappears* is as
dangerous as one that quietly appears, and neither announces itself.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.db import get_client
from app.marketdata import repo as md_repo
from app.notify import send_telegram

log = logging.getLogger(__name__)

RULES_DOC = "docs/BROKER_RULES.md"
TIMEOUT = 30.0

# rule id → (what it governs, source page)
WATCHED: dict[str, tuple[str, str]] = {
    "R1": ("extended-hours order types (limit/day/extended_hours only)",
           "https://docs.alpaca.markets/docs/orders-at-alpaca"),
    "R2": ("no brackets or OCO outside regular hours",
           "https://docs.alpaca.markets/docs/orders-at-alpaca"),
    "R3": ("PDT rules retired; only buying_power gates",
           "https://docs.alpaca.markets/docs/user-protection"),
    "R4": ("free-plan SIP delay and IEX pre-market coverage",
           "https://docs.alpaca.markets/docs/real-time-stock-pricing-data"),
    # verified reachable 2026-09-10; the /docs/fees and /docs/margin-and-shorting paths were already 404
    "R5": ("SEC/TAF charged on sells only",
           "https://alpaca.markets/disclosures"),
    "R6": ("shorting needs margin + shortable + easy-to-borrow",
           "https://docs.alpaca.markets/docs/margin-and-short-selling"),
}

_WHITESPACE = re.compile(r"\s+")
_VOLATILE = re.compile(r"(csrf|nonce|build-id|__NEXT_DATA__|_buildManifest|\"ts\":\d+)", re.IGNORECASE)


def normalise(html: str) -> str:
    """Strip the parts of a page that change on every request, so the hash tracks content, not noise."""
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _VOLATILE.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def page_hash(url: str) -> tuple[str | None, str | None]:
    """(sha256 of the normalised page, error). Never raises."""
    try:
        r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; tradehub/0.1)"})
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:140]}"
    body = normalise(r.text)
    if len(body) < 200:
        return None, f"page looks empty after normalising ({len(body)} chars) — possibly JS-rendered"
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), None


def stored() -> dict:
    return md_repo.fetch_setting("rules_file", {}) or {}


def save(state: dict) -> bool:
    try:
        get_client().table("app_settings").update({"value": state}).eq("key", "rules_file").execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.error("rules_file save failed: %s", e)
        return False


@dataclass(slots=True)
class FreshnessResult:
    checked_at: str
    version: int = 1
    changed: list[dict] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    unreachable: list[dict] = field(default_factory=list)
    baselined: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"checked_at": self.checked_at, "version": self.version, "changed": self.changed,
                "unchanged": self.unchanged, "unreachable": self.unreachable,
                "baselined": self.baselined, "errors": self.errors}

    def summary(self) -> str:
        lines = [f"📜 Rules freshness check — {self.checked_at[:10]}"]
        if self.changed:
            lines.append(f"⚠️ {len(self.changed)} source page(s) CHANGED — read them:")
            for c in self.changed:
                lines.append(f"   {c['rule']}: {c['governs']}")
                lines.append(f"      {c['url']}")
        else:
            lines.append(f"   no changes across {len(self.unchanged)} watched page(s)")
        if self.baselined:
            lines.append(f"   first look at: {', '.join(self.baselined)} (baseline recorded)")
        for u in self.unreachable:
            lines.append(f"   ❓ {u['rule']} unreachable: {u['error'][:70]}")
        if self.changed:
            lines.append("A changed page does not mean the rule changed — it means someone should read it "
                         "and update docs/BROKER_RULES.md.")
        return "\n".join(lines)


def run_check(*, notify: bool = True, rebaseline: bool = False) -> FreshnessResult:
    """Monthly. Flags moved pages; never edits the rules file and never decides a rule changed."""
    now = dt.datetime.now(tz=dt.UTC)
    state = stored()
    hashes: dict = dict(state.get("hashes") or {})
    res = FreshnessResult(checked_at=now.isoformat(), version=int(state.get("version", 1)))

    for rule, (governs, url) in WATCHED.items():
        digest, error = page_hash(url)
        if digest is None:
            res.unreachable.append({"rule": rule, "url": url, "error": error or "unknown"})
            continue
        previous = hashes.get(rule)
        if previous is None:
            res.baselined.append(rule)
        elif previous != digest and not rebaseline:
            res.changed.append({"rule": rule, "governs": governs, "url": url,
                                "previous_hash": previous[:12], "new_hash": digest[:12]})
        else:
            res.unchanged.append(rule)
        hashes[rule] = digest

    state |= {"version": res.version, "last_checked": now.isoformat(), "hashes": hashes,
              "doc": RULES_DOC,
              "flagged_changes": [c["rule"] for c in res.changed]}
    if not save(state):
        res.errors.append("could not persist the new hashes; the next run will re-flag the same pages")

    for c in res.changed:
        try:
            get_client().table("compliance_events").insert([{
                "kind": "rules_update", "verdict": "yellow",
                "reason": f"{c['rule']} source page changed ({c['governs']}) — read {c['url']} and "
                          f"update {RULES_DOC} if the rule moved",
                "detail": c,
            }]).execute()
        except Exception as e:  # noqa: BLE001
            res.errors.append(f"compliance_events write failed for {c['rule']}: {str(e)[:100]}")

    if notify:
        send_telegram(res.summary())
    return res


def rules_summary() -> dict:
    """What the briefing is told about rule freshness."""
    state = stored()
    last = state.get("last_checked")
    stale_days = None
    if last:
        try:
            stale_days = (dt.datetime.now(tz=dt.UTC)
                          - dt.datetime.fromisoformat(last)).days
        except ValueError:
            stale_days = None
    return {
        "version": state.get("version", 1), "last_checked": last,
        "days_since_check": stale_days,
        "is_stale": stale_days is None or stale_days > 45,
        "flagged_changes": state.get("flagged_changes") or [],
        "watched_rules": {k: v[0] for k, v in WATCHED.items()},
        "doc": RULES_DOC,
    }
