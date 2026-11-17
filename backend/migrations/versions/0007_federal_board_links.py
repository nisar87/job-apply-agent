"""federal board links: jobs from the German federal board link to its own page, which opens from anywhere

Many ads there come from regional job sites (persy.jobs, yourfirm.de, …) that do not open outside Germany.
New jobs already get the board's page (ArbeitsagenturSource); this re-points the ones stored before.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

BOARD = "https://www.arbeitsagentur.de/jobsuche/jobdetail/"


def upgrade() -> None:
    for table in ("postings", "pool_jobs"):
        op.execute(
            f"UPDATE {table} SET url = '{BOARD}' || source_id "
            f"WHERE source = 'arbeitsagentur' AND url NOT LIKE '{BOARD}%'"
        )


def downgrade() -> None:
    pass  # the original links are not kept; nothing to restore
