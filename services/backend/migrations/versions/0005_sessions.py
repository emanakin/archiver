"""Worker-only encrypted session references, source identities and username history."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None

def upgrade():
    op.add_column("sources", sa.Column("resolved_instagram_id", sa.String(64)))
    op.add_column("sources", sa.Column("collection_note", sa.String(200)))
    op.create_table("account_sessions",
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("revision", sa.String(36), nullable=False),
        sa.Column("pending_revision", sa.String(36)),
        sa.Column("library_version", sa.String(32), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True)),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("import_cursor", sa.String(200)),
        sa.Column("import_complete", sa.Boolean(), nullable=False))
    op.create_table("username_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("target_id", sa.String(64), sa.ForeignKey("targets.id"), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_username_history_target_id", "username_history", ["target_id"])

def downgrade():
    op.drop_index("ix_username_history_target_id", table_name="username_history")
    op.drop_table("username_history")
    op.drop_table("account_sessions")
    op.drop_column("sources", "collection_note")
    op.drop_column("sources", "resolved_instagram_id")
