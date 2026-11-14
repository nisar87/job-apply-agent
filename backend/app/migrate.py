"""Bring the database to the newest schema: `python -m app.migrate` (run as the owner role).

A database created earlier with `python -m app.schema` has the tables but no migration history. It is
first completed (missing tables and policies are added, nothing is dropped) and marked as the baseline,
then every later migration runs. A new, empty database simply runs every migration.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import get_settings

BASELINE = "0001"


def _config(url: str) -> Config:
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["url"] = url
    return cfg


def migrate(owner_url: str | None = None) -> str:
    url = owner_url or get_settings().database_owner_url
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    cfg = _config(url)
    if "users" in tables and "alembic_version" not in tables:
        from app.schema import create_schema

        create_schema(url)
        command.stamp(cfg, BASELINE)
        outcome = "existing database marked as baseline"
    else:
        outcome = "migrated"
    command.upgrade(cfg, "head")
    return outcome


if __name__ == "__main__":
    print(migrate())
