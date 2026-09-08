# /prompt-library — every prompt and strategy definition in one place

Plan §23. Human-readable source of truth. The database seed (`supabase/migrations/0002_seed.sql`) is **generated** from `engine/app/seed/*.py` + `hermes-core/*.md` by `engine/scripts/gen_seed.py`; strategy and framework rows reference these files via `library_file`. Version = git history.

**Provenance rule:** `pdf_canonical` > `interview_supplement`. Owner-supplied originals are kept verbatim in `_originals/` and never edited; corrections live in the adapted files with reasons.

## `_originals/` — raw sources (owner-supplied, verbatim)
Indexed by [`_originals/00_README.md`](_originals/00_README.md): 12 markdown files + 3 PDFs (the Kurisko HPS PDF is the source of truth for S4–S8; the two Polymarket PDFs for E3).

## `strategies/` — the 8 trading setups (NAME + CREATOR in every filename)
| File | Setup | Provenance | Status |
|---|---|---|---|
| [S1_low-float-gapper_OWN.md](strategies/S1_low-float-gapper_OWN.md) | pre-market low-float gapper, 5× IEX rel-vol | own | backtesting |
| [S2_premarket-watchlist-briefing_OWN.md](strategies/S2_premarket-watchlist-briefing_OWN.md) | auto-filled watchlist game plan (no orders) | own | paper |
| [S3_stocks-in-play_SMB-CAPITAL.md](strategies/S3_stocks-in-play_SMB-CAPITAL.md) | Reason / Participation / Structure scan, focus list of 3 | derived | backtesting |
| [S4_hps-quad-divergence_JOHN-KURISKO.md](strategies/S4_hps-quad-divergence_JOHN-KURISKO.md) | quad stochastic divergence "Super Signal", 5-min | pdf_canonical | backtesting |
| [S5_hps-2020-bull-flag_JOHN-KURISKO.md](strategies/S5_hps-2020-bull-flag_JOHN-KURISKO.md) | 20 EMA flag, 60-10 embedded > 85 | pdf_canonical | backtesting |
| [S6_hps-bear-flag-canonical_JOHN-KURISKO.md](strategies/S6_hps-bear-flag-canonical_JOHN-KURISKO.md) | short at the 9-3 rotation to 80, single entry | pdf_canonical | backtesting |
| [S7_hps-bear-flag-variantB_JOHN-KURISKO-interview.md](strategies/S7_hps-bear-flag-variantB_JOHN-KURISKO-interview.md) | 200 EMA regime, every rotation, rotation-complete exit | interview_supplement | backtesting (A/B vs S6) |
| [S8_hps-swing-divergence_JOHN-KURISKO.md](strategies/S8_hps-swing-divergence_JOHN-KURISKO.md) | S4 logic on 60-min / daily | pdf_canonical | backtesting |

## `frameworks/` — stock-SELECTION strategies (how to choose, not entries)
- [FA_stocks-in-play-triple-filter_SMB-CAPITAL.md](frameworks/FA_stocks-in-play-triple-filter_SMB-CAPITAL.md) — Reason / Participation / Structure + the one-sentence test
- [FB_trend-first-rule_JOHN-KURISKO.md](frameworks/FB_trend-first-rule_JOHN-KURISKO.md) — trend determines the exit; only trade the confirmed signal

## `hermes-core/` — the 7 core templates (plan §10) — parsed into `prompt_templates`
| # | File | Tier | Used by |
|---|---|---|---|
| 01 | [sentiment condenser](hermes-core/01_sentiment-condenser.md) | local 8B | Phase 3 → `news_sentiment` |
| 02 | [morning trade plan](hermes-core/02_morning-trade-plan.md) | hosted | Phase 4 → `signals` |
| 03 | [watchlist briefing](hermes-core/03_watchlist-briefing.md) | hosted | S2 |
| 04 | [one-sentence in-play test](hermes-core/04_one-sentence-in-play-test.md) | hosted | F-A gate for S1/S3 |
| 05 | [per-trade autopsy](hermes-core/05_per-trade-autopsy.md) | hosted | Phase 6 → `journal.autopsy` |
| 06 | [weekly autopsy → hypotheses](hermes-core/06_weekly-autopsy-hypotheses.md) | hosted 405B | Phase 6 → `hypotheses` |
| 07 | [Guardian briefing](hermes-core/07_guardian-briefing.md) | hosted | Phase 6.5 |

Each file: YAML-style header (`number, slug, name, tier, model, version`) + `## Purpose` + fenced `## System prompt`, `## User prompt template` ({{placeholders}}), `## Output schema` (JSON Schema). The generator rejects files missing any section.

## `hermes-extensions/` — templates 08–16 (built in the extension phases; headers + draft schemas now)
[08 portfolio risk](hermes-extensions/08_portfolio-risk-summary.md) · [09 analyst conclusion](hermes-extensions/09_analyst-conclusion.md) · [10 researcher](hermes-extensions/10_researcher-report.md) · [11 PM scoring](hermes-extensions/11_pm-wallet-trade-scoring.md) · [12 PM EOD](hermes-extensions/12_pm-eod-report.md) · [13 Ask Hermes](hermes-extensions/13_ask-hermes.md) · [14 long-term quality](hermes-extensions/14_long-term-quality.md) · [15 Fundamentals Lab filters](hermes-extensions/15_fundamentals-criteria-to-filters.md) · [16 Fundamentals Lab deep-dive](hermes-extensions/16_fundamentals-deep-dive.md)

## `extension-modules/` — adapted build specs, each linking its verbatim original
[E1 portfolio risk](extension-modules/E1_portfolio-risk_adapted.md) · [E2 financial analyst](extension-modules/E2_financial-analyst_adapted.md) · [E3 Polymarket source notes](extension-modules/E3_polymarket_source-notes.md) · [E4 researcher](extension-modules/E4_researcher_adapted.md) · [E5 long-term analysis](extension-modules/E5_long-term-analysis_adapted.md) · [Fundamentals Lab](extension-modules/FL_fundamentals-lab_adapted.md)
Build order (plan §36): E1 → E2 → E5 → Fundamentals Lab → E3 → E4, strictly after the core paper month.

## `tests/` — test specifications given by the owner (adapted)
[T1 N-bar trailing stop](tests/T1_2bar-trailing-stop_spec.md) · [T2 AI trade autopsy](tests/T2_ai-trade-autopsy_template.md) · [T3 performance analysis](tests/T3_performance-analysis_spec.md)
