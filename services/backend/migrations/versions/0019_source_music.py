"""Retain optional catalog metadata from imported media."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("content_drafts", sa.Column("source_music", sa.JSON()))


def downgrade():
    op.drop_column("content_drafts", "source_music")
