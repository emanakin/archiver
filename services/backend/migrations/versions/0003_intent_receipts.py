"""Remember every accepted request key, including business-deduplicated aliases."""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None

def upgrade():
    op.create_table("intent_receipts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id")),
        sa.Column("result", sa.JSON()),
        sa.UniqueConstraint("account_id", "idempotency_key"))

def downgrade():
    op.drop_table("intent_receipts")
