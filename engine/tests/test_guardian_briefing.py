"""Phase 6.5: the rules-freshness check and the Layer 3 briefing.

Offline. The properties under test: the freshness job flags but never decides, and the briefing never
drops its disclaimer or hides a missing figure.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app.config import get_settings
from app.guardian import briefing as briefing_mod
from app.guardian import rules as rules_mod
from app.main import app

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_settings().engine_api_token}"}


# ---------- page normalisation and hashing ----------

def test_normalise_strips_markup_scripts_and_styles():
    html = """<html><head><style>.a{color:red}</style><script>var nonce=1;</script></head>
              <body><h1>Extended hours</h1><p>limit orders only</p></body></html>"""
    out = rules_mod.normalise(html)
    assert "Extended hours" in out and "limit orders only" in out
    assert "color:red" not in out and "var nonce" not in out and "<" not in out


def test_normalise_ignores_per_request_noise():
    a = rules_mod.normalise('<body>Rule text<script>csrf="abc"</script><span>"ts":1712345</span></body>')
    b = rules_mod.normalise('<body>Rule text<script>csrf="zzz"</script><span>"ts":9999999</span></body>')
    assert a == b          # the same content must hash the same across requests


def test_a_short_page_is_treated_as_unreadable_not_as_content(monkeypatch):
    class R:
        text = "<html><body>404</body></html>"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(rules_mod.httpx, "get", lambda *a, **kw: R())
    digest, error = rules_mod.page_hash("https://example.test")
    assert digest is None and "empty after normalising" in error


def test_an_unreachable_page_returns_an_error_not_an_exception(monkeypatch):
    monkeypatch.setattr(rules_mod.httpx, "get",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    digest, error = rules_mod.page_hash("https://example.test")
    assert digest is None and "RuntimeError" in error


# ---------- the freshness run ----------

# R1 and R2 deliberately share one source page, so a per-URL hash is what both of them see.
def _pages(value_by_url: dict | None = None) -> dict[str, str]:
    return value_by_url or {url: f"hash-of-{url}" for _r, (_g, url) in rules_mod.WATCHED.items()}


def _baseline_from(pages: dict[str, str]) -> dict[str, str]:
    return {rule: pages[url] for rule, (_g, url) in rules_mod.WATCHED.items() if url in pages}


def _run(monkeypatch, hashes: dict, pages: dict[str, str], rebaseline: bool = False):
    saved: list[dict] = []
    events: list[dict] = []
    monkeypatch.setattr(rules_mod, "stored", lambda: {"version": 2, "hashes": dict(hashes)})
    monkeypatch.setattr(rules_mod, "save", lambda state: saved.append(state) or True)
    monkeypatch.setattr(rules_mod, "send_telegram", lambda t: True)

    class C:
        def table(self, name):
            return self

        def insert(self, rows):
            events.extend(rows)
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    monkeypatch.setattr(rules_mod, "get_client", lambda: C())
    monkeypatch.setattr(rules_mod, "page_hash",
                        lambda url: (pages.get(url), None) if pages.get(url) else (None, "unreachable"))
    res = rules_mod.run_check(notify=True, rebaseline=rebaseline)
    return res, saved, events


def test_a_first_look_records_a_baseline_and_flags_nothing(monkeypatch):
    pages = _pages()
    res, saved, events = _run(monkeypatch, {}, pages)
    assert set(res.baselined) == set(rules_mod.WATCHED)
    assert res.changed == [] and events == []
    assert saved and saved[0]["hashes"]


def test_an_unchanged_page_is_not_re_flagged(monkeypatch):
    pages = _pages()
    res, _saved, events = _run(monkeypatch, _baseline_from(pages), pages)
    assert res.changed == [] and events == []
    assert set(res.unchanged) == set(rules_mod.WATCHED)


def test_a_moved_page_is_flagged_with_the_rule_that_depends_on_it(monkeypatch):
    pages = _pages()
    existing = _baseline_from(pages)
    existing["R3"] = "something-older"
    res, _saved, events = _run(monkeypatch, existing, pages)
    assert [c["rule"] for c in res.changed] == ["R3"]
    assert "PDT" in res.changed[0]["governs"]
    assert events and events[0]["kind"] == "rules_update" and events[0]["verdict"] == "yellow"
    assert "read" in events[0]["reason"] and "BROKER_RULES.md" in events[0]["reason"]


def test_rebaseline_accepts_the_new_page_without_flagging(monkeypatch):
    pages = _pages({url: "new" for _r, (_g, url) in rules_mod.WATCHED.items()})
    existing = {rule: "old" for rule in rules_mod.WATCHED}
    res, saved, events = _run(monkeypatch, existing, pages, rebaseline=True)
    assert res.changed == [] and events == []
    assert all(v == "new" for v in saved[0]["hashes"].values())


def test_an_unreachable_page_is_reported_not_silently_passed(monkeypatch):
    res, _saved, _events = _run(monkeypatch, {}, {})
    assert len(res.unreachable) == len(rules_mod.WATCHED)
    assert res.changed == [] and res.unchanged == []
    assert "unreachable" in res.summary()


def test_the_summary_says_a_changed_page_is_not_a_changed_rule(monkeypatch):
    pages = _pages()
    existing = _baseline_from(pages)
    existing["R1"] = "older"
    res, _s, _e = _run(monkeypatch, existing, pages)
    assert "does not mean the rule changed" in res.summary()


def test_freshness_is_stale_when_never_checked_or_long_ago(monkeypatch):
    monkeypatch.setattr(rules_mod, "stored", dict)
    assert rules_mod.rules_summary()["is_stale"] is True
    old = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=60)).isoformat()
    monkeypatch.setattr(rules_mod, "stored", lambda: {"last_checked": old, "version": 3})
    out = rules_mod.rules_summary()
    assert out["is_stale"] is True and out["days_since_check"] == 60 and out["version"] == 3
    recent = (dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=3)).isoformat()
    monkeypatch.setattr(rules_mod, "stored", lambda: {"last_checked": recent})
    assert rules_mod.rules_summary()["is_stale"] is False


def test_every_watched_rule_is_documented():
    """A rule the job watches but nobody wrote down is a flag no one will understand."""
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "BROKER_RULES.md").read_text(encoding="utf-8")
    for rule in rules_mod.WATCHED:
        assert f"## {rule} " in doc, f"{rule} is watched but not described in BROKER_RULES.md"


# ---------- the briefing ----------

def test_the_tax_disclaimer_is_added_when_the_model_forgets_it(monkeypatch):
    monkeypatch.setattr(briefing_mod, "gather", lambda q="": {"as_of": "2026-09-10T12:00:00-04:00",
                                                              "paper_or_live": "PAPER", "kill_switch": False,
                                                              "account": {}, "ledger": {},
                                                              "compliance_events": [],
                                                              "portfolio_risk_summary": None,
                                                              "rules_file": {}, "previous_briefing": None,
                                                              "user_question": q})
    monkeypatch.setattr(briefing_mod, "load_prompt", lambda slug: object())
    monkeypatch.setattr(briefing_mod, "complete_json",
                        lambda spec, v: ({"summary": "fine", "tax_line": "Provision is $0.00."},
                                         {"model": "m", "attempts": 1}))
    monkeypatch.setattr(briefing_mod, "send_telegram", lambda t: True)
    out = briefing_mod.run_briefing(notify=False, store=False)
    assert "confirm with an accountant" in out["briefing"]["tax_line"].lower()
    assert "monitor, not an advisor" in out["boundary"]


def test_an_existing_disclaimer_is_not_duplicated(monkeypatch):
    monkeypatch.setattr(briefing_mod, "gather", lambda q="": {"as_of": "x", "paper_or_live": "PAPER",
                                                              "kill_switch": False, "account": {},
                                                              "ledger": {}, "compliance_events": [],
                                                              "portfolio_risk_summary": None,
                                                              "rules_file": {}, "previous_briefing": None,
                                                              "user_question": ""})
    monkeypatch.setattr(briefing_mod, "load_prompt", lambda slug: object())
    line = "Provision is $0.00. " + briefing_mod.TAX_DISCLAIMER
    monkeypatch.setattr(briefing_mod, "complete_json",
                        lambda spec, v: ({"tax_line": line}, {"model": "m", "attempts": 1}))
    out = briefing_mod.run_briefing(notify=False, store=False)
    assert out["briefing"]["tax_line"].lower().count("accountant") == 1


def test_a_failed_briefing_still_returns_the_real_figures(monkeypatch):
    from app.llm import errors as llm_errors

    inputs = {"as_of": "x", "paper_or_live": "PAPER", "kill_switch": False,
              "account": {"equity": 100.0}, "ledger": {}, "compliance_events": [],
              "portfolio_risk_summary": None, "rules_file": {}, "previous_briefing": None,
              "user_question": ""}
    monkeypatch.setattr(briefing_mod, "gather", lambda q="": inputs)
    monkeypatch.setattr(briefing_mod, "load_prompt", lambda slug: object())
    monkeypatch.setattr(briefing_mod, "complete_json",
                        lambda spec, v: (_ for _ in ()).throw(llm_errors.LLMUnavailable("down")))
    sent: list[str] = []
    monkeypatch.setattr(briefing_mod, "send_telegram", lambda t: sent.append(t) or True)
    out = briefing_mod.run_briefing(notify=True, store=False)
    assert "error" in out and out["inputs"]["account"]["equity"] == 100.0
    assert "figures above are still accurate" in out["note"]
    assert sent and "FAILED" in sent[0]


def test_the_telegram_text_leads_with_the_kill_switch_when_engaged():
    text = briefing_mod.telegram_text({"summary": "all quiet"},
                                      {"as_of": "2026-09-10T12:00", "paper_or_live": "PAPER",
                                       "kill_switch": True})
    assert "KILL SWITCH ENGAGED" in text.split("\n")[1]


# ---------- endpoints ----------

def test_guardian_endpoints_require_auth():
    for method, path in (("post", "/guardian/briefing"), ("get", "/guardian/state"),
                         ("post", "/guardian/rules-check"), ("get", "/guardian/rules"),
                         ("post", "/guardian/gate/check?symbol=A&qty=1&entry=1&stop=0.5")):
        assert getattr(client, method)(path).status_code == 401


def test_gate_check_validates_direction():
    r = client.post("/guardian/gate/check?symbol=AAPL&direction=sideways&qty=1&entry=1&stop=0.5",
                    headers=AUTH)
    assert r.status_code == 422
