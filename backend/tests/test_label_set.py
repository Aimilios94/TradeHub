"""The labeling tool: a reproducible sample, strict input parsing, and an export with no text."""

import json

from scripts import label_set


def pool(size: int = 300, macro_every: int = 4) -> list[dict]:
    return [{"id": n, "is_macro": n % macro_every == 0} for n in range(1, size + 1)]


def test_the_sample_is_reproducible_and_has_both_strata():
    first, second = label_set.draw(pool()), label_set.draw(pool())
    assert [(row["id"], stratum) for row, stratum in first] == [
        (row["id"], stratum) for row, stratum in second
    ]
    ids = [row["id"] for row, _ in first]
    assert len(ids) == len(set(ids)) == label_set.RANDOM_PART + label_set.MACRO_PART == 100
    macro = [row for row, stratum in first if stratum == "macro"]
    assert len(macro) == label_set.MACRO_PART and all(row["is_macro"] for row in macro)


def test_driver_input_is_parsed_strictly_from_the_fixed_menu():
    assert label_set._parse_drivers("") == []
    assert label_set._parse_drivers("0") == []
    assert label_set._parse_drivers("2+ 6-") == [
        {"driver": "monetary", "direction": 1},
        {"driver": "dollar", "direction": -1},
    ]
    assert label_set._parse_drivers("10") == [{"driver": "geopolitical", "direction": 0}]
    for bad in ("8+", "2", "monetary+", "2 +", "7-"):  # 7- : liquidity flush has no -1
        assert label_set._parse_drivers(bad) is None


def test_the_export_holds_ids_and_labels_but_no_news_text(tmp_path, capsys):
    working = {
        "seed": label_set.SEED,
        "items": [
            {
                "id": 7,
                "dedupe_hash": "abc",
                "stratum": "macro",
                "title": "SECRET HEADLINE",
                "excerpt": "SECRET EXCERPT",
                "url": "https://example.com/secret",
                "label": {"sentiment_direction": 1, "catalyst_type": "macro", "tickers": ["SPY"]},
            },
            {
                "id": 8,
                "dedupe_hash": "def",
                "stratum": "random",
                "title": "UNLABELED",
                "label": None,
            },
        ],
    }
    path = tmp_path / "labels.json"
    label_set.save(path, working)
    label_set.export(path)
    printed = capsys.readouterr().out
    exported = json.loads(printed)
    assert [item["id"] for item in exported["items"]] == [7]  # unlabeled items are left out
    assert set(exported["items"][0]) == {"id", "dedupe_hash", "stratum", "label"}
    assert "SECRET" not in printed and "example.com" not in printed
