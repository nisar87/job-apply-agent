"""job sites: the directory of places where jobs are looked for

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_sites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("learned_from", sa.Text(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_jobs", sa.Integer(), nullable=False),
        sa.Column("last_matches", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "slug"),
    )
    op.create_index(op.f("ix_job_sites_provider"), "job_sites", ["provider"], unique=False)
    op.create_index(op.f("ix_job_sites_last_checked_at"), "job_sites", ["last_checked_at"], unique=False)
    # Shared table without user rows: readable and writable by the application role, no row-level security.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON job_sites TO jobagent_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_job_sites_last_checked_at"), table_name="job_sites")
    op.drop_index(op.f("ix_job_sites_provider"), table_name="job_sites")
    op.drop_table("job_sites")
