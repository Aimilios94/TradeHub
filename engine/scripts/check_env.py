"""Credential readiness check — which keys are filled in .env and whether each one actually connects.

Usage (from /engine):  python scripts/check_env.py [--no-network]
Prints one line per credential: OK / MISSING / FAIL (with the real error). Never prints secret values.
Read-only: every probe is a harmless GET/auth call. Safe to run any time.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx

ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from app.config import get_settings  # noqa: E402

s = get_settings()
NETWORK = "--no-network" not in sys.argv
TIMEOUT = 15.0


def probe(name: str, needed: str, present: bool, fn=None):
    if not present:
        print(f"  MISSING  {name:<22} needed for: {needed}")
        return
    if not NETWORK or fn is None:
        print(f"  SET      {name:<22} (not tested)")
        return
    try:
        detail = fn() or ""
        print(f"  OK       {name:<22} {detail}")
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace("\n", " ")[:140]
        print(f"  FAIL     {name:<22} {msg}")


def get(url, **kw):
    r = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, **kw)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- probes

def supabase():
    r = get(f"{s.supabase_url}/rest/v1/app_settings?select=key&limit=1",
            headers={"apikey": s.supabase_service_role_key, "Authorization": f"Bearer {s.supabase_service_role_key}"})
    rows = r.json()
    return f"reachable; app_settings rows visible: {len(rows)} (0 means migrations not applied yet)"


def alpaca():
    r = get(f"{s.alpaca_paper_base_url}/v2/account",
            headers={"APCA-API-KEY-ID": s.alpaca_paper_key_id, "APCA-API-SECRET-KEY": s.alpaca_paper_secret_key})
    a = r.json()
    return f"paper account {a.get('status')} · buying_power={a.get('buying_power')} · currency={a.get('currency')}"


def alpaca_data():
    r = get(f"{s.alpaca_data_base_url}/v2/stocks/SPY/quotes/latest?feed={s.alpaca_data_feed}",
            headers={"APCA-API-KEY-ID": s.alpaca_paper_key_id, "APCA-API-SECRET-KEY": s.alpaca_paper_secret_key})
    q = r.json().get("quote", {})
    return f"SPY quote via {s.alpaca_data_feed}: bid={q.get('bp')} ask={q.get('ap')}"


def openrouter():
    r = get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {s.openrouter_api_key}"})
    d = r.json().get("data", {})
    return f"credit_used={d.get('usage')} limit={d.get('limit')}"


def telegram():
    r = get(f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe")
    return f"bot @{r.json()['result']['username']}" + ("" if s.telegram_chat_id else "  (TELEGRAM_CHAT_ID still empty)")


def finnhub():
    r = get(f"https://finnhub.io/api/v1/quote?symbol=SPY&token={s.finnhub_api_key}")
    return f"SPY last={r.json().get('c')}"


def fmp():
    r = get(f"https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={s.fmp_api_key}")
    d = r.json()
    return f"AAPL profile ok" if d else "empty response (key may be limited)"


def fred():
    r = get(f"https://api.stlouisfed.org/fred/series?series_id=FEDFUNDS&api_key={s.fred_api_key}&file_type=json")
    return r.json()["seriess"][0]["title"]


def eia():
    r = get(f"https://api.eia.gov/v2/petroleum/pri/spt/data/?api_key={s.eia_api_key}&frequency=daily&data[0]=value&length=1")
    return "petroleum spot price endpoint ok"


def reddit():
    auth = base64.b64encode(f"{s.reddit_client_id}:{s.reddit_client_secret}".encode()).decode()
    r = httpx.post("https://www.reddit.com/api/v1/access_token", data={"grant_type": "client_credentials"},
                   headers={"Authorization": f"Basic {auth}", "User-Agent": s.reddit_user_agent}, timeout=TIMEOUT)
    r.raise_for_status()
    return f"token ok, scope={r.json().get('scope')}"


def ollama():
    url = s.ollama_base_url.replace("host.docker.internal", "localhost")
    try:
        r = get(f"{url}/api/tags")
    except httpx.ConnectError as e:
        raise RuntimeError("Ollama not running or not installed — install from ollama.com, then: ollama pull hermes3:8b") from e
    names = [m["name"] for m in r.json().get("models", [])]
    return f"models: {', '.join(names) or 'none pulled yet'}" + ("" if any(s.ollama_model in n for n in names) else f"  (run: ollama pull {s.ollama_model})")


def twelvedata():
    r = get(f"https://api.twelvedata.com/quote?symbol=SPY&apikey={s.twelvedata_api_key}")
    return f"SPY close={r.json().get('close')}"


def finviz():
    r = get(f"https://elite.finviz.com/export.ashx?v=111&f=sh_price_u20&auth={s.finviz_elite_auth_token}")
    first = r.text.splitlines()[0] if r.text else ""
    if not first.startswith('"No.'):
        raise RuntimeError("did not receive a CSV (token invalid or not Elite)")
    return f"CSV export ok ({len(r.text.splitlines()) - 1} rows)"


# ---------------------------------------------------------------- report

def _env(name: str) -> str:  # .env is loaded by pydantic-settings; read raw for keys not in Settings
    from dotenv import dotenv_values

    return dotenv_values(ENGINE_ROOT.parent / ".env").get(name, "") or ""


print(f"\nTradeHub credential check — env={s.tradehub_env} paper_mode={s.paper_mode} feed={s.alpaca_data_feed}\n")

print("Phase 0-1 (needed now)")
probe("SUPABASE_URL+SERVICE_KEY", "Phase 1 · everything", s.supabase_configured, supabase)
probe("ALPACA paper keys", "Phase 1 · broker + market data", s.alpaca_configured, alpaca)
probe("ALPACA data feed", "Phase 1", s.alpaca_configured, alpaca_data)
probe("N8N_ENCRYPTION_KEY", "docker compose (generated)", bool(_env("N8N_ENCRYPTION_KEY")))
probe("ENGINE_API_TOKEN", "n8n → engine auth (generated)", bool(s.engine_api_token))
probe("N8N_WEBHOOK_SECRET", "engine → n8n HMAC (generated)", bool(s.n8n_webhook_secret))

print("\nPhase 3-5 (free)")
probe("OLLAMA", "Phase 3 · local Hermes 8B", True, ollama)
probe("FINNHUB_API_KEY", "Phase 3 · news", bool(s.finnhub_api_key), finnhub)
probe("REDDIT client id/secret", "Phase 3 · sentiment", bool(s.reddit_client_id and s.reddit_client_secret), reddit)
probe("FMP_API_KEY", "Phase 2 screener fallback · float · fundamentals", bool(s.fmp_api_key), fmp)
probe("OPENROUTER_API_KEY", "Phase 4 · hosted Hermes (pay-per-use)", bool(s.openrouter_api_key), openrouter)
probe("TELEGRAM_BOT_TOKEN", "Phase 5 · alerts", bool(s.telegram_bot_token), telegram)

print("\nPhase 6+ / optional (free)")
probe("FRED_API_KEY", "Phase 6 · macro values", bool(s.fred_api_key), fred)
probe("EIA_API_KEY", "Phase 6 · energy data", bool(s.eia_api_key), eia)
probe("TWELVEDATA_API_KEY", "backup index/VIX display only", bool(s.twelvedata_api_key), twelvedata)

print("\nPaid (later)")
probe("FINVIZ_ELITE_AUTH_TOKEN", "Phase 2 · pre-market screener + float", bool(s.finviz_elite_auth_token), finviz)
print()
