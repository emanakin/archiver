"""Keep shared audio references and Spotify synchronization checkpoints."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "playlist_sync",
        sa.Column("playlist_id", sa.String(22), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(16), nullable=False),
    )
    op.create_table(
        "saved_audio",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("audio_id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("artist", sa.String(250), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("feed_music", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("saved_audio")
    op.drop_table("playlist_sync")
