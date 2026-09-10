"""Every JSONB config that goes into the DB must validate against the pydantic contract,
and the generated SQL on disk must match the Python sources (single source of truth)."""

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import (
    AutomationMode,
    Condition,
    ExitStep,
    Provenance,
    Reentry,
    Risk,
    StrategyConfig,
    StrategyStatus,
)
from app.seed.frameworks import all_frameworks
from app.seed.settings import APP_SETTINGS
from app.seed.strategies import all_strategies

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
sys.path.insert(0, str(ENGINE_ROOT / "scripts"))
import gen_seed


def test_eight_strategies_validate():
    strategies = all_strategies()
    assert [s.code for s in strategies] == [f"S{i}" for i in range(1, 9)]
    for s in strategies:
        StrategyConfig.model_validate(s.model_dump())  # round-trip through JSON-able dict


def test_provenance_per_plan():
    by = {s.code: s for s in all_strategies()}
    assert by["S1"].provenance == Provenance.own and by["S2"].provenance == Provenance.own
    assert by["S3"].provenance == Provenance.derived
    for code in ("S4", "S5", "S6", "S8"):
        assert by[code].provenance == Provenance.pdf_canonical
    assert by["S7"].provenance == Provenance.interview_supplement


def test_automation_and_status_per_plan():
    by = {s.code: s for s in all_strategies()}
    assert by["S1"].automation_mode == AutomationMode.full_auto
    assert by["S3"].automation_mode == AutomationMode.full_auto
    for code in ("S4", "S5", "S6", "S7", "S8"):
        assert by[code].automation_mode == AutomationMode.semi_auto
    assert by["S2"].status == StrategyStatus.paper  # paper-ready immediately
    for code in ("S1", "S3", "S4", "S5", "S6", "S7", "S8"):
        assert by[code].status == StrategyStatus.backtesting


def test_s1_premarket_rules():
    s1 = {s.code: s for s in all_strategies()}["S1"]
    assert s1.risk.order_types == ["limit"]
    assert any(e.plugin == "synthetic_premarket_stop" and e.when == "premarket" for e in s1.exit_logic)
    oco = [e for e in s1.exit_logic if e.plugin == "bracket_oco"]
    assert oco and oco[0].when == "regular"
    relvol = [c for c in s1.entry_conditions.all if c.detector == "relative_volume_premarket"][0]
    assert relvol.value == 5.0 and relvol.params["feed"] == "sip" and relvol.params["baseline_days"] == 14  # decision #10


def test_s5_uses_pdf_threshold_85_not_interview_80():
    s5 = {s.code: s for s in all_strategies()}["S5"]
    slow = [c for c in s5.entry_conditions.all if c.detector == "stoch_embedded" and c.params["k"] == 60][0]
    assert slow.params["threshold"] == 85


def test_s7_reentry_and_rotation_exit():
    s7 = {s.code: s for s in all_strategies()}["S7"]
    assert s7.reentry.allowed and s7.reentry.max_entries == 3
    assert s7.exit_logic[0].plugin == "rotation_complete_exit"
    s6 = {s.code: s for s in all_strategies()}["S6"]
    assert not s6.reentry.allowed


def test_no_44_4_anywhere():
    for s in all_strategies():
        assert "[44, 4]" not in s.model_dump_json() and "44-4" not in s.model_dump_json()


def test_model_rejects_unknown_plugins():
    with pytest.raises(ValidationError):
        Condition(detector="magic_indicator")
    with pytest.raises(ValidationError):
        ExitStep(plugin="hope")


def test_model_rejects_premarket_bracket():
    base = all_strategies()[0].model_dump()
    base["exit_logic"] = [{"plugin": "synthetic_premarket_stop", "when": "premarket"}, {"plugin": "bracket_oco", "when": "always"}]
    with pytest.raises(ValidationError):
        StrategyConfig.model_validate(base)


def test_reentry_consistency():
    with pytest.raises(ValidationError):
        Reentry(allowed=False, max_entries=3)


def test_risk_bounds():
    with pytest.raises(ValidationError):
        Risk(risk_per_trade_pct=10)


def test_frameworks_and_settings():
    assert {f.code for f in all_frameworks()} == {"FA", "FB"}
    assert APP_SETTINGS["auto_approve"]["value"] is False
    assert APP_SETTINGS["kill_switch"]["value"] is False
    assert APP_SETTINGS["promotion_criteria"]["value"]["win_rate_is_gate"] is False
    assert APP_SETTINGS["promotion_criteria"]["value"]["min_profit_factor"] == 1.5


def test_templates_parse_and_count():
    templates = gen_seed.load_templates()
    assert [t.number for t in templates] == list(range(1, 8))
    assert {t.slug for t in templates} >= {"morning_trade_plan", "watchlist_briefing", "one_sentence_in_play_test"}
    for t in templates:
        assert t.output_schema["type"] == "object"
        assert "{{" in t.user_template


def test_strategy_template_slugs_exist():
    slugs = {t.slug for t in gen_seed.load_templates()}
    for s in all_strategies():
        if s.prompt_template_slug:
            assert s.prompt_template_slug in slugs, s.code


def test_library_files_exist():
    for s in all_strategies():
        assert (REPO_ROOT / s.library_file).exists(), s.library_file
    for f in all_frameworks():
        assert (REPO_ROOT / f.library_file).exists(), f.library_file


def test_generated_seed_sql_is_current():
    r = subprocess.run([sys.executable, str(ENGINE_ROOT / "scripts" / "gen_seed.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
