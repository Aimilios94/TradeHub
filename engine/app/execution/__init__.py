"""Execution engine (Phase 5, plan §12). Deploy webhook → kill switch → Guardian gate → order per rules:
pre-market = LIMIT only, extended_hours=true, TIF=day, no brackets/stops; synthetic stop via
alpaca-py websocket (< 1 s); 09:30 OCO attachment; every order event → `orders`; Telegram on everything.
"""
