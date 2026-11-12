"""CI guard: `.env.example` holds names and placeholders only, and local-only files stay out of git.

    uv run python -m scripts.check_env_example        (from backend/)

Rules:
    1. A variable whose name says it is a secret (KEY, SECRET, TOKEN, PASSWORD, DSN) is empty.
    2. Any other variable may carry a value only if its name is in ALLOWED_VALUES. A new setting
       with a default is added there on purpose, by a person who looked at it.
    3. `.env`, the handoff files, `data/` and `backups/` are not tracked by git.

Values are never printed: a failure names the variable and the line, nothing else.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "DSN")
ALLOWED_VALUES = frozenset(
    {
        "OPENROUTER_MODEL_BETTER",
        "OPENROUTER_MODEL_CHEAP",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "LLM_MONTHLY_BUDGET_EUR",
        "CONDENSER_DAILY_CAP",
        "CONDENSER_RUN_CAP",
        "CONDENSER_LOOKBACK_DAYS",
        "POSTGRES_USER",
        "POSTGRES_DB",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "SEARXNG_BASE_URL",
        "API_PORT",
        "SEARXNG_PORT",
        "N8N_PORT",
    }
)
LOCAL_ONLY = (
    ".env",
    "HANDOFF.md",
    "next-agent-prompt.md",
    "previous-chat-catchup.md",
    "data",
    "backups",
)


def is_secret_name(name: str) -> bool:
    return any(word in name.upper() for word in SECRET_WORDS)


def check_env_text(content: str) -> list[str]:
    """Problems found in the text of an env example file. Never includes a value."""
    problems = []
    for number, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not name:
            problems.append(f"line {number}: not a NAME=value line")
        elif not value:
            continue
        elif is_secret_name(name):
            problems.append(f"line {number}: {name} is a secret and must be empty")
        elif name not in ALLOWED_VALUES:
            problems.append(
                f"line {number}: {name} has a value but is not in ALLOWED_VALUES "
                "(scripts/check_env_example.py)"
            )
    return problems


def tracked_local_files(root: Path = REPO_ROOT) -> list[str]:
    """Local-only paths that git tracks. Empty is the only good answer."""
    result = subprocess.run(
        ["git", "ls-files", "--", *LOCAL_ONLY],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    problems = check_env_text((REPO_ROOT / ".env.example").read_text(encoding="utf-8"))
    problems += [f"{path} is tracked by git and must not be" for path in tracked_local_files()]
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(".env.example holds placeholders only; no local-only file is tracked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
