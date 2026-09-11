"""First-run setup (plan §33). Walks through keys, risk defaults, active strategies and a self-test.

    cd engine
    .\\.venv\\Scripts\\python scripts\\onboarding.py              # the full walkthrough
    .\\.venv\\Scripts\\python scripts\\onboarding.py --check      # read-only: what is set, what is missing
    .\\.venv\\Scripts\\python scripts\\onboarding.py --step risk  # just one step (keys|risk|strategies|selftest)

Safe to re-run. Every step shows the current value and keeps it if you press Enter, so this is also the
way to change one risk limit without hunting through the database.

Two things it will not do:

  * **It will not switch you to live.** Live trading is refused in `app/broker/orders.assert_paper_only`
    until Phase 8's paper month is finished and a separate, owner-approved commit lifts it. A setup
    wizard is exactly the wrong place for that decision, so the live option here only explains itself.
  * **It will not print a secret back to you.** Keys are shown as `sk-...4f2a` — enough to recognise,
    not enough to leak into a screenshot.

Secrets go into the repo-root `.env`, which is gitignored and never leaves this machine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import shutil
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from app.config import REPO_ROOT, get_settings  # noqa: E402

ENV_PATH = REPO_ROOT / ".env"

# (env var, what it is for, is it required to run at all)
KEYS: list[tuple[str, str, bool]] = [
    ("SUPABASE_URL", "the database — everything is stored there", True),
    ("SUPABASE_SERVICE_ROLE_KEY", "the database's server-side key", True),
    ("ALPACA_PAPER_KEY_ID", "your paper broker account + market data", True),
    ("ALPACA_PAPER_SECRET_KEY", "your paper broker account + market data", True),
    ("ENGINE_API_TOKEN", "the password n8n uses to call the engine", True),
    ("TELEGRAM_BOT_TOKEN", "alerts and the Deploy button", True),
    ("TELEGRAM_CHAT_ID", "which chat the bot talks to (yours, and only yours)", True),
    ("OPENROUTER_API_KEY", "the big AI that writes trade plans (pay-per-use)", True),
    ("FINNHUB_API_KEY", "company news for the catalyst check (free)", False),
    ("FMP_API_KEY", "fundamentals and the screener fallback (free)", False),
    ("FRED_API_KEY", "official macro figures (free)", False),
    ("EIA_API_KEY", "energy data (free)", False),
    ("REDDIT_CLIENT_ID", "retail chatter on small caps (free, optional)", False),
    ("REDDIT_CLIENT_SECRET", "retail chatter on small caps (free, optional)", False),
]

RISK_FIELDS: list[tuple[str, str, str]] = [
    ("risk_per_trade_pct_default", "Risk per trade, % of equity", "0.5 means a losing trade costs 0.5% of the account"),
    ("daily_loss_limit_r", "Daily loss limit, in R", "2.0 means the day stops after losing two full risk units"),
    ("max_concurrent_positions", "Max positions open at once", "3 is the seeded default"),
    ("spread_ceiling_pct", "Widest spread you will pay, %", "2.0 — above this the Guardian blocks the order"),
]


def mask(v: str) -> str:
    if not v:
        return ""
    return v if len(v) <= 8 else f"{v[:4]}...{v[-4:]}"


def read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    out = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def write_env(updates: dict[str, str]) -> None:
    """Rewrite only the lines that changed, keeping comments, order and everything else intact.

    A setup wizard that regenerates .env from a template is a setup wizard that one day deletes the key
    it did not know about. It also keeps one timestamped copy before writing.
    """
    if not updates:
        return
    if ENV_PATH.exists():
        shutil.copy2(ENV_PATH, ENV_PATH.parent / f".env.bak-{dt.datetime.now():%Y%m%d-%H%M%S}")
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = ["# TradeHub secrets — never commit this file"]

    seen = set()
    for i, line in enumerate(lines):
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in updates:
                lines[i] = f"{k}={updates[k]}"
                seen.add(k)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  wrote {len(updates)} value(s) to {ENV_PATH} (a .bak copy was kept)")


def ask(prompt: str, current: str = "", *, secret: bool = False) -> str:
    shown = f" [{mask(current) if secret else current}]" if current else " [not set]"
    reader = getpass.getpass if secret else input
    raw = reader(f"  {prompt}{shown}: ").strip()
    return raw or current


# --------------------------------------------------------------------------- steps

def step_keys(check_only: bool = False) -> None:
    print("\n=== 1. Keys ===")
    print("  Press Enter to keep what is already there. Typed keys are not echoed to the screen.\n")
    env, updates = read_env(), {}
    for name, why, required in KEYS:
        cur = env.get(name, "")
        tag = "REQUIRED" if required else "optional"
        if check_only:
            state = "set" if cur else ("MISSING" if required else "not set")
            print(f"  {state:<8} {name:<28} {tag:<8} {why}")
            continue
        print(f"  {name} — {why} ({tag})")
        v = ask(name, cur, secret=True)
        if v != cur:
            updates[name] = v
    if not check_only:
        write_env(updates)


def step_mode() -> None:
    print("\n=== 2. Paper or live ===")
    s = get_settings()
    print(f"  Currently: {'PAPER' if s.paper_mode else 'LIVE'} ({s.alpaca_paper_base_url})")
    print("  Live trading is refused in the code itself until the paper month is finished — see")
    print("  docs/DECISIONS.md #37. This step is here to say so, not to offer a switch.")
    print("  Paper it is.")


def step_risk(check_only: bool = False) -> None:
    print("\n=== 3. Risk defaults ===")
    from app.db import get_client

    try:
        row = (get_client().table("app_settings").select("value").eq("key", "guardian_defaults")
               .single().execute())
        cfg = dict(row.data["value"])
    except Exception as e:  # noqa: BLE001
        print(f"  could not read guardian_defaults: {str(e)[:140]}")
        return

    print("  These are ceilings. A strategy may tighten any of them; nothing may loosen them.\n")
    changed = {}
    for key, label, note in RISK_FIELDS:
        cur = cfg.get(key)
        if check_only:
            print(f"  {label:<34} {cur}")
            continue
        print(f"  {label}  —  {note}")
        raw = input(f"    [{cur}]: ").strip()
        if raw:
            try:
                changed[key] = int(raw) if key == "max_concurrent_positions" else float(raw)
            except ValueError:
                print(f"    '{raw}' is not a number — keeping {cur}")
    if check_only or not changed:
        if not check_only:
            print("\n  nothing changed")
        return
    cfg.update(changed)
    get_client().table("app_settings").update({"value": cfg}).eq("key", "guardian_defaults").execute()
    print(f"\n  updated: {', '.join(f'{k}={v}' for k, v in changed.items())}")


def step_strategies(check_only: bool = False) -> None:
    print("\n=== 4. Which strategies start active ===")
    from app.db import get_client

    try:
        rows = (get_client().table("strategy_configs").select("id,code,name,status,mode")
                .order("code").execute().data or [])
    except Exception as e:  # noqa: BLE001
        print(f"  could not read strategy_configs: {str(e)[:140]}")
        return

    for r in rows:
        print(f"  {r['code']:<4} {r['status']:<12} {r['mode']:<10} {r['name']}")
    if check_only:
        return

    print("\n  A strategy only reaches `paper` through /backtest/promote with a passing backtest —")
    print("  this step sets which ones the SCANNER looks at each morning (draft/backtesting are both")
    print("  scanned; they just cannot place orders). Type codes to scan, comma separated, or Enter to")
    print("  keep what is there.")
    raw = input("    codes: ").strip().upper()
    if not raw:
        print("  unchanged")
        return
    want = {c.strip() for c in raw.split(",") if c.strip()}
    unknown = want - {r["code"] for r in rows}
    if unknown:
        print(f"  unknown code(s): {', '.join(sorted(unknown))} — nothing changed")
        return
    client = get_client()
    for r in rows:
        if r["status"] in ("paper", "live"):
            continue                      # promoted strategies are not demoted by a setup wizard
        target = "backtesting" if r["code"] in want else "draft"
        if r["status"] != target:
            client.table("strategy_configs").update({"status": target}).eq("id", r["id"]).execute()
            print(f"  {r['code']}: {r['status']} → {target}")


def step_selftest() -> None:
    print("\n=== 5. Connectivity self-test ===")
    from app.ops import preflight as ops_preflight

    res = ops_preflight.run()
    icon = {"green": "ok  ", "yellow": "warn", "red": "FAIL", "unknown": "????", "not_applicable": "n/a "}
    for c in res.checks:
        print(f"  {icon.get(c.status, '?')}  {c.name:<16} {c.detail[:96]}")

    from app.notify import send_telegram

    ok = send_telegram("🔧 TradeHub setup — if you can read this, alerts are working.")
    print(f"  {'ok  ' if ok else 'FAIL'}  telegram         "
          + ("test message sent — check your phone" if ok else
             "could not send. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, and that you have "
             "messaged the bot at least once."))

    print(f"\n  Overall: {res.status.upper()} — "
          + ("the engine is in a fit state to trade" if res.can_enter else
             f"entries would be blocked right now ({res.reason()[:140]})"))
    if not res.can_enter:
        print("  Outside market hours a red data_feed check is expected; re-run during the session.")


def main() -> int:
    ap = argparse.ArgumentParser(description="TradeHub first-run setup (plan §33).")
    ap.add_argument("--check", action="store_true", help="read-only report: what is set, what is missing")
    ap.add_argument("--step", choices=["keys", "mode", "risk", "strategies", "selftest"],
                    help="run only one step")
    args = ap.parse_args()

    print("\n" + "=" * 74)
    print("  TradeHub setup — paper only, nothing here can place an order")
    print("=" * 74)

    steps = {"keys": lambda: step_keys(args.check), "mode": step_mode,
             "risk": lambda: step_risk(args.check), "strategies": lambda: step_strategies(args.check),
             "selftest": step_selftest}
    order = [args.step] if args.step else ["keys", "mode", "risk", "strategies", "selftest"]
    if args.check and not args.step:
        order = ["keys", "mode", "risk", "strategies"]        # --check never sends a Telegram message

    for name in order:
        try:
            steps[name]()
        except KeyboardInterrupt:
            print("\n\n  stopped — nothing further was changed\n")
            return 1
        except Exception as e:  # noqa: BLE001 — one broken step must not hide the others
            print(f"\n  step '{name}' failed: {type(e).__name__}: {str(e)[:200]}")

    print("\n" + "-" * 74)
    print("  Next: docker compose up -d --build engine")
    print("        .\\.venv\\Scripts\\python scripts\\check_env.py     (per-service credential probe)")
    print("        POST /ops/drill                                 (prove the Guardian can block)")
    print("        POST /ops/paper-month/start                     (start the four-week clock)")
    print("-" * 74 + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
