"""Migrations build exactly the schema the code expects, with row-level security.

Uses its own database (jobagent_migrate, from scripts/init-db.sql) so the per-test schema reset of the
main test database is not disturbed."""

import os

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.migrate import migrate
from app.models import Base
from app.schema import create_schema

HEAD = "0003"
URL = os.environ.get("TEST_MIGRATE_DATABASE_URL",
                     "postgresql+psycopg://jobagent_owner:owner@localhost:5432/jobagent_migrate")


def _empty_database() -> None:
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


def _state():
    engine = create_engine(URL)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        rls = conn.execute(text("SELECT relrowsecurity FROM pg_class WHERE relname = 'applications'")).scalar()
        policies = conn.execute(text("SELECT count(*) FROM pg_policies WHERE policyname = 'tenant_isolation'")).scalar()
    engine.dispose()
    return diff, version, rls, policies


def test_a_new_database_is_built_by_the_migrations():
    _empty_database()
    assert migrate(URL) == "migrated"
    diff, version, rls, policies = _state()
    assert diff == [], diff            # the migrations and the models agree
    assert version == HEAD and rls is True and policies == 16


def test_a_database_made_by_the_old_schema_command_is_adopted():
    _empty_database()
    create_schema(URL)
    assert migrate(URL) == "existing database marked as current"
    diff, version, rls, _ = _state()
    assert diff == [] and version == HEAD and rls is True
    assert migrate(URL) == "migrated"   # running it again changes nothing
