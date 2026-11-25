"""Persist approved caption commands and their source message pointers."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("dm_inboxes", sa.Column("pending_caption", sa.JSON(), nullable=True))
    op.add_column("dm_submissions", sa.Column("caption_command", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("dm_submissions", "caption_command")
    op.drop_column("dm_inboxes", "pending_caption")
