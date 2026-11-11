"""Checks on the seed files themselves. No database needed."""

import re

import pytest

from app.seeds import SEED_TABLES, read_seed

CANONICAL_ID = re.compile(r"^(EQ|ETF|FUT|IDX|SPOT|CRYPTO):[A-Z0-9.]+$")
SECTOR_ETFS = {"XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC"}


@pytest.fixture(scope="module")
def seeds(seeds_dir):
    return {table: read_seed(seeds_dir, table) for table in SEED_TABLES}


def test_taxonomy_is_the_seven_locked_tags(seeds):
    assert [row["id"] for row in seeds["driver_taxonomy"]] == [
        "geopolitical",
        "monetary",
        "macro_risk",
        "supply_flows",
        "positioning",
        "dollar",
        "liquidity_flush",
    ]


def test_only_liquidity_flush_has_no_minus_one(seeds):
    without = [r["id"] for r in seeds["driver_taxonomy"] if r["minus_one_meaning"] is None]
    assert without == ["liquidity_flush"]


def test_ticker_ids_are_canonical(seeds):
    for row in seeds["tickers"]:
        assert CANONICAL_ID.match(row["id"]), row["id"]
        assert row["id"] == f"{row['asset_class']}:{row['symbol']}"


def test_ticker_ids_are_unique(seeds):
    ids = [row["id"] for row in seeds["tickers"]]
    assert len(ids) == len(set(ids))


def test_display_aliases_map_to_exactly_one_ticker(seeds):
    """'Gold' must resolve to one canonical ID, never two."""
    seen: dict[str, str] = {}
    for row in seeds["tickers"]:
        for alias in row["aliases"]:
            assert alias == alias.lower(), f"alias '{alias}' must be lowercase"
            assert alias not in seen, f"alias '{alias}' on both {seen.get(alias)} and {row['id']}"
            seen[alias] = row["id"]
    assert seen["gold"] == "FUT:GC"


def test_aliases_never_collide_with_a_real_symbol(seeds):
    """Names like GOLD, SIL or OIL are real tickers; an alias must not shadow a seeded symbol."""
    symbols = {row["symbol"].lower() for row in seeds["tickers"]}
    for row in seeds["tickers"]:
        for alias in row["aliases"]:
            owner_symbol = row["symbol"].lower()
            assert alias == owner_symbol or alias not in symbols, alias


def test_all_sector_etfs_are_seeded(seeds):
    etfs = {row["symbol"] for row in seeds["tickers"] if row["asset_class"] == "ETF"}
    assert etfs >= SECTOR_ETFS


def test_watchlist_is_the_agreed_seed(seeds):
    watch = [row["ticker_id"] for row in seeds["watchlist"]]
    assert watch[:5] == ["ETF:SPY", "ETF:QQQ", "ETF:GLD", "EQ:TSLA", "EQ:NVDA"]
    assert {t.split(":")[1] for t in watch[5:]} == SECTOR_ETFS
    assert set(watch) <= {row["id"] for row in seeds["tickers"]}


def test_sensitivities_reference_known_drivers_and_tickers(seeds):
    drivers = {row["id"] for row in seeds["driver_taxonomy"]}
    tickers = {row["id"] for row in seeds["tickers"]}
    for row in seeds["driver_sensitivities"]:
        assert row["driver_id"] in drivers
        assert row["ticker_id"] in tickers
        assert row["expected_direction"] in (-1, 0, 1)
        assert row["horizon"] in ("intraday", "days", "weeks")


def test_sources_never_hold_a_key_only_the_variable_name(seeds):
    for row in seeds["sources"]:
        ref = row["credentials_ref"]
        assert ref is None or re.fullmatch(r"[A-Z0-9_]+_API_KEY", ref), row["id"]


def test_source_statuses_are_from_the_menu(seeds):
    for row in seeds["sources"]:
        assert row["status"] in ("working", "fallback", "pending", "untested", "dropped")
        if row["status"] == "working":
            assert row["last_verified_on"], f"{row['id']} is 'working' without a verified date"
