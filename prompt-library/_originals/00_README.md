# /prompt-library/_originals/ — source materials, verbatim

Every original prompt, strategy, and document supplied by the owner during the design phase.
**These are the raw sources.** Adapted/working versions live in the implementation plan (plan §7, §8, §10, §14, §22, §36, §37) and in the sibling folders of /prompt-library/ (see plan §23).

Rule: never edit a file in this folder. If a source needs correcting, note the correction in the adapted version and record why.

| File | What it is | Where it's used in the plan |
|---|---|---|
| 01_technical-traps_OWN.md | Owner's original architecture notes + 3 technical traps + intended daily workflow | §3 (corrected/verified), §4, §12 |
| 02_S1_low-float-gapper_OWN.md | Strategy 1, verbatim | §7 S1 |
| 03_S2_premarket-game-plan_OWN.md | Watchlist briefing prompt, verbatim | §7 S2, §10 template 3 |
| 04_T1_2bar-trailing-stop_OWN.md | 2-bar trailing stop spec (written in Pine terms) | §9 exit plugin #3 |
| 05_T2_ai-trade-autopsy_OWN.md | Trade autopsy template, verbatim | §10 template 5 |
| 06_T3_performance-analysis_OWN.md | Performance/journal analysis spec, verbatim | §14 Analytics Module |
| 07_E2_financial-analyst_OWN.md | Financial analyst prompt, verbatim | §22 E2 |
| 08_E4_researcher_OWN.md | Alternative-investments researcher prompt, verbatim | §22 E4 |
| 09_E1_portfolio-risk_OWN.md | Portfolio risk prompt, verbatim | §22 E1 |
| 10_F-A_stocks-in-play_SMB-CAPITAL.md | SMB Capital selection framework | §8 F-A, §7 S3 |
| 11_HPS-setups_JOHN-KURISKO_interview-supplement.md | Kurisko interview summary — **secondary source** | §7 S4–S8 (PDF wins on conflict) |
| 12_fundamental-research-process_RICHARD-COFFIN.md | Coffin's 6-step fundamental process | §37 Fundamentals Lab (qualitative), §36 E5 (candidate) |
| pdfs/John_Kurisko_s_interview_on_Desire_To_Trade.pdf | **SOURCE OF TRUTH for HPS strategies** | §7 S4–S8 |
| pdfs/hermes-polymarket-full.pdf | Polymarket copy-trading bot build prompt | §22 E3 |
| pdfs/polymarket-ai-trading-cheat-sheet.pdf | Polymarket 5 agent strategies + safety checklist | §22 E3 |

## Provenance rules (from plan §7/§23)
- `pdf_canonical` beats `interview_supplement` for the Kurisko strategies. Known conflicts are listed at the top of file 11.
- Owner-supplied prompts are kept verbatim even where the plan corrects them (e.g. LLM math, extended-hours brackets, price "forecasts"). The corrections and their reasons are in the plan, not here.
- Anything not supplied is marked "built from spec" in the adapted folders.
