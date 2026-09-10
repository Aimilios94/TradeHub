// TradeHub — market-proxy Edge Function (plan §21 touchpoint 3, Phase 1).
// The FUTURE FRONTEND'S ONLY DATA DOOR: Alpaca keys stay server-side; responses are cached 1–5 s.
// Plain fetch(), no SDK (spec: "overkill for two endpoints").
//
// Routes (GET, query-string):
//   ?op=quotes&symbols=AAPL,TSLA                      → latest IEX quotes (real-time)
//   ?op=snapshots&symbols=AAPL,TSLA                   → latest quote + trade + daily/prev-daily bars
//   ?op=bars&symbols=AAPL&timeframe=1Min&start=ISO[&end=ISO][&feed=iex|sip][&limit=n]
//            SIP bars are allowed (historical, ~15 min delayed on the free plan); SIP quotes are not.
//
// Auth: verify_jwt stays ON (Supabase default) — the owner's session token is required.
// Secrets (set once):  supabase secrets set ALPACA_PAPER_KEY_ID=... ALPACA_PAPER_SECRET_KEY=...
// Deploy:              supabase functions deploy market-proxy
//
// Cache: per-URL in-memory Map with a TTL (quotes 2 s, snapshots 3 s, bars 5 s). Edge isolates are
// short-lived so this is best-effort — exactly what the spec asks for (1–5 s), not a durable cache.

const DATA_BASE = "https://data.alpaca.markets/v2/stocks";
const TTL_MS: Record<string, number> = { quotes: 2000, snapshots: 3000, bars: 5000 };
const MAX_SYMBOLS = 100;
const TIMEFRAMES = new Set(["1Min", "5Min", "15Min", "1Hour", "1Day"]);

type CacheEntry = { at: number; status: number; body: string };
const cache = new Map<string, CacheEntry>();

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
};

function json(status: number, body: unknown, extra: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...CORS, ...extra },
  });
}

function parseSymbols(raw: string | null): string[] | null {
  if (!raw) return null;
  const syms = [...new Set(raw.split(",").map((s) => s.trim().toUpperCase()).filter((s) => /^[A-Z.\-]{1,10}$/.test(s)))];
  if (syms.length === 0 || syms.length > MAX_SYMBOLS) return null;
  return syms;
}

function alpacaUrl(op: string, q: URLSearchParams): string | { error: string } {
  const symbols = parseSymbols(q.get("symbols"));
  if (!symbols) return { error: `symbols: 1–${MAX_SYMBOLS} tickers, comma-separated` };
  const feed = q.get("feed") ?? "iex";
  if (feed !== "iex" && feed !== "sip") return { error: "feed must be iex or sip" };
  const p = new URLSearchParams({ symbols: symbols.join(","), feed });
  switch (op) {
    case "quotes":
      if (feed !== "iex") return { error: "latest quotes are IEX-only on this plan" };
      return `${DATA_BASE}/quotes/latest?${p}`;
    case "snapshots":
      if (feed !== "iex") return { error: "snapshots are IEX-only on this plan" };
      return `${DATA_BASE}/snapshots?${p}`;
    case "bars": {
      const timeframe = q.get("timeframe") ?? "1Min";
      if (!TIMEFRAMES.has(timeframe)) return { error: `timeframe must be one of ${[...TIMEFRAMES].join(", ")}` };
      const start = q.get("start");
      if (!start || Number.isNaN(Date.parse(start))) return { error: "start must be an ISO-8601 datetime" };
      p.set("timeframe", timeframe);
      p.set("start", new Date(start).toISOString());
      const end = q.get("end");
      if (end) {
        if (Number.isNaN(Date.parse(end))) return { error: "end must be an ISO-8601 datetime" };
        p.set("end", new Date(end).toISOString());
      }
      const limit = Math.min(Math.max(parseInt(q.get("limit") ?? "1000", 10) || 1000, 1), 10000);
      p.set("limit", String(limit));
      p.set("adjustment", "raw");
      return `${DATA_BASE}/bars?${p}`;
    }
    default:
      return { error: "op must be quotes | snapshots | bars" };
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "GET") return json(405, { error: "GET only" });

  const keyId = Deno.env.get("ALPACA_PAPER_KEY_ID");
  const secret = Deno.env.get("ALPACA_PAPER_SECRET_KEY");
  if (!keyId || !secret) return json(503, { error: "Alpaca secrets not configured on the function" });

  const q = new URL(req.url).searchParams;
  const op = q.get("op") ?? "";
  const target = alpacaUrl(op, q);
  if (typeof target !== "string") return json(422, target);

  const ttl = TTL_MS[op];
  const hit = cache.get(target);
  if (hit && Date.now() - hit.at < ttl) {
    return new Response(hit.body, {
      status: hit.status,
      headers: { "content-type": "application/json", "x-cache": "hit", "x-cache-age-ms": String(Date.now() - hit.at), ...CORS },
    });
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      headers: { "APCA-API-KEY-ID": keyId, "APCA-API-SECRET-KEY": secret, accept: "application/json" },
    });
  } catch (e) {
    return json(502, { error: `alpaca unreachable: ${(e as Error).message}` });
  }
  const body = await upstream.text();
  // Fail loud, never fake (plan §33): pass Alpaca's status + message through untouched.
  if (upstream.ok) cache.set(target, { at: Date.now(), status: upstream.status, body });
  if (cache.size > 500) cache.clear();
  return new Response(body, {
    status: upstream.status,
    headers: { "content-type": "application/json", "x-cache": "miss", "x-feed": q.get("feed") ?? "iex", ...CORS },
  });
});
