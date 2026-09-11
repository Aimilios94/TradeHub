"""Supabase → local JSONL, plus an index of the parquet cache (plan Phase 8).

Lives in the engine rather than only in a script because n8n has to be able to schedule it, and this
project's rule is thin n8n / fat Python (decision #1). `scripts/backup.py` is a CLI over these same
functions, so a manual backup and the nightly one cannot drift apart.

What is written, per run, into `data/backups/YYYY-MM-DD_HHMM/`:
  <table>.jsonl   one JSON object per row — diffable, greppable, restorable table by table
  manifest.json   row counts, byte sizes and a SHA-256 per file, plus the parquet index

What is deliberately NOT written:
  * `.env` — it holds every API key, and a backup folder is the easiest thing in the world to copy
    somewhere careless.
  * the parquet bars themselves — ~18 MB of history Alpaca hands back for free. The manifest records
    what was cached, which is all a rebuild needs.

Rows are read in pages: PostgREST caps a response at 1,000 rows, so a `journal` past that would
otherwise be backed up silently truncated — the worst kind of backup, because it looks fine.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

PAGE = 1000

# Ordering pages needs a column that exists. Most tables are keyed on `id`; these are not, and an
# unordered `range()` walk can return one row twice and miss another while the table is changing.
SORT_KEY: dict[str, str] = {
    "app_settings": "key", "plugin_registry": "name", "frameworks": "code", "symbol_stats": "symbol",
    "premarket_volume_baseline": "symbol", "feed_calibration": "symbol",
}

DEFAULT_TABLES = [
    "app_settings", "strategy_configs", "frameworks", "prompt_templates", "plugin_registry",
    "scans", "signals", "orders", "journal", "news_sentiment", "backtests", "guardian_ledger",
    "compliance_events", "hypotheses", "economic_events", "premarket_volume_baseline",
    "feed_calibration", "symbol_stats", "briefings",
]

NOTE = ("Rows only. The .env file (API keys) is NOT here by design — keep your own copy of the keys "
        "where you keep passwords. The parquet bars are indexed, not copied: they are free to "
        "re-download from Alpaca.")


def config() -> dict:
    from app.marketdata import repo as md_repo

    return md_repo.fetch_setting("backup", {}) or {}


def root(cfg: dict | None = None) -> Path:
    from app.config import REPO_ROOT

    p = Path((cfg or config()).get("path") or "data/backups")
    return p if p.is_absolute() else (REPO_ROOT / p)


def dump_table(name: str, out: Path) -> dict:
    """Every row, paged. Returns a manifest entry — or an `error` entry, never a silent short file."""
    from app.db import get_client

    client = get_client()
    rows: list[dict] = []
    offset = 0
    while True:
        try:
            page = (client.table(name).select("*").order(SORT_KEY.get(name, "id"))
                    .range(offset, offset + PAGE - 1).execute().data or [])
        except Exception as e:  # noqa: BLE001 — a table that cannot be read is named in the manifest
            return {"table": name, "rows": None, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        rows.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE

    path = out / f"{name}.jsonl"
    h = hashlib.sha256()
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            line = json.dumps(r, ensure_ascii=False, default=str)
            f.write(line + "\n")
            h.update(line.encode("utf-8"))
    return {"table": name, "rows": len(rows), "file": path.name, "bytes": path.stat().st_size,
            "sha256": h.hexdigest()}


def parquet_index() -> dict:
    """What the bar cache holds — names and sizes, not the bytes. Enough to know what to re-fetch."""
    from app.config import get_settings

    store = get_settings().parquet_store_path
    if not store.exists():
        return {"path": str(store), "exists": False, "files": 0, "bytes": 0, "entries": []}
    files = list(store.rglob("*.parquet"))
    return {"path": str(store), "exists": True, "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "entries": sorted(str(p.relative_to(store)).replace("\\", "/") for p in files)[:20_000]}


def prune(keep_daily: int, base: Path) -> list[str]:
    """Keep the newest `keep_daily` folders. The count is a setting, not a constant — old backups are
    the ones nobody needs until the day they do."""
    if not base.exists():
        return []
    stamps = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    removed = []
    for p in stamps[keep_daily:]:
        shutil.rmtree(p, ignore_errors=True)
        removed.append(p.name)
    return removed


def run_backup(*, tables: list[str] | None = None, out: Path | None = None, do_prune: bool = True,
               notify: bool = False) -> dict:
    """Take one backup. Returns the manifest plus a summary. Never raises on a single bad table."""
    cfg = config()
    base = root(cfg)
    names = tables or list(cfg.get("tables") or DEFAULT_TABLES)
    # Local time in the folder name on purpose: the owner reads these, and "2026-09-11_2300" should
    # mean 23:00 the way his clock shows it, not 04:00 the next day in UTC.
    folder = out or (base / dt.datetime.now().strftime("%Y-%m-%d_%H%M"))  # noqa: DTZ005
    folder.mkdir(parents=True, exist_ok=True)

    entries = [dump_table(n, folder) for n in names]
    failed = [e["table"] for e in entries if e.get("error")]
    pq = parquet_index()
    manifest = {"created_at": dt.datetime.now(dt.UTC).isoformat(), "tables": entries,
                "parquet": pq, "note": NOTE}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    removed = prune(int(cfg.get("keep_daily", 14)), base) if do_prune else []
    rows = sum(e.get("rows") or 0 for e in entries)
    summary = {"folder": str(folder), "tables": len(entries), "failed": failed, "rows": rows,
               "bytes": sum(e.get("bytes") or 0 for e in entries), "pruned": removed,
               "parquet_files": pq["files"], "complete": not failed, "note": NOTE}

    if failed:
        # A backup missing three tables is not a backup; it must be as loud as any other failure.
        from app.ops import failures

        failures.failure("backup_failed",
                         f"backup {folder.name} is INCOMPLETE — {', '.join(failed)} could not be read",
                         key="backup", severity="error", detail=summary, notify=True)
    elif notify:
        from app.notify import send_telegram

        send_telegram(f"💾 Backup {folder.name}: {rows:,} rows across {len(entries)} tables, "
                      f"{summary['bytes'] / 1_048_576:.1f} MB"
                      + (f" · pruned {len(removed)} old" if removed else ""))
    return {"summary": summary, "manifest": manifest}


def list_backups() -> list[dict]:
    base = root()
    if not base.exists():
        return []
    out = []
    for p in sorted((x for x in base.iterdir() if x.is_dir()), reverse=True):
        man = p / "manifest.json"
        entry = {"name": p.name, "path": str(p),
                 "bytes": sum(f.stat().st_size for f in p.glob("*") if f.is_file())}
        if man.exists():
            m = json.loads(man.read_text(encoding="utf-8"))
            entry |= {"created_at": m.get("created_at"),
                      "rows": sum(t.get("rows") or 0 for t in m.get("tables", [])),
                      "tables": len(m.get("tables", [])),
                      "failed": [t["table"] for t in m.get("tables", []) if t.get("error")]}
        else:
            entry |= {"created_at": None, "rows": None, "tables": None,
                      "failed": ["(no manifest — the run did not finish)"]}
        out.append(entry)
    return out
