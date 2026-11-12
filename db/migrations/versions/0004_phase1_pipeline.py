"""Phase 1 pipeline columns: dedupe, tagger and condenser bookkeeping, and social samples.

- news_items.processed_at: set once an item has been through dedupe and the tagger.
- news_items.importance_components: the inputs behind importance_score, so a rank can be explained.
- llm_traces.news_item_id: ties a condenser call (valid or failed) to the item it was for.
- tagged_events: one tag per driver per news item, with the rule set and rules that produced it.
- social_sentiment: one small sample per ticker per day per social source (StockTwits).

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE news_items ADD COLUMN processed_at timestamptz;
ALTER TABLE news_items ADD COLUMN importance_components jsonb;
CREATE INDEX news_items_duplicate_of_idx ON news_items (duplicate_of)
    WHERE duplicate_of IS NOT NULL;
CREATE INDEX news_items_unprocessed_idx ON news_items (id) WHERE processed_at IS NULL;
COMMENT ON COLUMN news_items.duplicate_of IS
    'The first-stored item telling the same story. NULL means this row is the one that is shown.';

ALTER TABLE llm_traces
    ADD COLUMN news_item_id bigint REFERENCES news_items (id) ON DELETE SET NULL;
CREATE INDEX llm_traces_news_item_idx ON llm_traces (news_item_id)
    WHERE news_item_id IS NOT NULL;

ALTER TABLE tagged_events ADD COLUMN tagger_version text;
ALTER TABLE tagged_events ADD COLUMN matched_rules text[] NOT NULL DEFAULT '{}';
CREATE UNIQUE INDEX tagged_events_news_driver_key ON tagged_events (news_item_id, driver_id)
    WHERE news_item_id IS NOT NULL;

-- Social samples are additive: a missing row means the score is news-only (D21).
CREATE TABLE social_sentiment (
    ticker_id  text NOT NULL REFERENCES tickers (id),
    day        date NOT NULL,
    source_id  text NOT NULL REFERENCES sources (id),
    bullish    integer NOT NULL CHECK (bullish >= 0),
    bearish    integer NOT NULL CHECK (bearish >= 0),
    messages   integer NOT NULL CHECK (messages >= bullish + bearish),
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker_id, day, source_id)
);
COMMENT ON COLUMN social_sentiment.messages IS
    'Messages in the sample, labeled or not. bullish + bearish is the labeled part.';
"""

DOWNGRADE = """
DROP TABLE IF EXISTS social_sentiment;
DROP INDEX IF EXISTS tagged_events_news_driver_key;
ALTER TABLE tagged_events DROP COLUMN IF EXISTS matched_rules;
ALTER TABLE tagged_events DROP COLUMN IF EXISTS tagger_version;
ALTER TABLE llm_traces DROP COLUMN IF EXISTS news_item_id;
DROP INDEX IF EXISTS news_items_unprocessed_idx;
DROP INDEX IF EXISTS news_items_duplicate_of_idx;
ALTER TABLE news_items DROP COLUMN IF EXISTS importance_components;
ALTER TABLE news_items DROP COLUMN IF EXISTS processed_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
