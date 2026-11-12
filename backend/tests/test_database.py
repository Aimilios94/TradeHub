"""Migrations, seeds and fixed-menu constraints, against a real PostgreSQL."""

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.seeds import SEED_TABLES, load_seeds, read_seed
from tests.conftest import alembic_config

V1_TABLES = {
    "sources",
    "news_items",
    "news_sentiment",
    "driver_taxonomy",
    "driver_sensitivities",
    "tagged_events",
    "tickers",
    "market_context",
    "economic_events",
    "daily_scores",
    "watchlist",
    "strategy_configs",
    "datasets",
    "backtests",
    "llm_traces",
    "eval_runs",
    "social_sentiment",  # added in 0004
}


def _tables(engine) -> set[str]:
    query = text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    with engine.connect() as conn:
        return set(conn.execute(query).scalars()) - {"alembic_version"}


def _counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in SEED_TABLES}


def test_migration_creates_exactly_the_v1_tables(migrated_engine):
    assert _tables(migrated_engine) == V1_TABLES


def test_seeds_load_and_are_idempotent(migrated_engine, seeds_dir):
    expected = {table: len(read_seed(seeds_dir, table)) for table in SEED_TABLES}
    assert load_seeds(migrated_engine, seeds_dir) == expected
    assert _counts(migrated_engine) == expected
    load_seeds(migrated_engine, seeds_dir)
    assert _counts(migrated_engine) == expected


def test_reseeding_keeps_a_source_the_owner_switched_off(migrated_engine, seeds_dir):
    load_seeds(migrated_engine, seeds_dir)
    with migrated_engine.begin() as conn:
        conn.execute(text("UPDATE sources SET enabled = false WHERE id = 'stocktwits'"))
    load_seeds(migrated_engine, seeds_dir)
    with migrated_engine.begin() as conn:
        enabled = conn.execute(text("SELECT enabled FROM sources WHERE id = 'stocktwits'")).scalar()
        conn.execute(text("UPDATE sources SET enabled = true WHERE id = 'stocktwits'"))
    assert enabled is False


@pytest.fixture
def news_item_id(migrated_engine, seeds_dir):
    load_seeds(migrated_engine, seeds_dir)
    with migrated_engine.begin() as conn:
        item_id = conn.execute(
            text(
                "INSERT INTO news_items (source_id, title, url, dedupe_hash, is_demo) "
                "VALUES ('finnhub', 'test item', 'https://example.com/a', 'hash-menu-test', true) "
                "ON CONFLICT (dedupe_hash) DO UPDATE SET title = EXCLUDED.title RETURNING id"
            )
        ).scalar()
    return item_id


SENTIMENT_INSERT = text(
    "INSERT INTO news_sentiment (news_item_id, ticker_id, catalyst_type, sentiment_score, "
    "driver_direction, one_sentence_reason, prompt_version, model) "
    "VALUES (:item, 'EQ:TSLA', :catalyst, :score, :direction, 'r', :version, 'test-model')"
)


@pytest.mark.parametrize(
    ("catalyst", "score", "direction"),
    [
        ("rumour", 0.2, 0),  # catalyst type not on the menu
        ("earnings", 1.2, 0),  # sentiment score outside -1..+1
        ("earnings", 0.2, 2),  # driver direction not in -1/0/+1
    ],
)
def test_condenser_values_outside_the_menus_cannot_be_stored(
    migrated_engine, news_item_id, catalyst, score, direction
):
    row = {
        "item": news_item_id,
        "catalyst": catalyst,
        "score": score,
        "direction": direction,
        "version": "bad",
    }
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(SENTIMENT_INSERT, row)


def test_a_valid_condenser_row_is_stored(migrated_engine, news_item_id):
    row = {
        "item": news_item_id,
        "catalyst": "earnings",
        "score": -0.4,
        "direction": 0,
        "version": "ok",
    }
    with migrated_engine.begin() as conn:
        conn.execute(text("DELETE FROM news_sentiment WHERE prompt_version = 'ok'"))
        conn.execute(SENTIMENT_INSERT, row)


def test_liquidity_flush_cannot_be_tagged_minus_one(migrated_engine, news_item_id):
    insert = text(
        "INSERT INTO tagged_events (news_item_id, driver_id, direction, scheduled, tagged_by, "
        "event_time) VALUES (:item, 'liquidity_flush', -1, false, 'code', now())"
    )
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(insert, {"item": news_item_id})


def test_ticker_id_must_match_class_and_symbol(migrated_engine):
    insert = text(
        "INSERT INTO tickers (id, asset_class, symbol, display_name) "
        "VALUES ('EQ:GOLD', 'ETF', 'GOLD', 'mismatch')"
    )
    with pytest.raises(IntegrityError), migrated_engine.begin() as conn:
        conn.execute(insert)


def test_downgrade_then_upgrade_is_clean(empty_db_url, migrated_engine):
    """Runs last in this module: leaves the schema migrated for other tests."""
    config = alembic_config(empty_db_url)
    command.downgrade(config, "base")
    fresh = create_engine(empty_db_url)
    assert _tables(fresh) == set()
    command.upgrade(config, "head")
    assert _tables(fresh) == V1_TABLES
    fresh.dispose()
