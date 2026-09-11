"""Back up Supabase (and index the parquet cache) to a local folder. Phase 8.

    cd engine
    .\\.venv\\Scripts\\python scripts\\backup.py                 # a full backup into data/backups/<stamp>
    .\\.venv\\Scripts\\python scripts\\backup.py --tables journal,orders
    .\\.venv\\Scripts\\python scripts\\backup.py --list          # what backups exist, and how big

A thin front end over `app/ops/backup.py`, which is the same code the nightly n8n job calls through
`POST /ops/backup`. Doing it twice would eventually mean doing it two different ways.

Restoring is a separate script on purpose: `scripts/restore.py`, CLI only, never a webhook.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from app.ops import backup as backup_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Back up Supabase tables and index the parquet cache.")
    ap.add_argument("--tables", help="comma-separated subset (default: every table in app_settings.backup)")
    ap.add_argument("--out", help="destination folder (default: data/backups/<timestamp>)")
    ap.add_argument("--list", action="store_true", help="list existing backups and exit")
    ap.add_argument("--no-prune", action="store_true", help="keep every old backup")
    args = ap.parse_args()

    if args.list:
        rows = backup_mod.list_backups()
        if not rows:
            print(f"\nno backups yet at {backup_mod.root()}\n")
            return 0
        print(f"\nBackups in {backup_mod.root()}\n")
        for r in rows:
            rows_txt = f"{r['rows']:>7,} rows" if r["rows"] is not None else "  no manifest"
            bad = f"  ⚠️ failed: {', '.join(r['failed'])}" if r.get("failed") else ""
            print(f"  {r['name']}  {r['bytes'] / 1_048_576:6.1f} MB  {rows_txt}  {r['tables'] or '?'} tables{bad}")
        print()
        return 0

    tables = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None
    print("\nTradeHub backup — this can take a minute on a full database\n")
    out = backup_mod.run_backup(tables=tables, out=Path(args.out) if args.out else None,
                                do_prune=not args.no_prune, notify=False)
    s, man = out["summary"], out["manifest"]

    for e in man["tables"]:
        if e.get("error"):
            print(f"  FAIL  {e['table']:<28} {e['error'][:90]}")
        else:
            print(f"  ok    {e['table']:<28} {e['rows']:>7,} rows  {e['bytes'] / 1024:8.1f} KB")

    pq = man["parquet"]
    print(f"\n  → {s['folder']}")
    print(f"  parquet cache: {pq['files']} file(s), {pq['bytes'] / 1_048_576:.1f} MB — indexed, not copied")
    print(f"  {s['rows']:,} rows in {s['tables'] - len(s['failed'])} of {s['tables']} tables")
    if s["pruned"]:
        print(f"  pruned {len(s['pruned'])} old backup(s): {', '.join(s['pruned'][:5])}"
              + (" ..." if len(s["pruned"]) > 5 else ""))

    if s["failed"]:
        print(f"\n  ⚠️  {len(s['failed'])} table(s) failed: {', '.join(s['failed'])} — this backup is "
              f"INCOMPLETE\n")
        return 1
    print(f"\n  restore with:  .\\.venv\\Scripts\\python scripts\\restore.py \"{s['folder']}\" --yes\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
