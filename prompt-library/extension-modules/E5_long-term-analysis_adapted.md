# E5 — Long-Term Investment Analysis (adapted build spec)

**Plan:** §36 · **Build order:** after E2 (shares its pipeline) · **Template:** [14](../hermes-extensions/14_long-term-quality.md) · **Table:** `longterm_reports`
**Owner-supplied related source:** [`../_originals/12_fundamental-research-process_RICHARD-COFFIN.md`](../_originals/12_fundamental-research-process_RICHARD-COFFIN.md)

## One engine, two lenses
E2 answers "is this fairly priced right now"; E5 answers "is this a business worth owning for 5–10 years". Same FMP + Alpaca data; one extra scoring function + one extra template — not a duplicate module.

## Fixed methodology (five checks, every ticker)
1. Growth consistency (5/10/15-year CAGR averages, lumpiness)
2. Margin & ROIC/ROCE/ROE trend (moat widening or eroding)
3. Debt trajectory (leverage, interest coverage across years)
4. Shareholder returns (dividends, payout ratio, buybacks vs dilution)
5. Valuation vs its own 10-year history (P/E, EV/EBITDA, FCF yield percentiles)

Output: quality score + **Wonderful / Fair / Avoid** + thesis-breaking risks. All math in Python; Hermes writes the judgment.

## Provenance
This five-check list was authored during design, not supplied by the owner. Coffin's quantitative steps (finances, strategy/capital efficiency, valuation) may be adopted as E5's methodology in a later revision — owner deferred. Coffin's qualitative steps belong to the Fundamentals Lab.
