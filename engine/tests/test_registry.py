import pytest

from app import detectors, exits
from app.registry import ALL_PLUGINS, DETECTOR_NAMES, DETECTORS, EXIT_NAMES, EXITS, get


def test_counts_match_plan():
    assert len(DETECTORS) == 23  # plan §9 (21) + §27 (CVD, volume profile)
    assert len(EXITS) == 9
    assert len(ALL_PLUGINS) == 32


def test_numbers_are_sequential():
    assert [p["number"] for p in DETECTORS] == list(range(1, 24))
    assert [p["number"] for p in EXITS] == list(range(1, 10))


def test_names_unique_and_snake_case():
    names = [p["name"] for p in ALL_PLUGINS]
    assert len(names) == len(set(names))
    for n in names:
        assert n == n.lower() and " " not in n


def test_canonical_quad_bands_never_44_4():
    stoch = get("stoch_quad_extreme")
    assert stoch["params"]["bands"]["default"] == [[9, 3], [14, 3], [40, 4], [60, 10]]


def test_plan_constraints_encoded():
    assert "09:30" in get("bracket_oco")["description"]
    assert "< 1s" in get("synthetic_premarket_stop")["description"]
    assert "DISPLAY-ONLY" in get("cvd")["description"]
    assert get("relative_volume_premarket")["params"]["feed"]["default"] == "iex"


def test_register_rejects_unknown_names():
    with pytest.raises(KeyError):
        detectors.register("not_a_detector")
    with pytest.raises(KeyError):
        exits.register("not_an_exit")


def test_registered_but_unimplemented_raise_not_implemented():
    assert detectors.implemented() == []
    assert exits.implemented() == []
    with pytest.raises(NotImplementedError):
        detectors.get("ema")
    with pytest.raises(NotImplementedError):
        exits.get("bracket_oco")
    assert "ema" in DETECTOR_NAMES and "bracket_oco" in EXIT_NAMES
