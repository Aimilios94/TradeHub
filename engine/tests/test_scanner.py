"""Scanner: condition evaluator, stage-A filter, and a full morning run with Alpaca/Supabase stubbed."""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient

from app.config import get_settings
from app.detectors import DetectorResult
from app.main import app
from app.marketdata import alpaca
from app.marketdata import repo as md_repo
from app.marketdata.alpaca import ET, Bar
from app.models import Condition
from app.scanner import conditions
from app.scanner import run as scan_run
from app.scanner import universe as universe_job
from app.seed.strategies import all_strategies

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {get_settings().engine_api_token}"}
DAY = dt.date(2026, 9, 9)


# ---------- conditions ----------

def test_condition_evaluation_pass_fail_pending():
    c = Condition(detector="gap_pct", op=">=", value=10.0)
    assert conditions.evaluate(c, DetectorResult("gap_pct", 12.0, {"abs": 12.0})).status == "pass"
    assert conditions.evaluate(c, DetectorResult("gap_pct", 3.0, {"abs": 3.0})).status == "fail"
    assert conditions.evaluate(c, None).status == "pending"
    assert conditions.evaluate(c, DetectorResult("gap_pct", None, {}, quality="degraded")).status == "pending"
    cf = Condition(detector="gap_pct", field="abs", op=">=", value=3.0)
    assert conditions.evaluate(cf, DetectorResult("gap_pct", -4.0, {"abs": 4.0})).passed is True
    ct = Condition(detector="premarket_high_low", op="true")
    assert conditions.evaluate(ct, DetectorResult("premarket_high_low", 10.0, {})).passed is True
    s = conditions.summarize([conditions.evaluate(c, DetectorResult("gap_pct", 3.0, {"abs": 3.0})),
                              conditions.evaluate(cf, None)])
    assert s["passed"] is False and len(s["failed"]) == 1 and s["pending"] == ["gap_pct"]
    assert conditions.compare("between", 5, (1, 10)) and conditions.compare("in", "a", ["a"]) and not conditions.compare(">", None, 1)


# ---------- stage A ----------

def test_stage_a_filter_from_config_params():
    stats = {"AAA": {"prev_close": 5.0, "adv20": 3_000_000, "atr14": 0.5, "shares_outstanding": 10_000_000},
             "BBB": {"prev_close": 50.0, "adv20": 3_000_000, "atr14": 2.0},
             "CCC": {"prev_close": 5.0, "adv20": 100_000, "atr14": 0.5, "float_shares": 50_000_000},
             "DDD": {"prev_close": 5.0, "adv20": 100_000, "atr14": 0.5}}
    s1 = next(s for s in all_strategies() if s.code == "S1").universe.params
    keep, info = scan_run.stage_a_filter(s1, stats)
    assert set(keep) == {"AAA", "DDD"} and info["unknown_float"] == 1     # BBB price, CCC float; DDD float unknown → kept + flagged
    s3 = next(s for s in all_strategies() if s.code == "S3").universe.params
    keep, _ = scan_run.stage_a_filter(s3, stats)
    assert set(keep) == {"BBB"}
    assert scan_run.gap_filter(s1, 12.0) and not scan_run.gap_filter(s1, 9.9) and not scan_run.gap_filter(s1, None)
    assert scan_run.gap_filter(s3, -3.5) and not scan_run.gap_filter(s3, 2.0)


# ---------- universe stats ----------

def test_stats_from_daily():
    bars = [Bar("XYZ", dt.datetime.combine(DAY - dt.timedelta(days=i), dt.time(0, 0), ET), 10, 11, 9, 10, 1_000_000, None, None, "sip")
            for i in range(30, 0, -1)]
    st = universe_job.stats_from_daily("XYZ", universe_job.daily_frame(bars))
    assert st["prev_close"] == 10 and st["adv20"] == 1_000_000 and st["sessions"] == 30
    assert st["atr14"] == 2.0 and st["high_20d"] == 11 and st["low_60d"] == 9 and st["as_of"] == "2026-09-08"


# ---------- full morning run ----------

