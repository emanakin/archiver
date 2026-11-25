"""Store feed music identities alongside existing canonical playlist receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("content_drafts", sa.Column("playlist_music", sa.JSON(), nullable=True))
    with op.batch_alter_table("playlist_selections") as batch:
        batch.alter_column("catalog_id", existing_type=sa.String(32), nullable=True)
        batch.add_column(sa.Column("audio_asset_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("audio_cluster_id", sa.String(32), nullable=True))
        batch.create_unique_constraint(
            "uq_playlist_selection_asset", ["account_id", "audio_asset_id"]
        )
        batch.create_unique_constraint(
            "uq_playlist_selection_cluster", ["account_id", "audio_cluster_id"]
        )


def downgrade():
    # Preserve spent-track receipts by carrying the asset identifier into the
    # legacy ID column before removing the feed-only fields.
    op.execute(
        "UPDATE playlist_selections SET catalog_id = audio_asset_id WHERE catalog_id IS NULL"
    )
    with op.batch_alter_table("playlist_selections") as batch:
        batch.drop_constraint("uq_playlist_selection_cluster", type_="unique")
        batch.drop_constraint("uq_playlist_selection_asset", type_="unique")
        batch.drop_column("audio_cluster_id")
        batch.drop_column("audio_asset_id")
        batch.alter_column("catalog_id", existing_type=sa.String(32), nullable=False)
    op.drop_column("content_drafts", "playlist_music")
