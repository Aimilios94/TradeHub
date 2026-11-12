# Sample data

Small frozen samples committed for CI. Nothing here comes from a paid or copyrighted source, and none of it is real news.

## `golden_day/`

One synthetic day of headlines that must always come out the same.

- `news_items.json`: 24 invented headlines in the shape the recorder stores (source, title, link, publication time, excerpt, attached tickers). They cover copies of one story across feeds, a copy with a word added, a templated title about two companies, a repeated title outside the 72-hour window, every driver tag, and listicle wording.
- `expected.json`: which items are duplicates of which, the driver tags with direction and scheduled flag, and the ranking with each score.

The test `test_golden_day_gives_the_same_duplicates_tags_and_ranking` stores the headlines through the recorder's own code, runs the real pipeline and compares. When it fails after a change to dedupe, the tagger rules or the importance score, that change altered what the app would show: review the difference first, then regenerate `expected.json` on purpose.

To see what the pipeline makes of the file (it stores demo rows, so use a database you can afford to fill, not the live one):

```sh
python -m scripts.load_sample_data --expected
```

## Still to come

A few weeks of daily bars for the backtester's determinism test (Phase 3), and synthetic eval sets (Phase 4).
