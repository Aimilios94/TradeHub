r"""Apply a migration file (or a single statement) to Supabase from this PC.

    .\.venv\Scripts\python scripts\apply_sql.py ..\supabase\migrations\0006_backtest_spec.sql
    .\.venv\Scripts\python scripts\apply_sql.py --grep "plugin_registry" ..\supabase\migrations\0002_seed.sql

The connection goes through the SESSION POOLER host in SUPABASE_DB_URL — the direct `db.<ref>` host is
IPv6-only and does not resolve from here (docs/DECISIONS.md #14).

`--grep` exists because re-running the whole generated seed would overwrite every `app_settings` row,
including ones the owner has edited. The rule from CLAUDE.md is to apply only the statements that
changed; this makes that a command instead of a copy-paste into the SQL editor.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402

# Statements are separated by ";" — but a semicolon inside a quoted body is not a separator, and this
# seed is full of them ("IEX-only volume); exact on SIP history"). Both quoting styles are masked out
# before the split: $json$…$json$ / $txt$…$txt$ dollar-quoted blocks, and ordinary '…' strings with
# their doubled-quote escapes. Splitting naively produces SQL that fails halfway through a migration,
# which is the worst possible moment to discover the parser was wrong.
DOLLAR = re.compile(r"\$(json|txt)\$.*?\$\1\$", re.DOTALL)
SINGLE = re.compile(r"'(?:[^']|'')*'", re.DOTALL)


def split_statements(sql: str) -> list[str]:
    holes: list[str] = []

    def stash(m: re.Match) -> str:
        holes.append(m.group(0))
        return f"\x00{len(holes) - 1}\x00"

    masked = SINGLE.sub(stash, DOLLAR.sub(stash, sql))
    masked = re.sub(r"--[^\n]*", "", masked)
    parts = [p.strip() for p in masked.split(";")]
    out = []
    for p in parts:
        if not p:
            continue
        for i, h in enumerate(holes):
            p = p.replace(f"\x00{i}\x00", h)
        out.append(p)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="path to a .sql file")
    ap.add_argument("--grep", help="apply only the statements containing this text")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, change nothing")
    args = ap.parse_args(argv)

    url = get_settings().supabase_db_url
    if not url:
        print("SUPABASE_DB_URL is not set in .env — nothing was applied.")
        return 2

    sql = Path(args.path).read_text(encoding="utf-8")
    statements = split_statements(sql)
    if args.grep:
        statements = [s for s in statements if args.grep in s]
    if not statements:
        print("nothing matched — nothing was applied.")
        return 1

    print(f"{len(statements)} statement(s) from {args.path}"
          + (f" matching {args.grep!r}" if args.grep else ""))
    for s in statements:
        print("  " + " ".join(s.split())[:140])
    if args.dry_run:
        print("\n--dry-run: nothing was applied.")
        return 0

    import psycopg

    applied = 0
    with psycopg.connect(url, autocommit=False) as conn, conn.cursor() as cur:
        for s in statements:
            if s.lower() in ("begin", "commit"):
                continue
            cur.execute(s)
            applied += 1
        conn.commit()
    print(f"\napplied {applied} statement(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
