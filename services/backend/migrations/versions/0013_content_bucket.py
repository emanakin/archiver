"""Track the storage backend of immutable originals."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "content_assets",
        sa.Column("storage_backend", sa.String(12), nullable=False, server_default="local"),
    )


def downgrade():
    bucket_assets = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM content_assets WHERE storage_backend != 'local'")
    )
    if bucket_assets:
        raise RuntimeError("Restore bucket originals to local storage before downgrading")
    op.drop_column("content_assets", "storage_backend")
