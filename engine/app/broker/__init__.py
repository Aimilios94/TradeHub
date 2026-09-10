"""Broker access (Alpaca paper). Phase 4 is READ-ONLY: account snapshot and open positions.

Order placement is Phase 5 and lands beside this as `orders.py`, behind the Guardian gate and the
owner's Deploy tap. Nothing in this package can create, modify or cancel an order.
"""
