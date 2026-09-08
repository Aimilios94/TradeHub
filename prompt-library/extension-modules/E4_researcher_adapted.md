# E4 — Researcher (adapted build spec)

**Original (verbatim):** [`../_originals/08_E4_researcher_OWN.md`](../_originals/08_E4_researcher_OWN.md) · **Plan:** §22 E4 · **Build order:** last (fuzziest) · **Template:** [10](../hermes-extensions/10_researcher-report.md) · **Table:** `research_reports`

## New infrastructure
Web search for n8n: self-hosted **SearXNG** (preferred, fits Docker) or Brave Search API free tier.

## Flow
theme / geo / type / horizon → search + discovery of 10–20 candidates with sub-segments (e.g. AI: chips / infra / SaaS / robotics / data-center) → FMP + Alpaca quantitative comparison (valuation, growth, momentum, beta, drawdown, Sharpe where available) → Python normalizes 0–100 (weights config; defaults Valuation 30 / Growth 25 / Momentum 15 / Sentiment 15 / Macro 15) → Hermes: ranked top-5 with entry zones, horizons, top-3 rationale, risks, diversification mix.

## Naming
Deliberately distinct from **Fundamentals Lab**: Researcher = thematic discovery ("best AI investments"); Fundamentals Lab = user-defined fundamental logic.
