"""a person's own Claude key, kept encrypted next to their own Gemini key

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("claude_api_key_enc", sa.Text(), server_default="", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "claude_api_key_enc")
