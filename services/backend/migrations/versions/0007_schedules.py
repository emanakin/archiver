"""Opt-in daily source collection and indexes for account queues/provenance."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = depends_on = None

def upgrade():
    op.add_column("sources", sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("sources", sa.Column("schedule_local_time", sa.String(5), nullable=False, server_default="09:00"))
    op.add_column("sources", sa.Column("schedule_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_jobs_account_state", "jobs", ["account_id", "state", "scheduled_at"])
    op.create_index("ix_jobs_account_target_kind", "jobs", ["account_id", "target_id", "kind", "state"])
    op.create_index("ix_discoveries_target_id", "discoveries", ["target_id"])

def downgrade():
    op.drop_index("ix_discoveries_target_id", table_name="discoveries")
    op.drop_index("ix_jobs_account_target_kind", table_name="jobs")
    op.drop_index("ix_jobs_account_state", table_name="jobs")
    for name in ("schedule_version", "schedule_local_time", "schedule_enabled"):
        op.drop_column("sources", name)
