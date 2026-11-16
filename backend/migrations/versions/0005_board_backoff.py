"""board backoff: company boards that keep showing no technical job are checked less often

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_sites", sa.Column("idle_checks", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("job_sites", "idle_checks")