def _pm(sym, rows):
    out = []
    for hhmm, px, vol in rows:
        hh, mm = map(int, hhmm.split(":"))
        out.append(Bar(sym, dt.datetime.combine(DAY, dt.time(hh, mm), ET), px, px, px, px, vol, None, None, "sip"))
    return out


def _daily(sym, close, vol, rng):
    return [Bar(sym, dt.datetime.combine(DAY - dt.timedelta(days=i), dt.time(0, 0), ET), close, close + rng / 2, close - rng / 2,
                close, vol, None, None, "sip") for i in range(40, 0, -1)]


def test_run_morning_end_to_end(monkeypatch):
    strategies = {s.code: s for s in all_strategies()}
    monkeypatch.setattr(scan_run, "load_strategies", lambda statuses: [("id-s1", strategies["S1"]), ("id-s3", strategies["S3"]),
                                                                       ("id-s2", strategies["S2"]), ("id-s4", strategies["S4"])])
    stats_db = {
        "GAPR": {"symbol": "GAPR", "prev_close": 5.0, "adv20": 800_000, "atr14": 0.4, "tradable": True, "shares_outstanding": 12_000_000,
                 "high_20d": 6.0, "low_20d": 4.0, "high_60d": 7.0, "low_60d": 3.5, "shortable": False, "easy_to_borrow": False},
        "BIGC": {"symbol": "BIGC", "prev_close": 50.0, "adv20": 5_000_000, "atr14": 2.0, "tradable": True,
                 "high_20d": 52.0, "low_20d": 45.0, "high_60d": 55.0, "low_60d": 40.0, "shortable": True, "easy_to_borrow": True},
        "FLAT": {"symbol": "FLAT", "prev_close": 8.0, "adv20": 900_000, "atr14": 0.3, "tradable": True, "shares_outstanding": 5_000_000},
        "TSLA": {"symbol": "TSLA", "prev_close": 300.0, "adv20": 90_000_000, "atr14": 9.0, "tradable": True},
    }

    def fake_stats(price_min=None, price_max=None, adv_min=None, atr_min=None, symbols=None):
        out = {}
        for s, r in stats_db.items():
            if symbols and s not in symbols:
                continue
            if price_min is not None and r["prev_close"] < price_min:
                continue
            if price_max is not None and r["prev_close"] > price_max:
                continue
            if adv_min is not None and r["adv20"] < adv_min:
                continue
            if atr_min is not None and r["atr14"] < atr_min:
                continue
            out[s] = dict(r)
        return out

    monkeypatch.setattr(scan_run, "load_symbol_stats", fake_stats)
    pm = {"GAPR": _pm("GAPR", [(f"06:{i:02d}", 5.9, 4_000) for i in range(9)] + [("07:30", 6.0, 64_000)]),   # +20 % gap, 100k pm vol, 10 prints
          "BIGC": _pm("BIGC", [(f"07:{i:02d}", 52.0, 15_000) for i in range(19)] + [("07:40", 52.4, 415_000)]),  # +4.8 %, 700k = 14 % ADV, 20 prints
          "FLAT": _pm("FLAT", [("07:00", 8.05, 1_000)]),                                  # +0.6 % → fails gap
          "TSLA": _pm("TSLA", [("07:00", 303.0, 50_000)])}
    daily = {s: _daily(s, stats_db[s]["prev_close"], stats_db[s]["adv20"], stats_db[s]["atr14"] * 1.0) for s in stats_db}

    def fake_bars(symbols, start, end, feed="iex", timeframe="1Min"):
        if timeframe == "1Day":
            return [b for s in symbols for b in daily.get(s, [])]
        return [b for s in symbols for b in pm.get(s, [])]

    monkeypatch.setattr(alpaca, "get_bars", fake_bars)
    monkeypatch.setattr(md_repo, "fetch_setting", lambda key, default=None: {"premarket_volume": "sip", "baseline_days": 14} if key == "data_feed" else default)
    base = {"GAPR": [{"symbol": "GAPR", "trade_date": (DAY - dt.timedelta(days=i)).isoformat(), "feed": "sip", "premarket_volume": 20_000,
                      "cum_profile": {"07:30": 12_000, "08:00": 15_000}} for i in range(1, 15)],
            "BIGC": [{"symbol": "BIGC", "trade_date": (DAY - dt.timedelta(days=i)).isoformat(), "feed": "sip", "premarket_volume": 500_000,
                      "cum_profile": {"07:30": 300_000, "08:00": 350_000}} for i in range(1, 15)]}
    monkeypatch.setattr(scan_run, "baseline_rows_bulk", lambda symbols, feed, before, limit_days=30: {s: base.get(s, []) for s in symbols})
    monkeypatch.setattr(universe_job, "enrich_shares_outstanding", lambda symbols, **kw: {})   # FMP budget untouched in tests
    written: list[dict] = []
    monkeypatch.setattr(scan_run, "_write_scans", lambda rows: written.extend(rows) or len(rows))
    sent: list[str] = []
    monkeypatch.setattr(scan_run, "send_telegram", lambda t: sent.append(t) or True)

    res = scan_run.run_morning(now=dt.datetime.combine(DAY, dt.time(8, 0), ET), notify=True)
    assert not res.errors, res.errors
    by = {s["code"]: s for s in res.strategies}
    assert set(by) == {"S1", "S2", "S3"}                       # S4 (semi_auto watchlist) is not a screener strategy
    # S1: GAPR gap +20 %. Latest SIP bar in the batch is 07:40 → window clamped to 07:41 → checkpoint 07:30 → relvol 100k / 12k = 8.33 ≥ 5;
    #     float 12M < 20M (approximate) → pass. FLAT fails the gap filter.
    assert by["S1"]["passed"] == 1 and by["S1"]["top"][0]["symbol"] == "GAPR"
    gapr = next(r for r in written if r["symbol"] == "GAPR" and r["strategy_id"] == "id-s1")
    m = gapr["metrics"]
    assert gapr["passed"] is True and gapr["rank"] == 1 and gapr["scan_date"] == "2026-09-09" and gapr["run_kind"] == "morning"
    assert m["gap_pct"] == 20.0 and m["relvol"] == 8.333 and m["relvol_checkpoint"] == "07:30" and m["float_source"] == "fmp_shares_outstanding"
    assert res.as_of == "08:00" and m["as_of"] == "07:41"   # latest SIP bar in the batch is BIGC 07:40
    assert "float_shares" in m["quality_flags"] and "one_sentence_in_play_test" in m["pending_gates"]
    # S3: BIGC gap 4.8 % ≥ 3, ADV 5M > 2M, ATR 2 > 1, pct_of_adv 14 ≥ 10, distance to 52 high_20d = 0.2 ATR ≤ 1.5 → pass
    assert by["S3"]["passed"] == 1 and by["S3"]["top"][0]["symbol"] == "BIGC"
    bigc = next(r for r in written if r["symbol"] == "BIGC" and r["strategy_id"] == "id-s3")
    assert bigc["metrics"]["pct_of_adv"] == 14.0 and bigc["metrics"]["nearest_level"]["level_name"] == "high_20d"
    # S2 watchlist: informational rows for every symbol present in symbol_stats
    assert by["S2"]["evaluated"] == 1 and by["S2"]["passed"] == 1
    assert sent and sent[0].startswith("🌅") and "S1: 1 passed" in sent[0]


def test_scan_endpoint_auth_and_validation(monkeypatch):
    assert client.post("/scan/run").status_code == 401
    assert client.post("/scan/run?kind=evening", headers=AUTH).status_code == 422
    assert client.post("/scan/run?statuses=bogus", headers=AUTH).status_code == 422
    monkeypatch.setattr(scan_run, "run_morning", lambda **kw: scan_run.ScanResult(kind="morning", scan_date="2026-09-09", as_of=kw["now"].strftime("%H:%M")))
    r = client.post("/scan/run?kind=morning&as_of=08:00&date=2026-09-09&dry_run=true", headers=AUTH)
    assert r.status_code == 200 and r.json()["as_of"] == "08:00"
