"""Scope inboxes to senders and retain playlist selections."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "dm_inboxes", naming_convention={"pk": "%(table_name)s_pkey"}
    ) as table:
        table.drop_constraint("dm_inboxes_pkey", type_="primary")
        table.create_primary_key("dm_inboxes_pkey", ["account_id", "sender_id"])
    op.create_table(
        "playlist_tracks",
        sa.Column("playlist_id", sa.String(22), primary_key=True),
        sa.Column("spotify_id", sa.String(22), primary_key=True),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("artist", sa.String(250), nullable=False),
    )
    op.create_table(
        "playlist_selections",
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("content_drafts.id"), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("spotify_id", sa.String(22), nullable=False),
        sa.Column("song_key", sa.String(64), nullable=False),
        sa.Column("catalog_id", sa.String(32), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("artist", sa.String(250), nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "spotify_id"),
        sa.UniqueConstraint("account_id", "song_key"),
        sa.UniqueConstraint("account_id", "catalog_id"),
    )


def downgrade():
    with op.batch_alter_table("dm_inboxes") as table:
        table.drop_constraint("dm_inboxes_pkey", type_="primary")
        table.create_primary_key("dm_inboxes_pkey", ["account_id"])
    op.drop_table("playlist_selections")
    op.drop_table("playlist_tracks")
