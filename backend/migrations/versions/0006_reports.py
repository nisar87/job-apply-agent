"""reports: how often each user gets the report of what was applied to, and at what local hour

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("report_frequency", sa.String(8), server_default="daily", nullable=False))
    op.add_column("users", sa.Column("report_hour", sa.Integer(), server_default="20", nullable=False))


def downgrade() -> None:
    op.drop_column("users", "report_hour")
    op.drop_column("users", "report_frequency")
