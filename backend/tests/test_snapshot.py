"""Snapshot tooling: freeze, manifest, hash, registry row, and the never-edited rule."""

import json
from datetime import UTC, date, datetime

import polars as pl
import pytest
from sqlalchemy import text

from app.data.snapshot import (
    SnapshotError,
    freeze_snapshot,
    open_snapshot,
    read_manifest,
    verify_snapshot,
)
from app.data.store import TABLE_KEYS, live_path, merge_rows
from app.ingest.base import DAILY_SCHEMA, INTRADAY_SCHEMA

FREEZE_TIME = datetime(2026, 11, 11, 21, 30, tzinfo=UTC)


def daily(ticker_id: str, days: list[int]) -> pl.DataFrame:
    rows = [
        {
            "ticker_id": ticker_id,
            "date": date(2026, 11, day),
            "open": 100.0 + day,
            "high": 101.0 + day,
            "low": 99.0 + day,
            "close": 100.5 + day,
            "adj_close": 100.25 + day,
            "volume": 1000 * day,
            "dividends": 0.0,
            "stock_splits": 0.0,
            "source_id": "yahoo_finance",
        }
        for day in days
    ]
    return pl.DataFrame(rows, schema=DAILY_SCHEMA)


def intraday(ticker_id: str, stamps: list[datetime]) -> pl.DataFrame:
    rows = [
        {
            "ticker_id": ticker_id,
            "interval": "1m",
            "ts": stamp,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10,
            "source_id": "yahoo_finance",
        }
        for stamp in stamps
    ]
    return pl.DataFrame(rows, schema=INTRADAY_SCHEMA)


@pytest.fixture
def engine(migrated_engine):
    with migrated_engine.begin() as conn:
        conn.execute(text("DELETE FROM datasets"))
    return migrated_engine


@pytest.fixture
def data_dir(tmp_path):
    """A live area with two symbols; 11 Nov is the freeze day and must be left out."""
    merge_rows(
        live_path(tmp_path, "bars_daily", "ETF:SPY"),
        daily("ETF:SPY", [9, 10, 11]),
        TABLE_KEYS["bars_daily"],
    )
    merge_rows(
        live_path(tmp_path, "bars_daily", "ETF:GLD"),
        daily("ETF:GLD", [10, 11]),
        TABLE_KEYS["bars_daily"],
    )
    merge_rows(
        live_path(tmp_path, "bars_intraday", "ETF:SPY", "1m"),
        intraday(
            "ETF:SPY",
            [
                datetime(2026, 11, 10, 20, 59, tzinfo=UTC),
                datetime(2026, 11, 11, 14, 30, tzinfo=UTC),
            ],
        ),
        TABLE_KEYS["bars_intraday"],
    )
    return tmp_path


def test_freeze_writes_files_manifest_and_the_datasets_row(engine, data_dir):
    manifest = freeze_snapshot(engine, data_dir, now=FREEZE_TIME, code_version="abc1234")
    assert manifest["version"] == "v2026-11-11"
    assert manifest["cutoff_date"] == "2026-11-11"
    assert manifest["sources"] == ["yahoo_finance"]
    assert set(manifest["tables"]) == {"bars_daily", "bars_intraday"}  # no COT recorded: no table
    bars = manifest["tables"]["bars_daily"]
    assert (bars["rows"], bars["date_start"], bars["date_end"]) == (3, "2026-11-09", "2026-11-10")
    assert bars["symbols"] == ["ETF:GLD", "ETF:SPY"]
    assert manifest["tables"]["bars_intraday"]["rows"] == 1
    assert read_manifest(data_dir, "v2026-11-11") == manifest

    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM datasets")).mappings().one()
    assert row["version"] == row["path"] == "v2026-11-11"
    assert row["content_hash"] == manifest["content_hash"]
    assert (row["date_start"], row["date_end"]) == (date(2026, 11, 9), date(2026, 11, 10))
    assert row["symbols"] == ["ETF:GLD", "ETF:SPY"]
    assert row["row_counts"] == {"bars_daily": 3, "bars_intraday": 1}
    assert row["manifest"] == manifest
    assert verify_snapshot(data_dir, "v2026-11-11", engine) == []


def test_the_freeze_day_is_never_in_a_snapshot(engine, data_dir):
    freeze_snapshot(engine, data_dir, now=FREEZE_TIME)
    with open_snapshot(data_dir, "v2026-11-11") as conn:
        assert conn.execute("SELECT max(date) FROM bars_daily").fetchone()[0] == date(2026, 11, 10)
        assert conn.execute("SELECT count(*) FROM bars_intraday").fetchone()[0] == 1
        spy = conn.execute(
            "SELECT close, adj_close FROM bars_daily WHERE ticker_id = 'ETF:SPY' ORDER BY date"
        ).fetchall()
    assert spy == [(109.5, 109.25), (110.5, 110.25)]


def test_a_snapshot_is_never_edited_or_overwritten(engine, data_dir):
    first = freeze_snapshot(engine, data_dir, now=FREEZE_TIME)
    # New data arrives in the live area afterwards.
    merge_rows(
        live_path(data_dir, "bars_daily", "ETF:SPY"),
        daily("ETF:SPY", [6]),
        TABLE_KEYS["bars_daily"],
    )
    with pytest.raises(SnapshotError, match="already exists"):
        freeze_snapshot(engine, data_dir, now=FREEZE_TIME)
    assert verify_snapshot(data_dir, "v2026-11-11", engine) == []
    # The new data goes into a new snapshot with a different hash.
    second = freeze_snapshot(engine, data_dir, version="v2026-11-11b", now=FREEZE_TIME)
    assert second["tables"]["bars_daily"]["rows"] == 4
    assert second["content_hash"] != first["content_hash"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM datasets")).scalar() == 2


def test_the_same_data_always_gives_the_same_hash(engine, data_dir):
    first = freeze_snapshot(engine, data_dir, version="a", now=FREEZE_TIME)
    second = freeze_snapshot(engine, data_dir, version="b", now=FREEZE_TIME)
    assert first["content_hash"] == second["content_hash"]


def test_verify_detects_a_changed_file(engine, data_dir):
    freeze_snapshot(engine, data_dir, now=FREEZE_TIME)
    target = data_dir / "v2026-11-11" / "bars_daily.parquet"
    target.chmod(0o644)
    daily("ETF:SPY", [9]).write_parquet(target)
    assert verify_snapshot(data_dir, "v2026-11-11", engine) == [
        "bars_daily: file hash differs from the manifest"
    ]


def test_verify_detects_a_changed_manifest_or_a_missing_registration(engine, data_dir):
    manifest = freeze_snapshot(engine, data_dir, now=FREEZE_TIME)
    path = data_dir / "v2026-11-11" / "manifest.json"
    path.chmod(0o644)
    path.write_text(json.dumps(manifest | {"content_hash": "0" * 64}), encoding="utf-8")
    assert verify_snapshot(data_dir, "v2026-11-11", engine) == [
        "content_hash in the manifest does not match the files",
        "content_hash differs from the datasets table",
    ]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM datasets"))
    assert "not registered in the datasets table" in verify_snapshot(
        data_dir, "v2026-11-11", engine
    )


def test_freeze_without_daily_bars_fails_and_leaves_nothing_behind(engine, tmp_path):
    with pytest.raises(SnapshotError, match="No daily bars recorded"):
        freeze_snapshot(engine, tmp_path, now=FREEZE_TIME)
    assert list(tmp_path.iterdir()) == []
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM datasets")).scalar() == 0
