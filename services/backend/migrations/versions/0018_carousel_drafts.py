"""Persist carousel composition and retain deleted draft receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("content_drafts", sa.Column("photo_assets", sa.JSON()))
    op.add_column("content_drafts", sa.Column("photo_order", sa.JSON()))
    op.add_column("content_drafts", sa.Column("discarded_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("content_drafts", "discarded_at")
    op.drop_column("content_drafts", "photo_order")
    op.drop_column("content_drafts", "photo_assets")
