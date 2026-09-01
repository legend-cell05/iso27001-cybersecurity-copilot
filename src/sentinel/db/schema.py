"""Schema lifecycle: create, inspect, drop.

``initialise_database`` is idempotent and is what every entry point calls
first. Every statement in ``sql/schema`` is ``CREATE ... IF NOT EXISTS``, and
a regression test asserts it: a compliance tool that quietly empties the
evidence index on start-up would destroy the only record that an organisation
ever did the work.

Dropping is a separate, explicitly named operation for the same reason.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sentinel.config import Settings, get_settings
from sentinel.db.engine import get_engine
from sentinel.db.sql_files import read_sql, split_statements, sql_dir
from sentinel.exceptions import DatabaseError
from sentinel.logging_config import get_logger

logger = get_logger(__name__)


def _apply_file(relative_path: str, settings: Settings) -> int:
    sql = read_sql(relative_path, settings)
    statements = split_statements(sql)
    engine = get_engine(settings)
    try:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))
    except SQLAlchemyError as exc:
        raise DatabaseError(f"{relative_path}: {exc}") from exc
    return len(statements)


def initialise_database(settings: Settings | None = None) -> dict[str, int]:
    """Create the schema and every table. Safe to re-run."""
    settings = settings or get_settings()
    applied: dict[str, int] = {}
    schema_files = sorted((sql_dir(settings) / "schema").glob("*.sql"))
    if not schema_files:
        raise DatabaseError(f"no schema files under {sql_dir(settings) / 'schema'}")
    for path in schema_files:
        applied[path.name] = _apply_file(f"schema/{path.name}", settings)
    logger.info("database initialised", extra={"files": len(applied)})
    return applied


def drop_schema(settings: Settings | None = None) -> None:
    """Drop the ISMS schema and everything in it.

    Every assessment, every applicability decision, every evidence record and
    every risk acceptance. The CLI asks before calling this.
    """
    settings = settings or get_settings()
    try:
        with get_engine(settings).begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {settings.schema_name} CASCADE"))
    except SQLAlchemyError as exc:
        raise DatabaseError(f"failed to drop the schema: {exc}") from exc
    logger.warning("schema dropped", extra={"schema": settings.schema_name})


def schema_exists(settings: Settings | None = None) -> bool:
    """Whether the ISMS schema has been created.

    A reachable database is not an initialised one, and the commands that
    report on the ISMS need to tell a user which of the two they are looking
    at rather than failing with "relation does not exist".
    """
    settings = settings or get_settings()
    try:
        with get_engine(settings).connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
                {"name": settings.schema_name},
            ).first()
    except SQLAlchemyError as exc:
        raise DatabaseError(f"failed to inspect the schema: {exc}") from exc
    return found is not None


def table_counts(settings: Settings | None = None) -> dict[str, int]:
    """Exact row counts per table.

    Exact, not ``pg_class.reltuples``: an estimate is zero until autovacuum
    has been past, which makes a freshly populated register look empty in a
    report that is meant to prove it is not.
    """
    settings = settings or get_settings()
    engine = get_engine(settings)
    counts: dict[str, int] = {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """SELECT table_name
                       FROM information_schema.tables
                       WHERE table_schema = :schema AND table_type = 'BASE TABLE'
                       ORDER BY table_name"""
                ),
                {"schema": settings.schema_name},
            ).all()
            for row in rows:
                table = str(row[0])
                # Annotated because SQLAlchemy 2.1 types scalar_one() as a
                # TypeVar that cannot be solved here: the query is built from
                # a string, so there is no Select[...] to infer from.
                count: int = conn.execute(
                    text(f"SELECT COUNT(*) FROM {settings.schema_name}.{table}")
                ).scalar_one()
                counts[table] = count
    except SQLAlchemyError as exc:
        raise DatabaseError(f"failed to count tables: {exc}") from exc
    return counts
