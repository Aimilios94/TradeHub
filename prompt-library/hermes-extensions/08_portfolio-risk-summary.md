---
number: 8
slug: portfolio_risk_summary
name: Portfolio risk summary (E1)
tier: hosted
model: nousresearch/hermes-3-llama-3.1-70b
version: 0
status: built in extension phase E1 (after the core paper month)
---

# 08 — Portfolio risk summary (E1)

## Purpose
Plan §22 E1. Python computes everything (weights, vol, beta, Sharpe/Sortino, max DD, VaR 95/99, correlation matrix, diversification ratio, HHI, marginal risk contribution, capture ratios, five stress scenarios, PyPortfolioOpt suggestion). Hermes writes the client-ready summary → `portfolio_risk_reports`. Sanctioned read-point: the Guardian may read the latest row for concentration warnings.

## Inputs (all computed)
positions + external_holdings, weights, per-holding metrics, correlation summary, stress-scenario table, optimizer output (proposed weights + expected volatility reduction), user params (risk tolerance, benchmark, horizon).

## Output schema (draft)
```json
{
  "type": "object",
  "required": ["summary", "top_risk_holdings", "diversification_grade", "actions", "volatility_bands"],
  "properties": {
    "summary": {"type": "string"},
    "top_risk_holdings": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {"symbol": {"type": "string"}, "why": {"type": "string"}}}},
    "diversification_grade": {"type": "string", "enum": ["A", "B", "C", "D", "F"]},
    "actions": {"type": "array", "items": {"type": "object", "properties": {"symbol": {"type": "string"}, "action": {"type": "string", "enum": ["trim", "hold", "accumulate"]}, "reason": {"type": "string"}}}},
    "volatility_bands": {"type": "string"},
    "caveats": {"type": "array", "items": {"type": "string"}}
  }
}
```

## Original
[`../_originals/09_E1_portfolio-risk_OWN.md`](../_originals/09_E1_portfolio-risk_OWN.md) — adapted spec: [`../extension-modules/E1_portfolio-risk_adapted.md`](../extension-modules/E1_portfolio-risk_adapted.md).
