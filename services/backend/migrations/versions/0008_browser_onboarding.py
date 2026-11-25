"""Short-lived worker-encrypted browser account connection handoff."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = depends_on = None

def upgrade():
    op.create_table("onboarding_keys", sa.Column("id", sa.String(64), primary_key=True), sa.Column("public_key", sa.String(100), nullable=False), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("account_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), unique=True, nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("password_envelope", sa.JSON(), nullable=True),
        sa.Column("code_envelope", sa.JSON(), nullable=True),
        sa.Column("challenge_id", sa.String(36)),
        sa.Column("challenge_kind", sa.String(24)),
        sa.Column("code_submitted", sa.Boolean(), nullable=False),
        sa.Column("recovery_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("max_requests", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(36)),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_account_connections_account_id", "account_connections", ["account_id"])
    op.create_index("ix_account_connections_state", "account_connections", ["state"])

def downgrade():
    op.drop_table("account_connections")
    op.drop_table("onboarding_keys")
