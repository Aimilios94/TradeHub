"""Freeze, verify or list Parquet snapshots.

Usage (inside the api container):
    python -m scripts.snapshot freeze [--version v2026-11-11]
    python -m scripts.snapshot verify v2026-11-11
    python -m scripts.snapshot list

A snapshot is never edited. Freezing twice under one version is refused.
"""

import argparse
import json
import os
import sys

from sqlalchemy import text

from app.config import get_settings
from app.data.snapshot import SnapshotError, freeze_snapshot, verify_snapshot
from app.db import get_engine

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--version", help="default: v<today's UTC date>")
    commands.add_parser("verify").add_argument("version")
    commands.add_parser("list")
    args = parser.parse_args()

    data_dir, engine = get_settings().data_dir, get_engine()
    try:
        if args.command == "freeze":
            manifest = freeze_snapshot(
                engine,
                data_dir,
                version=args.version,
                code_version=os.environ.get("TRADEHUB_CODE_VERSION", "unknown"),
            )
            summary = {key: manifest[key] for key in ("version", "cutoff_date", "content_hash")}
            summary["tables"] = {
                name: {key: table[key] for key in ("rows", "date_start", "date_end")}
                | {"symbols": len(table["symbols"])}
                for name, table in manifest["tables"].items()
            }
            print(json.dumps(summary, indent=2))
        elif args.command == "verify":
            problems = verify_snapshot(data_dir, args.version, engine)
            print("\n".join(problems) if problems else f"{args.version}: intact")
            sys.exit(1 if problems else 0)
        else:
            query = text(
                "SELECT version, date_start, date_end, row_counts, content_hash, created_at "
                "FROM datasets ORDER BY created_at"
            )
            with engine.connect() as conn:
                for row in conn.execute(query).mappings():
                    print(dict(row))
    except SnapshotError as exc:
        sys.exit(f"error: {exc}")
