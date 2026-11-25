"""Versioned explicit workflow enablement and adoption retention anchors."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None

def upgrade():
    op.add_column("accounts", sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("relationships", sa.Column("adopted_at", sa.DateTime(timezone=True)))

def downgrade():
    op.drop_column("relationships", "adopted_at")
    op.drop_column("accounts", "workflow_version")
