"""Phase 1 — market data layer.

- alpaca.py     : alpaca-py wrapper (bars, latest quotes, snapshots) with feed selection + ET helpers
- premarket.py  : PURE functions — pre-market window, cumulative-volume profile, 14-day baseline, rel-vol
- repo.py       : Supabase reads/writes for premarket_volume_baseline and feed_calibration
- jobs.py       : nightly baseline + IEX↔SIP calibration job (called by n8n 23:00 ET)
- router.py     : /data/* endpoints
"""
