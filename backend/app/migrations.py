"""
Database schema migrations for QuakeResQ.

Provides idempotent, non-destructive migrations to bring existing SQLite tables
up to date with the latest SQLAlchemy entity models without deleting data or
recreating tables.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)


class ColumnSpec(NamedTuple):
    name: str
    sql_type: str
    default_clause: str = "DEFAULT NULL"


# Missing columns to add to existing tables if absent
TABLE_MIGRATIONS: dict[str, list[ColumnSpec]] = {
    "incidents": [
        ColumnSpec("severity", "INTEGER"),
        ColumnSpec("people_affected", "INTEGER"),
        ColumnSpec("urgency", "INTEGER"),
        ColumnSpec("evidence_strength", "INTEGER"),
        ColumnSpec("priority_score", "FLOAT"),
        ColumnSpec("priority_level", "VARCHAR(20)"),
    ],
    "rescue_teams": [
        ColumnSpec("members_count", "INTEGER"),
    ],
}


def run_migrations(engine: Engine) -> list[str]:
    """
    Apply missing column migrations to existing tables idempotently.

    Inspects each table's schema, adds missing columns via ALTER TABLE ADD COLUMN,
    and returns a list of applied migration actions. Preserves all existing records.
    """
    applied_actions: list[str] = []

    with engine.connect() as conn:
        inspector = inspect(conn)
        existing_tables = set(inspector.get_table_names())

        for table_name, column_specs in TABLE_MIGRATIONS.items():
            if table_name not in existing_tables:
                continue

            existing_cols = {col["name"] for col in inspector.get_columns(table_name)}

            for col in column_specs:
                if col.name not in existing_cols:
                    alter_stmt = (
                        f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col.sql_type} {col.default_clause}"
                    )
                    logger.info("Applying migration: %s", alter_stmt)
                    conn.execute(text(alter_stmt))
                    applied_actions.append(f"{table_name}.{col.name}")
                    existing_cols.add(col.name)

        conn.commit()

    if applied_actions:
        logger.info("Applied database migrations: %s", ", ".join(applied_actions))
    else:
        logger.debug("Database schema is up to date; no migrations needed.")

    return applied_actions


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Ensure backend root is on sys.path
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from app.database import engine

    actions = run_migrations(engine)
    if actions:
        print(f"Applied migrations for columns: {', '.join(actions)}")
    else:
        print("Database schema is already up to date; no migrations required.")
