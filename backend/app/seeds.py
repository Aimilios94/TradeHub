"""Load the config seeds (taxonomy, tickers, sources, sensitivities, watchlist) into PostgreSQL.

Idempotent: every table is upserted on its natural key, so running it again changes nothing
unless a seed file changed. Runtime columns (source health, watchlist added by the user) are
never overwritten.
"""

import json
from pathlib import Path

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import Text

# table -> (conflict key columns, columns that must never be overwritten on re-seed)
SEED_TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "driver_taxonomy": (("id",), ()),
    "tickers": (("id",), ()),
    "sources": (("id",), ("enabled",)),
    "driver_sensitivities": (("driver_id", "ticker_id", "horizon"), ("status",)),
    "watchlist": (("ticker_id",), ("position",)),
}


def read_seed(seeds_dir: Path, table: str) -> list[dict]:
    return json.loads((seeds_dir / f"{table}.json").read_text(encoding="utf-8"))


def _upsert_sql(table: str, columns: list[str], keys: tuple[str, ...], keep: tuple[str, ...]):
    updates = [c for c in columns if c not in keys and c not in keep]
    action = (
        "DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in updates)
        if updates
        else "DO NOTHING"
    )
    statement = text(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        f"ON CONFLICT ({', '.join(keys)}) {action}"
    )
    return statement


def _bind_types(statement, rows: list[dict]):
    """Tell the driver which params are text[] or jsonb, from the seed values themselves."""
    from sqlalchemy import bindparam

    params = []
    for column in rows[0]:
        values = [row[column] for row in rows if row[column] is not None]
        if values and isinstance(values[0], list):
            params.append(bindparam(column, type_=ARRAY(Text)))
        elif values and isinstance(values[0], dict):
            params.append(bindparam(column, type_=JSONB))
    return statement.bindparams(*params) if params else statement


def load_seeds(engine: Engine, seeds_dir: Path) -> dict[str, int]:
    """Upsert every seed file. Returns the number of seed rows per table."""
    loaded: dict[str, int] = {}
    with engine.begin() as conn:
        for table, (keys, keep) in SEED_TABLES.items():
            rows = read_seed(seeds_dir, table)
            columns = list(rows[0])
            if any(list(row) != columns for row in rows):
                raise ValueError(f"{table}.json: every row must have the same keys in order")
            statement = _bind_types(_upsert_sql(table, columns, keys, keep), rows)
            conn.execute(statement, rows)
            loaded[table] = len(rows)
    return loaded
