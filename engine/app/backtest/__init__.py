"""Backtest engine (Phase 7, plan §13). Custom pandas/NumPy event loop with scanner-universe replay
(decision 2026-09-08: not vectorbt / backtesting.py). Four mandatory countermeasures: survivorship
coverage stats, IEX↔SIP calibration, explicit fill model (spread, partial fills, no mid-bar magic),
overfitting guards (min samples, walk-forward). Win rate is information, never the gate.
"""
