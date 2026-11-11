"""news_items.source_meta: extra facts a source sends with a headline.

Holds what does not fit the fixed columns, for example the publisher, or the sentiment label
Massive's own model attaches to an item. Such labels are a reference to compare against later.
They are never TradeHub's sentiment (that is news_sentiment) and never an eval label.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE = """
ALTER TABLE news_items ADD COLUMN source_meta jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN news_items.source_meta IS
    'Extra fields from the source, kept as received. Reference only; never a score or a label.';
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("ALTER TABLE news_items DROP COLUMN IF EXISTS source_meta")
