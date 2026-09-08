# E1 — Portfolio Risk Assessment (adapted build spec)

**Original (verbatim):** [`../_originals/09_E1_portfolio-risk_OWN.md`](../_originals/09_E1_portfolio-risk_OWN.md) · **Plan:** §22 E1 · **Build order:** first extension, after the core paper month · **Template:** [08](../hermes-extensions/08_portfolio-risk-summary.md) · **Table:** `portfolio_risk_reports`, `external_holdings`

## Split: code computes, Hermes concludes (~90% deterministic)
| Code (Python, `engine/app/extensions/portfolio_risk/`) | Hermes |
|---|---|
| positions from Alpaca + `external_holdings`; weights; volatility; beta; Sharpe; Sortino; max drawdown; VaR 95/99 per holding; correlation matrix + avg pairwise; diversification ratio; HHI; marginal risk contribution; upside/downside capture; five stress scenarios (−10% equities, +100bps rates, top-sector −20%, VIX +50%, −30% correlated shock); PyPortfolioOpt optimization with expected volatility reduction | client-ready summary, top-3 risk holdings, diversification grade A–F, trim/hold/accumulate, projected volatility bands |

## Inputs
Alpaca positions (auto), `external_holdings` (manual: symbol, qty, cost basis, account label), optional params (risk tolerance, benchmark, horizon).

## Integrations (the only sanctioned ones)
Guardian reads the latest report for concentration warnings; the weekly autopsy may cite it. Nothing else.

## Corrections vs the original
Any request in the original for the LLM to compute a metric is moved to code; Hermes receives the computed table.
