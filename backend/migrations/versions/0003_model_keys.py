"""model keys: a user's own Gemini key and the spend limits on each key

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("llm_api_key_enc", sa.Text(), server_default="", nullable=False))
    op.add_column("users", sa.Column("platform_key_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("users", sa.Column("platform_cap_usd", sa.Float(), nullable=True))
    op.add_column("users", sa.Column("own_key_cap_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "own_key_cap_usd")
    op.drop_column("users", "platform_cap_usd")
    op.drop_column("users", "platform_key_allowed")
    op.drop_column("users", "llm_api_key_enc")
