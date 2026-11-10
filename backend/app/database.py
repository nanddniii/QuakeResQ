"""SQLite engine, SQLAlchemy base, and database session helpers."""

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import URL, create_engine, event, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_PATH = Path(__file__).resolve().parents[1] / "quake_resq.db"
DATABASE_URL = os.getenv("DATABASE_URL") or URL.create(
    "sqlite", database=str(DATABASE_PATH)
)

connect_args = (
    {"check_same_thread": False}
    if make_url(DATABASE_URL).get_backend_name() == "sqlite"
    else {}
)
engine = create_engine(DATABASE_URL, connect_args=connect_args)


if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    """Base class shared by all SQLAlchemy models."""


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Provide a database session to a FastAPI request."""
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()


def initialize_database() -> None:
    """Create all tables registered by the shared model package."""
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
