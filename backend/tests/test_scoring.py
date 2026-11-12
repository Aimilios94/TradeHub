"""Unit tests for the code half of the pipeline: dedupe, tagger rules, importance, composite.

Pure functions, no database and no network. Headlines here are invented.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.scoring import composite, importance
from app.scoring.dedupe import (
    DedupeItem,
    assign_duplicates,
    normalize_title,
    same_story,
    title_tokens,
)
from app.tagger.rules import DRIVER_IDS, Rule, RuleSet, Tagger, default_tagger, load_rule_set

T0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def item(item_id: int, title: str, hours: float = 0) -> DedupeItem:
    return DedupeItem(item_id, title, T0 + timedelta(hours=hours))


# --- dedupe ---------------------------------------------------------------------------------


def test_normalize_title_ignores_case_punctuation_and_accents():
    assert normalize_title("  Tesla’s Q3: Résumé — “Beat”!  ") == "tesla s q3 resume beat"


def test_same_title_with_other_punctuation_is_the_same_story():
    a, b = (
        title_tokens("Tesla beats estimates, shares rise"),
        title_tokens("Tesla Beats Estimates; Shares Rise!"),
    )
    assert same_story(a, b)


def test_a_title_with_words_added_is_the_same_story():
    a = title_tokens("Futures rise before the bell as chipmakers gain on strong demand")
    b = title_tokens("Update: Futures rise before the bell as chipmakers gain on strong demand")
    assert same_story(a, b)


def test_a_template_with_another_company_is_a_different_story():
    a = title_tokens("Alpha stock: should you buy it or sell it now?")
    b = title_tokens("Beta stock: should you buy it or sell it now?")
    assert not same_story(a, b)


def test_a_swapped_weekday_is_a_different_story():
    a = title_tokens("What the fund sold on Thursday: bank shares lead the outflows again")
    b = title_tokens("What the fund sold on Friday: bank shares lead the outflows again")
    assert not same_story(a, b)


def test_short_titles_must_match_exactly():
    assert not same_story(title_tokens("Markets wrap"), title_tokens("Markets wrap today"))
    assert same_story(title_tokens("Markets wrap"), title_tokens("MARKETS WRAP"))


def test_empty_titles_never_match():
    assert not same_story(title_tokens("!!!"), title_tokens("???"))


def test_the_first_stored_item_stays_and_later_copies_point_to_it():
    new = [item(3, "Alpha raises outlook"), item(1, "Alpha raises outlook"), item(2, "Other news")]
    assert assign_duplicates(new, []) == {1: None, 2: None, 3: 1}


def test_a_copy_outside_the_window_is_not_a_duplicate():
    new = [item(1, "Sector little changed"), item(2, "Sector little changed", hours=73)]
    assert assign_duplicates(new, []) == {1: None, 2: None}


def test_new_items_are_matched_against_items_already_shown():
    shown = [item(10, "Alpha raises outlook")]
    new = [item(11, "Alpha raises outlook", hours=5), item(12, "Beta cuts outlook", hours=5)]
    assert assign_duplicates(new, shown) == {11: 10, 12: None}


def test_the_most_similar_shown_item_wins():
    shown = [
        item(1, "Futures rise before the bell as chipmakers gain on demand"),
        item(2, "Update: Futures rise before the bell as chipmakers gain on demand"),
    ]
    new = [item(3, "UPDATE - futures rise before the bell as chipmakers gain on demand")]
    assert assign_duplicates(new, shown) == {3: 2}


# --- tagger ---------------------------------------------------------------------------------


def tags_of(title: str) -> dict[str, int]:
    return {tag.driver: tag.direction for tag in default_tagger().tag(title)}


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fed signals slower cuts", {"monetary": 1}),  # the plan's own example
        ("Fed delivers quarter-point rate cut", {"monetary": -1}),
        ("Stocks fall on surging Treasury yields", {"monetary": 1}),
        ("Hot CPI sparks Fed hike fears", {"monetary": 1}),
        ("Powell speaks at conference", {"monetary": 0}),
        ("Escalation in Middle East conflict lifts oil", {"geopolitical": 1}),
        ("Oil slides on easing Middle East tensions", {"geopolitical": -1}),
        ("Lawmakers push to end the war in the region", {"geopolitical": -1}),
        ("New tariffs on imported cars announced", {"geopolitical": 1}),
        ("Regional bank under pressure after bank run", {"macro_risk": 1}),
        ("Debt-ceiling deal reached", {"macro_risk": -1}),
        ("Crude inventories draw more than expected", {"supply_flows": 1}),
        ("OPEC+ to raise output next month", {"supply_flows": -1}),
        ("Gold COT record long", {"positioning": 1}),
        ("Chipmaker hits most oversold level in a year", {"positioning": -1}),
        ("Dollar rises to a two-month high", {"dollar": 1}),
        ("Gold gains as dollar slips", {"dollar": -1}),
        ("Funds face margin calls in cross-asset sell-off", {"liquidity_flush": 1}),
    ],
)
def test_rules_tag_the_expected_driver_and_direction(title, expected):
    assert tags_of(title) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Company launches a billion-dollar buyback",
        "Dollar General beats estimates",
        "Chipmakers in a price war over memory",
        "Fed up with fees, customers switch banks",
        "Oil falls as demand outlook weakens",
        "Nvidia unveils new data center chips",
    ],
)
def test_lookalike_wording_is_not_tagged(title):
    assert tags_of(title) == {}


def test_negated_stress_wording_is_not_stress_rising():
    assert tags_of("Record valuation isn't a recession signal, strategist says") == {
        "macro_risk": 0
    }


def test_rules_pointing_both_ways_give_direction_zero():
    assert tags_of("Dollar rises then dollar falls in choppy trade") == {"dollar": 0}


def test_scheduled_flag_marks_events_known_in_advance():
    by_driver = {
        tag.driver: tag for tag in default_tagger().tag("Futures flat ahead of CPI report")
    }
    assert by_driver["monetary"].scheduled is True
    surprise = default_tagger().tag("Fed delivers surprise rate hike")[0]
    assert surprise.scheduled is False and surprise.direction == 1


def test_a_tag_records_the_rules_that_fired():
    tag = default_tagger().tag("Hot CPI sparks Fed hike fears")[0]
    assert "monetary_hawkish" in tag.rules and "monetary_topic" in tag.rules


def test_rule_file_uses_only_the_seven_locked_tags(seeds_dir):
    import json

    taxonomy = {row["id"] for row in json.loads((seeds_dir / "driver_taxonomy.json").read_text())}
    assert set(DRIVER_IDS) == taxonomy
    assert {rule.driver for rule in load_rule_set().rules} <= taxonomy


def test_a_rule_outside_the_menu_is_rejected():
    with pytest.raises(ValueError):
        Rule(id="x", driver="weather", direction=1, patterns=["storm"])
    with pytest.raises(ValueError):
        Rule(id="x", driver="monetary", direction=2, patterns=["hike"])
    with pytest.raises(ValueError, match="no -1"):
        Rule(id="x", driver="liquidity_flush", direction=-1, patterns=["calm"])


def test_tagger_is_deterministic_and_ordered_by_taxonomy():
    rule_set = RuleSet(
        version="t",
        rules=[
            Rule(id="d", driver="dollar", direction=1, patterns=["dollar up"]),
            Rule(id="g", driver="geopolitical", direction=1, patterns=["invasion"]),
        ],
    )
    tags = Tagger(rule_set).tag("Invasion sends dollar up")
    assert [tag.driver for tag in tags] == ["geopolitical", "dollar"]


# --- importance -----------------------------------------------------------------------------


def score(title: str, **kwargs) -> importance.Importance:
    defaults = {"copies": 1, "watchlist_tickers": 1, "ticker_names": [], "tag_directions": []}
    return importance.score_item(title, **{**defaults, **kwargs})


def test_importance_stays_between_0_and_100():
    low = score("3 Stocks to Buy and Hold Forever")
    high = score(
        "Alpha beats earnings estimates as Fed cuts rates",
        copies=9,
        watchlist_tickers=9,
        ticker_names=["Alpha"],
        tag_directions=[1],
    )
    assert low.score == 0.0
    assert high.score == 100.0


def test_a_company_event_outranks_a_listicle_and_a_plain_note():
    event = score("Alpha beats earnings estimates", ticker_names=["ALPH", "alpha"])
    note = score("Alpha shares little changed", ticker_names=["ALPH", "alpha"])
    listicle = score("5 reasons to buy Alpha", ticker_names=["ALPH", "alpha"])
    assert event.score > note.score > listicle.score


def test_more_copies_of_a_story_raise_its_score():
    assert score("Alpha news", copies=3).score > score("Alpha news", copies=1).score
    assert score("Alpha news", copies=4).score == score("Alpha news", copies=40).score


def test_a_tag_with_a_direction_counts_more_than_a_bare_topic():
    directed, topic, none = (
        score("x", tag_directions=d).components["driver"] for d in ([1], [0], [])
    )
    assert (directed, topic, none) == (1.0, 0.5, 0.0)


def test_symbols_match_in_capitals_only_and_names_in_any_case():
    assert importance.mentions_any("Why NOW is rallying", ["NOW"])
    assert not importance.mentions_any("Why now is the time", ["NOW"])
    assert importance.mentions_any("TESLA recalls cars", ["TSLA", "tesla"])
    assert not importance.mentions_any("Teslas everywhere", ["TSLA", "tesla"])


def test_importance_components_explain_the_score():
    result = score("Alpha to acquire Beta", copies=2, ticker_names=["alpha"])
    assert result.components["catalyst_hint"] == "m_and_a"
    assert result.components["version"] == importance.VERSION
    expected = 100 * (0.30 * 1.0 + 0.25 * (1 / 3) + 0.20 * 1.0)
    assert result.score == round(expected, 2)


# --- composite score ------------------------------------------------------------------------


def test_no_input_gives_no_score():
    assert composite.composite_score(news_mean=None, news_items=0, driver_mean=None) is None


def test_news_only_score_scales_with_volume():
    one = composite.composite_score(news_mean=0.8, news_items=1, driver_mean=None)
    five = composite.composite_score(news_mean=0.8, news_items=5, driver_mean=None)
    many = composite.composite_score(news_mean=0.8, news_items=50, driver_mean=None)
    assert (one.score, five.score, many.score) == (16.0, 80.0, 80.0)


def test_news_and_driver_are_weighted_60_40():
    result = composite.composite_score(
        news_mean=1.0, news_items=5, driver_mean=-1.0, driver_events=5
    )
    assert result.score == 20.0
    assert result.components["driver"] == -1.0


def test_driver_only_score_scales_with_tagged_headlines():
    def score(events):
        return composite.composite_score(
            news_mean=None, news_items=0, driver_mean=-1.0, driver_events=events
        )

    assert [score(n).score for n in (1, 2, 5, 40)] == [-20.0, -40.0, -100.0, -100.0]
    assert score(1).components["driver"] == -0.2
    assert score(1).components["driver_mean"] == -1.0
    assert score(1).components["version"] == "composite-v2"
    assert score(0) is None  # a mean without a tagged headline is no input


def test_one_tag_moves_a_ticker_with_news_only_a_little():
    result = composite.composite_score(
        news_mean=0.5, news_items=5, driver_mean=-1.0, driver_events=1
    )
    assert result.score == 22.0  # 100 * (0.6 * 0.5 + 0.4 * -0.2)


def test_social_is_additive_and_ignored_below_the_minimum():
    base = composite.composite_score(news_mean=0.5, news_items=5, driver_mean=None)
    few = composite.composite_score(news_mean=0.5, news_items=5, driver_mean=None, bullish=9)
    enough = composite.composite_score(
        news_mean=0.5, news_items=5, driver_mean=None, bullish=9, bearish=3
    )
    assert few.score == base.score == 50.0
    assert few.components["social"] is None
    assert enough.score == 60.0  # +20 * (9 - 3) / 12


def test_score_is_limited_to_plus_and_minus_100():
    top = composite.composite_score(
        news_mean=1.0, news_items=9, driver_mean=1.0, driver_events=9, bullish=30
    )
    bottom = composite.composite_score(
        news_mean=-1.0, news_items=9, driver_mean=-1.0, driver_events=9, bearish=30
    )
    assert (top.score, bottom.score) == (100.0, -100.0)
