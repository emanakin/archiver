"""Owner-authorized DM inboxes, scan checkpoints and unique submissions."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dm_inboxes",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("sender_id", sa.String(64), nullable=False),
        sa.Column("sender_username", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(64)),
        sa.Column("cursor", sa.String(512)),
        sa.Column("scan_head_id", sa.String(64)),
        sa.Column("watermark", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "dm_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("sender_id", sa.String(64), nullable=False),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("media_ref", sa.String(128)),
        sa.Column("draft_id", sa.String(36), sa.ForeignKey("content_drafts.id")),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "thread_id", "message_id"),
    )
    op.create_index("ix_dm_submissions_account_id", "dm_submissions", ["account_id"])


def downgrade():
    op.drop_table("dm_submissions")
    op.drop_table("dm_inboxes")
