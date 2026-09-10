"""Generate the n8n CLI import files from the tracked workflow JSON + .env.

    python n8n/build_import.py            # write the files
    python n8n/build_import.py --check    # exit 1 if what's on disk is stale

Source of truth is `n8n/workflows/*.json` (tracked) plus ENGINE_API_TOKEN from `.env`.
Output goes to `n8n/data/import/` (gitignored — it contains the token) which is bind-mounted into the
container at /home/node/.n8n/import.

Why one file per workflow: `n8n import:workflow` creates every tag it sees without de-duplicating within
a batch, so two workflows sharing a tag (02 and 03 both carry `phase-2`) abort the whole import with
`SQLITE_CONSTRAINT: UNIQUE constraint failed: tag_entity.name`. Importing the files one at a time reuses
the tag that already exists and works. Verified 2026-09-10.

The credential is written in n8n's *decrypted* import shape: `n8n import:credentials` encrypts it with the
instance's own key on the way in, so this file never needs to know that key.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / "n8n" / "workflows"
OUT_DIR = REPO_ROOT / "n8n" / "data" / "import"
CONTAINER_DIR = "/home/node/.n8n/import"
CREDENTIAL_NAME = "TradeHub Engine token"
CREDENTIAL_ID = "tradehub-engine-token"
TELEGRAM_CREDENTIAL_NAME = "TradeHub Telegram bot"
TELEGRAM_CREDENTIAL_ID = "tradehub-telegram-bot"


def env_value(key: str) -> str:
    env = REPO_ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    raise SystemExit(f"{key} is empty or missing in .env")


def engine_token() -> str:
    return env_value("ENGINE_API_TOKEN")


def build() -> tuple[dict, list[tuple[str, list]]]:
    credentials = [
        {
            "id": CREDENTIAL_ID,
            "name": CREDENTIAL_NAME,
            "type": "httpHeaderAuth",
            "data": {"name": "Authorization", "value": f"Bearer {engine_token()}"},
        },
        {
            # the Telegram Trigger node needs the bot token to receive Deploy / Cancel button taps
            "id": TELEGRAM_CREDENTIAL_ID,
            "name": TELEGRAM_CREDENTIAL_NAME,
            "type": "telegramApi",
            "data": {"accessToken": env_value("TELEGRAM_BOT_TOKEN"), "baseUrl": "https://api.telegram.org"},
        },
    ]
    workflows = []
    for path in sorted(WORKFLOW_DIR.glob("*.json")):
        wf = json.loads(path.read_text(encoding="utf-8"))
        # The tracked files carry no id (it is deployment state, not design). The CLI requires one, and a
        # STABLE id is what makes a re-import update the existing workflow instead of adding a duplicate.
        wf.setdefault("id", f"tradehub-wf-{path.stem.split('_')[0]}")
        # every HTTP node points at the shared credential by name; n8n resolves the id on import
        for node in wf.get("nodes", []):
            cred = node.get("credentials", {}).get("httpHeaderAuth")
            if cred is not None:
                cred["id"] = CREDENTIAL_ID
                cred["name"] = CREDENTIAL_NAME
            tg = node.get("credentials", {}).get("telegramApi")
            if tg is not None:
                tg["id"] = TELEGRAM_CREDENTIAL_ID
                tg["name"] = TELEGRAM_CREDENTIAL_NAME
        workflows.append((path.stem, [wf]))
    return credentials, workflows


def render(credentials: list, workflows: list[tuple[str, list]]) -> dict[str, str]:
    files = {"credentials.json": json.dumps(credentials, indent=2, ensure_ascii=False) + "\n"}
    for stem, payload in workflows:
        files[f"workflows/{stem}.json"] = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    return files


def main() -> int:
    check = "--check" in sys.argv
    credentials, workflows = build()
    files = render(credentials, workflows)

    if check:
        stale = [name for name, body in files.items()
                 if not (OUT_DIR / name).exists() or (OUT_DIR / name).read_text(encoding="utf-8") != body]
        if stale:
            print("stale or missing import files:", ", ".join(stale))
            print("run: python n8n/build_import.py")
            return 1
        print("import files are current")
        return 0

    # a stale flat workflows.json from before this script existed would import duplicates
    legacy = OUT_DIR / "workflows.json"
    if legacy.exists():
        legacy.unlink()
    if (OUT_DIR / "split").exists():
        shutil.rmtree(OUT_DIR / "split")

    (OUT_DIR / "workflows").mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (OUT_DIR / name).write_text(body, encoding="utf-8")

    print(f"wrote {len(files)} file(s) to {OUT_DIR}")
    print("\nImport them with:")
    print(f"  docker exec tradehub-n8n n8n import:credentials --input={CONTAINER_DIR}/credentials.json")
    for stem, _ in workflows:
        print(f"  docker exec tradehub-n8n n8n import:workflow    --input={CONTAINER_DIR}/workflows/{stem}.json")
    print("  docker restart tradehub-n8n")
    print("\n(Git Bash mangles container paths: prefix each command with MSYS_NO_PATHCONV=1.)")
    print("Imported workflows arrive INACTIVE — activate them at http://localhost:5678.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
