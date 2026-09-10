"""The synthetic stop monitor (plan §12 rule 3) — its own process, its own container.

Pre-market, a broker-side stop is a lie: Alpaca accepts it and it does not trigger until 09:30 (§3.4).
So while a position is held pre-market its only protection is this: a websocket listener on trades and
quotes that fires a closing LIMIT order in under a second when the stop level is breached.

It runs as a separate container (`monitor` in docker-compose) rather than inside the engine, because
rebuilding the API after a code change must never take the stop down while a position is open, and a
crash here must restart only this.

Best-effort, and honest about it: a halt, a gap through the level, or a dead connection can all beat it.
That is a property of pre-market trading, not a bug — plan §34 requires the risk be surfaced, not hidden.
"""
