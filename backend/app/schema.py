"""Create or upgrade the database schema, including row-level security.

Run as the owner role: `python -m app.schema`. The application itself connects as the
unprivileged `jobagent_app` role, so the policies below apply to everything it does.
"""

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.models import SHARED_TABLES, USER_TABLES, Base

APP_ROLE = "jobagent_app"

_POLICY = (
    "current_setting('app.bypass_rls', true) = 'on' "
    "OR {col} = nullif(current_setting('app.user_id', true), '')::uuid"
)


def create_schema(owner_url: str | None = None, drop_first: bool = False) -> None:
    engine = create_engine(owner_url or get_settings().database_owner_url)
    with engine.begin() as conn:
        if drop_first:
            Base.metadata.drop_all(conn)
        Base.metadata.create_all(conn)
        for table, col in [("users", "id")] + [(t, "user_id") for t in USER_TABLES]:
            cond = _POLICY.format(col=col)
            conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            conn.execute(
                text(f"CREATE POLICY tenant_isolation ON {table} USING ({cond}) WITH CHECK ({cond})")
            )
        for table in ["users"] + USER_TABLES + SHARED_TABLES:
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}"))
    engine.dispose()


if __name__ == "__main__":
    create_schema()
    print("schema ready")
