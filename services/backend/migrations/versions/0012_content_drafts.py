"""Immutable image assets and revisioned photo/story drafts."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None


def upgrade():
    op.create_table("content_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("mime", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False), sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "sha256"))
    op.create_index("ix_content_assets_account_id", "content_assets", ["account_id"])
    op.create_table("content_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("content_assets.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False), sa.Column("caption", sa.String(2200), nullable=False),
        sa.Column("fit_mode", sa.String(12), nullable=False), sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("overlay_text", sa.String(180), nullable=False), sa.Column("text_position", sa.String(12), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("media_id", sa.String(64)), sa.Column("publication_job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_content_drafts_account_id", "content_drafts", ["account_id"])


def downgrade():
    op.drop_table("content_drafts")
    op.drop_table("content_assets")
