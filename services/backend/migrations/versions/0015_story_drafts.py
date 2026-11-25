"""Link a separate photo story draft to its published feed draft.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("content_drafts") as batch:
        batch.add_column(sa.Column("source_draft_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_content_drafts_source_draft", "content_drafts", ["source_draft_id"], ["id"]
        )
        batch.create_unique_constraint("uq_content_drafts_source_draft", ["source_draft_id"])


def downgrade():
    with op.batch_alter_table("content_drafts") as batch:
        batch.drop_constraint("uq_content_drafts_source_draft", type_="unique")
        batch.drop_constraint("fk_content_drafts_source_draft", type_="foreignkey")
        batch.drop_column("source_draft_id")
