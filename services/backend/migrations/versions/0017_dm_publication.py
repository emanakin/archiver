"""Add draft music and opt-in DM publication grants."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("content_drafts", sa.Column("music_canonical_id", sa.String(32)))
    op.add_column("dm_inboxes", sa.Column("direct_publish_from", sa.DateTime(timezone=True)))
    op.add_column("dm_submissions", sa.Column("draft_commands", sa.JSON()))


def downgrade():
    op.drop_column("dm_submissions", "draft_commands")
    op.drop_column("dm_inboxes", "direct_publish_from")
    op.drop_column("content_drafts", "music_canonical_id")
