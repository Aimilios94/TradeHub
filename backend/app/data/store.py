"""The live recording area: Parquet files under data/live/ that the recorder appends to.

This is the only place that changes over time. Backtests never read it; they read a frozen
snapshot (see snapshot.py), which is a copy taken from here.

Layout:
    data/live/bars_daily/ETF_SPY.parquet
    data/live/bars_intraday/1m/ETF_SPY.parquet
    data/live/cot/FUT_GC.parquet
"""

import os
from pathlib import Path

import polars as pl

LIVE_DIR = "live"
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "bars_daily": ("ticker_id", "date"),
    "bars_intraday": ("ticker_id", "interval", "ts"),
    "cot": ("ticker_id", "report_date"),
}


def ticker_file(ticker_id: str) -> str:
    """ETF:SPY -> ETF_SPY.parquet (a colon is not a valid file-name character on Windows)."""
    return ticker_id.replace(":", "_") + ".parquet"


def live_path(data_dir: Path, table: str, ticker_id: str, interval: str | None = None) -> Path:
    folder = data_dir / LIVE_DIR / table
    if interval:
        folder = folder / interval
    return folder / ticker_file(ticker_id)


def merge_rows(path: Path, new: pl.DataFrame, keys: tuple[str, ...]) -> dict[str, int]:
    """Add rows to a file. On the same key the newer row wins; rows only in the file are kept.

    The file is written beside the target and swapped in, so a crash cannot leave it half-written.
    """
    before = 0
    combined = new
    if path.exists():
        old = pl.read_parquet(path)
        before = old.height
        if old.schema != new.schema:
            raise ValueError(f"{path.name}: stored columns differ from the incoming ones")
        combined = pl.concat([old, new])
    combined = combined.unique(list(keys), keep="last", maintain_order=True).sort(list(keys))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    combined.write_parquet(temporary, compression="zstd")
    os.replace(temporary, path)
    return {"rows": combined.height, "added": combined.height - before}


def last_value(path: Path, column: str):
    """Largest value of a column in a live file, or None when nothing is recorded yet."""
    if not path.exists():
        return None
    return pl.scan_parquet(path).select(pl.col(column).max()).collect().item()


def read_live_table(data_dir: Path, table: str) -> pl.DataFrame | None:
    """Every live file of a table as one frame, or None when the table has no files."""
    files = sorted((data_dir / LIVE_DIR / table).rglob("*.parquet"))
    if not files:
        return None
    return pl.concat([pl.read_parquet(file) for file in files]).sort(list(TABLE_KEYS[table]))
