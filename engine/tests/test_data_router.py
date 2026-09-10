"""/data/* endpoints and the nightly job with Alpaca + Supabase stubbed out (no network)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.marketdata import alpaca, jobs, repo
from app.marketdata.alpaca import ET, Bar

client = TestClient(app)
TOKEN = get_settings().engine_api_token or "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
DAY = dt.date(2026, 9, 9)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(get_settings(), "engine_api_token", TOKEN)


def bar(sym, hh, mm, vol, day=DAY, feed="sip"):
    return Bar(symbol=sym, ts=dt.datetime.combine(day, dt.time(hh, mm), ET), open=1, high=1, low=1, close=1,
               volume=vol, vwap=None, trade_count=None, feed=feed)


def test_auth_required():
    assert client.get("/data/quotes?symbols=SPY").status_code == 401
    assert client.get("/data/quotes?symbols=SPY", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/health").status_code == 200  # stays open


def test_quotes_uses_iex_and_validates(monkeypatch):
    monkeypatch.setattr(alpaca, "get_latest_quotes",
                        lambda syms, feed="iex": {s: {"bid": 1.0, "ask": 1.01, "feed": feed} for s in syms})
    r = client.get("/data/quotes?symbols=spy, qqq", headers=AUTH)
    assert r.status_code == 200 and set(r.json()["quotes"]) == {"SPY", "QQQ"} and r.json()["feed"] == "iex"
    assert client.get("/data/quotes?symbols=SPY&feed=nope", headers=AUTH).status_code == 422
    assert client.get("/data/quotes?symbols=,", headers=AUTH).status_code == 422


def test_upstream_error_is_surfaced_not_faked(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("alpaca down")

    monkeypatch.setattr(alpaca, "get_latest_quotes", boom)
    r = client.get("/data/quotes?symbols=SPY", headers=AUTH)
    assert r.status_code == 502 and "alpaca down" in r.json()["detail"]


def test_premarket_relvol_same_feed_same_checkpoint(monkeypatch):
    live = [bar("ABC", 4, 5, 100), bar("ABC", 7, 0, 400), bar("ABC", 7, 44, 500)]  # SIP delayed: latest bar 07:44
    monkeypatch.setattr(alpaca, "get_bars", lambda syms, start, end, feed="iex", timeframe="1Min": live)
    monkeypatch.setattr(alpaca, "now_et", lambda: dt.datetime.combine(DAY, dt.time(8, 0), ET))
    monkeypatch.setattr(repo, "fetch_setting", lambda key, default=None: {"premarket_volume": "sip"} if key == "data_feed" else default)
    rows = [{"symbol": "ABC", "trade_date": (DAY - dt.timedelta(days=i)).isoformat(), "feed": "sip",
             "premarket_volume": 5000, "cum_profile": {"07:30": 200, "08:00": 900}} for i in range(1, 15)]
    monkeypatch.setattr(repo, "fetch_baseline_rows", lambda *a, **k: rows)

    r = client.get("/data/premarket/abc", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["feed"] == "sip" and body["as_of_requested"] == "08:00"
    assert body["as_of_effective"] == "07:45"           # clamped to latest delayed bar + 1 min
    assert body["checkpoint"] == "07:30"                 # nearest checkpoint ≤ effective window
    assert body["current_volume"] == 1000                # all three bars start before 07:45
    assert body["baseline"]["average"] == 200 and body["baseline"]["days_used"] == 14
    assert body["relvol"] == 5.0 and body["note"] is None


def test_nightly_job_writes_both_feeds_and_calibration(monkeypatch):
    def fake_bars(symbols, start, end, feed="iex", timeframe="1Min"):
        day = start.astimezone(ET).date()
        if feed == "iex":
            return [bar("AAA", 8, 0, 10, day, "iex"), bar("AAA", 10, 0, 100, day, "iex")]
        return [bar("AAA", 8, 0, 1000, day, "sip"), bar("AAA", 10, 0, 10000, day, "sip"), bar("BBB", 7, 0, 300, day, "sip")]

    written: dict[str, list] = {}
    monkeypatch.setattr(alpaca, "get_bars", fake_bars)
    monkeypatch.setattr(repo, "upsert_baselines", lambda rows: written.setdefault("b", rows) and len(rows))
    monkeypatch.setattr(repo, "upsert_calibrations", lambda rows: written.setdefault("c", rows) and len(rows))
    monkeypatch.setattr(repo, "baseline_universe", lambda: ["AAA", "BBB"])
    sent: list[str] = []
    monkeypatch.setattr(jobs, "send_telegram", lambda text: sent.append(text) or True)

    res = jobs.run_premarket_baseline(end_date=DAY, days=2, notify=True)
    assert res.dates == ["2026-09-08", "2026-09-09"] and res.symbols == 2 and not res.errors
    assert res.baseline_rows == 8            # 2 days × 2 feeds × 2 symbols
    assert res.calibration_rows == 8         # 2 days × 2 symbols × 2 windows
    iex_aaa = next(r for r in written["b"] if r["symbol"] == "AAA" and r["feed"] == "iex")
    assert iex_aaa["premarket_volume"] == 10 and iex_aaa["cum_profile"]["09:30"] == 10 and iex_aaa["cum_profile"]["08:00"] == 0
    cal = next(r for r in written["c"] if r["symbol"] == "BBB" and r["session_window"] == "premarket")
    assert cal == {"symbol": "BBB", "trade_date": "2026-09-09", "session_window": "premarket", "iex_volume": 0, "sip_volume": 300} or cal["trade_date"] == "2026-09-08"
    assert res.zero_iex_premarket == 2       # BBB had SIP prints but no IEX prints on both days
    assert res.median_premarket_ratio == 0.005
    assert sent and sent[0].startswith("✅")


def test_nightly_job_skips_holiday_and_reports_fetch_errors(monkeypatch):
    calls = []

    def fake_bars(symbols, start, end, feed="iex", timeframe="1Min"):
        calls.append(feed)
        if feed == "sip":
            raise RuntimeError("rate limited")
        return []

    monkeypatch.setattr(alpaca, "get_bars", fake_bars)
    res = jobs.run_premarket_baseline(end_date=DAY, days=1, symbols=["aaa"], dry_run=True)
    assert res.skipped_dates == ["2026-09-09"] and res.baseline_rows == 0
    assert len(res.errors) == 1 and "rate limited" in res.errors[0]


def test_run_endpoint_passes_through(monkeypatch):
    monkeypatch.setattr(repo, "fetch_setting", lambda key, default=None: default)
    monkeypatch.setattr(jobs, "run_premarket_baseline",
                        lambda **kw: jobs.JobResult(dates=["2026-09-09"], symbols=len(kw["symbols"]), baseline_rows=0))
    r = client.post("/data/premarket-baseline/run?symbols=SPY,QQQ&dry_run=true", headers=AUTH)
    assert r.status_code == 200 and r.json()["symbols"] == 2
    assert client.post("/data/premarket-baseline/run").status_code == 401
