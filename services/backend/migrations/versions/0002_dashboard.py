"""Persistent dashboard settings, collections and acquisition intents."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None

def upgrade():
    for column in [
        sa.Column("label", sa.String(100), nullable=False, server_default=""),
        sa.Column("requested_limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("requested_order", sa.String(16), nullable=False, server_default="default"),
        sa.Column("last_collected_at", sa.DateTime(timezone=True)),
        sa.Column("collection_state", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("collection_cursor", sa.String(200)),
        sa.Column("collection_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]:
        op.add_column("sources", column)
    op.add_column("jobs", sa.Column("request_fingerprint", sa.String(64)))
    op.create_table("account_settings",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False))
    op.create_table("follow_intents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("target_id", sa.String(64), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.UniqueConstraint("account_id", "target_id"))
    op.create_table("worker_heartbeats",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table("worker_heartbeats")
    op.drop_table("follow_intents")
    op.drop_table("account_settings")
    op.drop_column("jobs", "request_fingerprint")
    for name in ("collection_complete", "collection_cursor", "collection_state", "last_collected_at", "requested_order", "requested_limit", "label"):
        op.drop_column("sources", name)
