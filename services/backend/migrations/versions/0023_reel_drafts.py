"""Keep source provenance and caption review state for content drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("content_drafts", sa.Column("reel_source", sa.JSON()))
    op.add_column("content_drafts", sa.Column("caption_review", sa.JSON()))


def downgrade():
    op.drop_column("content_drafts", "caption_review")
    op.drop_column("content_drafts", "reel_source")
