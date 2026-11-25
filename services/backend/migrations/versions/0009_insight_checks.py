"""Durable bounded account and media observations."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    op.create_table("insight_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), unique=True, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(200)), sa.Column("next_cursor", sa.String(200)),
        sa.Column("complete", sa.Boolean()), sa.Column("profile", sa.JSON()), sa.Column("relationship", sa.JSON()),
        sa.Column("items", sa.JSON(), nullable=False), sa.Column("note", sa.String(500)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_insight_checks_account_id", "insight_checks", ["account_id"])


def downgrade():
    op.drop_table("insight_checks")
