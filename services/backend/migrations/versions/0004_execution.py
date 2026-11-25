"""Durable claims, account state, attempt journal and request/action budgets."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None

def upgrade():
    for column in [
        sa.Column("session_state", sa.String(24), nullable=False, server_default="disconnected"),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("automatic_pause_reason", sa.String(100)),
        sa.Column("live_acquisition_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("live_cleanup_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_only_verified_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("accounts", column)
    op.add_column("relationships", sa.Column("follows_back", sa.Boolean()))
    op.add_column("relationships", sa.Column("follows_back_observed_at", sa.DateTime(timezone=True)))
    for column in [
        sa.Column("claim_owner", sa.String(36)), sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True)), sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    ]:
        op.add_column("jobs", column)
    op.create_table("action_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("request_count", sa.Integer(), nullable=False))
    op.create_index("ix_action_attempts_job_id", "action_attempts", ["job_id"])
    op.create_table("attempt_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("action_attempts.id"), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_attempt_events_attempt_id", "attempt_events", ["attempt_id"])
    op.create_table("budget_usage",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("local_day", sa.String(10), primary_key=True),
        sa.Column("category", sa.String(16), primary_key=True),
        sa.Column("consumed", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False))
    op.create_table("budget_reservations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("action_attempts.id"), nullable=False),
        sa.Column("local_day", sa.String(10), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released", sa.Boolean(), nullable=False))
    op.create_index("ix_budget_reservations_attempt_id", "budget_reservations", ["attempt_id"])

def downgrade():
    op.drop_index("ix_budget_reservations_attempt_id", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_table("budget_usage")
    op.drop_index("ix_attempt_events_attempt_id", table_name="attempt_events")
    op.drop_table("attempt_events")
    op.drop_index("ix_action_attempts_job_id", table_name="action_attempts")
    op.drop_table("action_attempts")
    for name in ("attempt_count", "priority", "heartbeat_at", "dispatch_started_at", "lease_expires_at", "claim_owner"):
        op.drop_column("jobs", name)
    op.drop_column("relationships", "follows_back_observed_at")
    op.drop_column("relationships", "follows_back")
    for name in ("read_only_verified_at", "live_cleanup_enabled", "live_acquisition_enabled", "automatic_pause_reason", "cooldown_until", "restricted", "session_state"):
        op.drop_column("accounts", name)
