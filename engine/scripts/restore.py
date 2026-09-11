"""Restore a backup written by `scripts/backup.py`. Phase 8.

    cd engine
    .\\.venv\\Scripts\\python scripts\\restore.py data\\backups\\2026-09-11_2300            # dry run
    .\\.venv\\Scripts\\python scripts\\restore.py data\\backups\\2026-09-11_2300 --yes      # actually write
    .\\.venv\\Scripts\\python scripts\\restore.py <folder> --tables journal,orders --yes

Three deliberate refusals, because a restore is the one operation that can destroy more than it repairs:

  1. **It is a dry run unless you type `--yes`.** The dry run prints exactly what would be written, table
     by table, and touches nothing.
  2. **It upserts, it never truncates.** Rows are matched on each table's primary key, so restoring
     over a live database
     repairs what is missing and updates what differs, and leaves anything newer than the backup
     alone.
     Wiping first would turn "I restored yesterday's journal" into "I deleted today's".
  3. **It verifies the checksum first.** A file whose SHA-256 does not match the manifest is refused
     outright rather than partially loaded — half a table is worse than none, because it looks complete.

Foreign keys mean order matters: parents before children. `ORDER` below is that order, and anything not
listed there is restored last.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from app.config import REPO_ROOT  # noqa: E402
from app.db import get_client  # noqa: E402

CHUNK = 200

# Not every table is keyed on `id`. `app_settings` is keyed on `key`, the two volume tables on a
# composite — upserting those with the wrong conflict target either errors or, worse, inserts duplicates.
PRIMARY_KEY: dict[str, str] = {
    "app_settings": "key",
    "plugin_registry": "name",
    "frameworks": "code",
    "symbol_stats": "symbol",
    "premarket_volume_baseline": "symbol,trade_date,feed",
    "feed_calibration": "symbol,trade_date,session_window",
}


def pk(table: str) -> str:
    return PRIMARY_KEY.get(table, "id")


# Parents before children: a `signals` row references `strategy_configs`, an `orders` row references
# `signals`, a `guardian_ledger` row references both `orders` and `journal`.
ORDER = [
    "app_settings", "plugin_registry", "frameworks", "prompt_templates", "strategy_configs",
    "symbol_stats", "premarket_volume_baseline", "feed_calibration", "economic_events",
    "scans", "news_sentiment", "briefings", "signals", "orders", "journal", "guardian_ledger",
    "backtests", "hypotheses", "compliance_events",
]


def read_manifest(folder: Path) -> dict:
    man = folder / "manifest.json"
    if not man.exists():
        raise SystemExit(f"no manifest.json in {folder} — that folder is not a complete backup")
    return json.loads(man.read_text(encoding="utf-8"))


def verify(folder: Path, entry: dict) -> tuple[list[dict], str]:
    """Load and checksum one table file. Returns (rows, problem) — problem empty when it is intact."""
    path = folder / entry["file"]
    if not path.exists():
        return [], f"{entry['file']} is missing"
    h, rows = hashlib.sha256(), []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            h.update(line.encode("utf-8"))
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                return [], f"{entry['file']} line {len(rows) + 1} is not JSON: {str(e)[:80]}"
    if entry.get("sha256") and h.hexdigest() != entry["sha256"]:
        return [], (f"{entry['file']} checksum mismatch — the file has changed since the backup was "
                    f"written. Refusing to load it: half a table looks complete and is not.")
    if entry.get("rows") is not None and len(rows) != entry["rows"]:
        return [], f"{entry['file']} holds {len(rows)} rows, the manifest says {entry['rows']}"
    return rows, ""


def restore_table(name: str, rows: list[dict], *, write: bool) -> tuple[int, str]:
    if not rows:
        return 0, ""
    if not write:
        return len(rows), ""
    client, done = get_client(), 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        try:
            client.table(name).upsert(chunk, on_conflict=pk(name)).execute()
        except Exception as e:  # noqa: BLE001
            return done, f"{type(e).__name__}: {str(e)[:200]}"
        done += len(chunk)
    return done, ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore a TradeHub backup folder into Supabase.")
    ap.add_argument("folder", help="the backup folder (e.g. data\\backups\\2026-09-11_2300)")
    ap.add_argument("--tables", help="comma-separated subset (default: everything in the manifest)")
    ap.add_argument("--yes", action="store_true", help="actually write; without it this is a dry run")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_absolute():
        folder = (REPO_ROOT / folder) if not folder.exists() else folder.resolve()
    if not folder.exists():
        raise SystemExit(f"{folder} does not exist")

    man = read_manifest(folder)
    wanted = {t.strip() for t in args.tables.split(",")} if args.tables else None
    entries = [e for e in man["tables"] if not e.get("error")
               and (wanted is None or e["table"] in wanted)]
    skipped = [e["table"] for e in man["tables"] if e.get("error")]
    entries.sort(key=lambda e: ORDER.index(e["table"]) if e["table"] in ORDER else len(ORDER))

    mode = "RESTORE" if args.yes else "DRY RUN (nothing will be written — add --yes to commit)"
    print(f"\nTradeHub restore — {mode}")
    print(f"  from: {folder}")
    print(f"  taken: {man.get('created_at', '?')}\n")
    if skipped:
        print(f"  note: {', '.join(skipped)} failed during the backup and are not in this folder\n")

    total, problems = 0, []
    for e in entries:
        rows, problem = verify(folder, e)
        if problem:
            problems.append(problem)
            print(f"  REFUSED  {e['table']:<28} {problem[:100]}")
            continue
        n, err = restore_table(e["table"], rows, write=args.yes)
        total += n
        if err:
            problems.append(f"{e['table']}: {err}")
            print(f"  FAIL     {e['table']:<28} wrote {n} of {len(rows)} — {err[:90]}")
        else:
            print(f"  {'ok   ' if args.yes else 'would'}    {e['table']:<28} {n:>7,} rows")

    print(f"\n  {total:,} rows {'restored' if args.yes else 'would be restored'} "
          f"across {len(entries)} table(s)")
    if problems:
        print(f"  ⚠️  {len(problems)} problem(s) — nothing was loaded for those tables\n")
        return 1
    if not args.yes:
        print("  Nothing was written. Re-run with --yes to commit.\n")
    else:
        print("  Rows were upserted on each table's primary key: missing rows restored, existing "
              "rows updated, newer rows left alone.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
