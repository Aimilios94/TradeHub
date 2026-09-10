# Supabase Edge Functions

| Function | Purpose | Phase |
|---|---|---|
| `market-proxy` | Frontend's only market-data door: Alpaca quotes / snapshots / bars with keys server-side and a 2–5 s cache (plan §21 touchpoint 3). | 1 |

## Deploy (one-time setup, PowerShell)

Needs Node 22 LTS (the CLI runs through `npx`) and a Supabase **personal access token** (dashboard → Account → Access Tokens).

```powershell
cd C:\Users\sellm\Desktop\TradeHub
$env:SUPABASE_ACCESS_TOKEN = "<personal access token>"          # or: npx supabase login
npx supabase link --project-ref mehjcmbypalshlgwhxme
npx supabase secrets set ALPACA_PAPER_KEY_ID=<key> ALPACA_PAPER_SECRET_KEY=<secret>
npx supabase functions deploy market-proxy
```

JWT verification stays ON (default): callers must send the owner's Supabase session token
(`Authorization: Bearer <access_token>` plus the project `apikey` header). Smoke test after deploy:

```powershell
$u = "https://mehjcmbypalshlgwhxme.supabase.co/functions/v1/market-proxy?op=quotes&symbols=SPY,AAPL"
curl.exe -s $u -H "apikey: <anon key>" -H "Authorization: Bearer <session access token>"
```

Routes: `op=quotes|snapshots` (IEX, real-time) and `op=bars&timeframe=1Min&start=<ISO>[&end=][&feed=iex|sip]`
(SIP = consolidated tape, ~15 min delayed on the free plan). Errors from Alpaca pass through untouched (plan §33).
