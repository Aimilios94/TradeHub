"""Frozen snapshots: a dated folder of Parquet files plus a manifest, registered in `datasets`.

A snapshot is never edited. New data means a new snapshot, and a backtest always names the one
it read. `verify_snapshot` re-hashes the files to prove a snapshot is still what was frozen.
"""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text

from app.data.store import read_live_table

MANIFEST_FILE = "manifest.json"
# table -> its date column. bars_daily is required; the others are frozen when recorded.
SNAPSHOT_TABLES: dict[str, str] = {
    "bars_daily": "date",
    "bars_intraday": "ts",
    "cot": "report_date",
}
NOTES = {
    "prices": (
        "open/high/low/close are Yahoo's prices as traded, adjusted for splits but not for "
        "dividends. adj_close is Yahoo's split- and dividend-adjusted close, stored as its own "
        "column. dividends and stock_splits hold the raw corporate actions."
    ),
    "cutoff": (
        "Only rows dated before cutoff_date are included, so a bar that was still forming on "
        "the freeze day cannot be in the snapshot."
    ),
    "futures": (
        "FUT:* series are Yahoo front-month continuous (=F) series with no roll adjustment. "
        "They are display-only and must never be used in a backtest."
    ),
    "intraday": "ts is the bar start in UTC. Regular session only (no pre- or post-market).",
    "cot": (
        "CFTC legacy futures-only report. report_date is the Tuesday the positions refer to; "
        "the report is published the following Friday. A backtest must lag it accordingly."
    ),
    "biases": "Survivorship: the universe is today's symbols.",
}

_INSERT_DATASET = text(
    "INSERT INTO datasets (version, path, content_hash, date_start, date_end, symbols, "
    "row_counts, manifest) VALUES (:version, :path, :content_hash, :date_start, :date_end, "
    ":symbols, :row_counts, :manifest)"
).bindparams(
    bindparam("symbols", type_=ARRAY(Text)),
    bindparam("row_counts", type_=JSONB),
    bindparam("manifest", type_=JSONB),
)


class SnapshotError(Exception):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_hash(file_hashes: dict[str, str]) -> str:
    """One hash for the whole snapshot: the per-file hashes, in file-name order."""
    lines = "\n".join(f"{name}:{file_hashes[name]}" for name in sorted(file_hashes))
    return hashlib.sha256(lines.encode()).hexdigest()


def _describe(path: Path, date_column: str) -> dict:
    """Row count, date range and symbols, read back with DuckDB from the file just written."""
    with duckdb.connect() as conn:
        source = "read_parquet(?)"
        rows, first, last = conn.execute(
            f"SELECT count(*), min({date_column})::date, max({date_column})::date FROM {source}",
            [str(path)],
        ).fetchone()
        symbols = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT ticker_id FROM {source} ORDER BY 1", [str(path)]
            ).fetchall()
        ]
    return {
        "file": path.name,
        "rows": rows,
        "date_start": first.isoformat(),
        "date_end": last.isoformat(),
        "symbols": symbols,
    }


def _before_cutoff(frame: pl.DataFrame, date_column: str, cutoff: date) -> pl.DataFrame:
    column = pl.col(date_column)
    if frame.schema[date_column] != pl.Date():
        column = column.dt.date()
    return frame.filter(column < cutoff)


def freeze_snapshot(
    engine: Engine,
    data_dir: Path,
    version: str | None = None,
    now: datetime | None = None,
    code_version: str = "unknown",
) -> dict:
    """Copy the live recordings into data/<version>/ and register it. Returns the manifest."""
    now = now or datetime.now(UTC)
    cutoff = now.date()
    version = version or f"v{cutoff.isoformat()}"
    target = data_dir / version
    with engine.connect() as conn:
        known = conn.execute(
            text("SELECT 1 FROM datasets WHERE version = :v"), {"v": version}
        ).scalar()
    if known or target.exists():
        raise SnapshotError(
            f"Snapshot {version} already exists. Snapshots are never edited: use a new version."
        )

    building = data_dir / f".building-{version}"
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)
    try:
        tables: dict[str, dict] = {}
        sources: set[str] = set()
        for table, date_column in SNAPSHOT_TABLES.items():
            frame = read_live_table(data_dir, table)
            if frame is not None:
                frame = _before_cutoff(frame, date_column, cutoff)
            if frame is None or frame.is_empty():
                if table == "bars_daily":
                    raise SnapshotError("No daily bars recorded yet: run the backfill first.")
                continue
            path = building / f"{table}.parquet"
            frame.write_parquet(path, compression="zstd")
            tables[table] = {**_describe(path, date_column), "sha256": file_sha256(path)}
            sources.update(frame["source_id"].unique().to_list())

        manifest = {
            "version": version,
            "created_at": now.isoformat(timespec="seconds"),
            "cutoff_date": cutoff.isoformat(),
            "content_hash": combined_hash({t["file"]: t["sha256"] for t in tables.values()}),
            "sources": sorted(sources),
            "tables": tables,
            "notes": NOTES,
            "code_version": code_version,
            "written_with": {"polars": pl.__version__, "duckdb": duckdb.__version__},
        }
        (building / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        building.rename(target)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise

    daily = tables["bars_daily"]
    try:
        with engine.begin() as conn:
            conn.execute(
                _INSERT_DATASET,
                {
                    "version": version,
                    "path": version,  # relative to the data directory
                    "content_hash": manifest["content_hash"],
                    "date_start": date.fromisoformat(daily["date_start"]),
                    "date_end": date.fromisoformat(daily["date_end"]),
                    "symbols": daily["symbols"],
                    "row_counts": {name: table["rows"] for name, table in tables.items()},
                    "manifest": manifest,
                },
            )
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # no unregistered snapshot is left behind
        raise
    for path in target.iterdir():
        path.chmod(0o444)
    return manifest


def read_manifest(data_dir: Path, version: str) -> dict:
    path = data_dir / version / MANIFEST_FILE
    if not path.exists():
        raise SnapshotError(f"Snapshot {version}: no manifest at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_snapshot(data_dir: Path, version: str, engine: Engine | None = None) -> list[str]:
    """Re-hash a snapshot. Returns the problems found; an empty list means it is intact."""
    manifest = read_manifest(data_dir, version)
    problems = []
    hashes = {}
    for name, table in manifest["tables"].items():
        path = data_dir / version / table["file"]
        if not path.exists():
            problems.append(f"{name}: file {table['file']} is missing")
            continue
        hashes[table["file"]] = file_sha256(path)
        if hashes[table["file"]] != table["sha256"]:
            problems.append(f"{name}: file hash differs from the manifest")
    if not problems and combined_hash(hashes) != manifest["content_hash"]:
        problems.append("content_hash in the manifest does not match the files")
    if engine is not None:
        with engine.connect() as conn:
            registered = conn.execute(
                text("SELECT content_hash FROM datasets WHERE version = :v"), {"v": version}
            ).scalar()
        if registered is None:
            problems.append("not registered in the datasets table")
        elif registered != manifest["content_hash"]:
            problems.append("content_hash differs from the datasets table")
    return problems


def open_snapshot(data_dir: Path, version: str) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB connection with one view per snapshot table (bars_daily, ...)."""
    manifest = read_manifest(data_dir, version)
    conn = duckdb.connect()
    for name, table in manifest["tables"].items():
        path = (data_dir / version / table["file"]).as_posix().replace("'", "''")
        conn.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{path}')")
    return conn
