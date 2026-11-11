"""news_items.source_tickers: the tickers the source itself attached to a headline.

The recorder fetches news per watchlist ticker. Without this column the link between a headline
and the ticker it was fetched for would be lost until the condenser runs. This is a fact from
the source (which feed or query returned the item), not a model's judgement; the condenser's
own ticker list stays in news_sentiment.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE news_items ADD COLUMN source_tickers text[] NOT NULL DEFAULT '{}';
COMMENT ON COLUMN news_items.source_tickers IS
    'Canonical ticker IDs the source attached to the item (the feed or query it came from).';
CREATE INDEX news_items_source_tickers_idx ON news_items USING gin (source_tickers);
"""

DOWNGRADE = """
DROP INDEX IF EXISTS news_items_source_tickers_idx;
ALTER TABLE news_items DROP COLUMN IF EXISTS source_tickers;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
