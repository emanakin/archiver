"""Reviewable, target-specific follow/unfollow test approvals."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade():
    op.create_table("relationship_tests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("target_id", sa.String(64), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("before_check_id", sa.String(36), sa.ForeignKey("insight_checks.id"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("follow_job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("unfollow_job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("authorized_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("account_id", "target_id"))
    op.create_index("ix_relationship_tests_account_id", "relationship_tests", ["account_id"])


def downgrade():
    op.drop_table("relationship_tests")
