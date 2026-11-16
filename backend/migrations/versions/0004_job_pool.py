"""job pool: jobs collected by the harvester from free public feeds, shared by every search

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pool_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=500), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_snippet", sa.Boolean(), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("posted_date", sa.Date(), nullable=True),
        sa.Column("work_mode", sa.String(length=16), nullable=True),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column("contract_type", sa.String(length=100), nullable=True),
        sa.Column("hiring_regions", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                  nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_id"),
    )
    op.create_index(op.f("ix_pool_jobs_country"), "pool_jobs", ["country"], unique=False)
    op.create_index(op.f("ix_pool_jobs_posted_date"), "pool_jobs", ["posted_date"], unique=False)
    op.create_index(op.f("ix_pool_jobs_dedupe_key"), "pool_jobs", ["dedupe_key"], unique=False)
    op.create_index(op.f("ix_pool_jobs_last_seen"), "pool_jobs", ["last_seen"], unique=False)
    # Shared table without user rows: readable and writable by the application role, no row-level security.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON pool_jobs TO jobagent_app")


def downgrade() -> None:
    op.drop_index(op.f("ix_pool_jobs_last_seen"), table_name="pool_jobs")
    op.drop_index(op.f("ix_pool_jobs_dedupe_key"), table_name="pool_jobs")
    op.drop_index(op.f("ix_pool_jobs_posted_date"), table_name="pool_jobs")
    op.drop_index(op.f("ix_pool_jobs_country"), table_name="pool_jobs")
    op.drop_table("pool_jobs")
