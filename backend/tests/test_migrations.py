"""Tests for database migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.migrations import TABLE_MIGRATIONS, run_migrations
from app.models.incident import Incident
from app.models.rescue_team import RescueTeam
from app.models.road_closure import RoadClosure


def create_legacy_schema(conn: sqlite3.Connection) -> None:
    """Create older table schemas missing the new Member 2 priority and capacity columns."""
    cursor = conn.cursor()
    cursor.executescript(
        """
        CREATE TABLE earthquakes (
            id INTEGER NOT NULL PRIMARY KEY,
            external_id VARCHAR(100) UNIQUE,
            magnitude FLOAT NOT NULL,
            location_name VARCHAR(200) NOT NULL,
            latitude FLOAT NOT NULL,
            longitude FLOAT NOT NULL,
            depth_km FLOAT,
            occurred_at DATETIME NOT NULL,
            status VARCHAR(30) NOT NULL
        );

        CREATE TABLE incidents (
            id INTEGER NOT NULL PRIMARY KEY,
            earthquake_id INTEGER,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            status VARCHAR(30) NOT NULL,
            latitude FLOAT NOT NULL,
            longitude FLOAT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(earthquake_id) REFERENCES earthquakes (id)
        );

        CREATE TABLE rescue_teams (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(150) NOT NULL,
            team_type VARCHAR(80) NOT NULL,
            status VARCHAR(30) NOT NULL,
            capacity INTEGER NOT NULL,
            latitude FLOAT NOT NULL,
            longitude FLOAT NOT NULL
        );

        CREATE TABLE road_closures (
            id INTEGER NOT NULL PRIMARY KEY,
            road_name VARCHAR(200) NOT NULL,
            start_node_id VARCHAR(100) NOT NULL,
            end_node_id VARCHAR(100) NOT NULL,
            edge_id VARCHAR(100) NOT NULL,
            reason TEXT,
            status VARCHAR(30) NOT NULL,
            latitude FLOAT,
            longitude FLOAT,
            created_at DATETIME NOT NULL
        );
        """
    )
    # Seed legacy records
    cursor.execute(
        """
        INSERT INTO incidents (id, title, description, status, latitude, longitude, created_at)
        VALUES (1, 'Legacy Incident', 'Debris blocking road', 'reported', 12.9716, 77.5946, '2026-11-20 10:00:00');
        """
    )
    cursor.execute(
        """
        INSERT INTO rescue_teams (id, name, team_type, status, capacity, latitude, longitude)
        VALUES (1, 'Alpha Team', 'Medical', 'available', 2, 12.9700, 77.5900);
        """
    )
    cursor.execute(
        """
        INSERT INTO road_closures (id, road_name, start_node_id, end_node_id, edge_id, reason, status, latitude, longitude, created_at)
        VALUES (1, 'Main St', '101', '102', '0', 'Tree fall', 'active', 12.9710, 77.5920, '2026-11-20 10:05:00');
        """
    )
    conn.commit()


def test_migration_adds_missing_columns_to_legacy_database(tmp_path: Path) -> None:
    """Test that run_migrations adds missing columns to an existing older database."""
    db_file = tmp_path / "test_legacy.db"
    raw_conn = sqlite3.connect(str(db_file))
    create_legacy_schema(raw_conn)
    raw_conn.close()

    engine = create_engine(f"sqlite:///{db_file}")

    # Run migration on legacy database
    applied = run_migrations(engine)

    assert "incidents.severity" in applied
    assert "incidents.people_affected" in applied
    assert "incidents.urgency" in applied
    assert "incidents.evidence_strength" in applied
    assert "incidents.priority_score" in applied
    assert "incidents.priority_level" in applied
    assert "rescue_teams.members_count" in applied

    # Verify columns exist via PRAGMA
    with engine.connect() as conn:
        cols_incidents = {row[1] for row in conn.execute(text("PRAGMA table_info('incidents');")).fetchall()}
        cols_teams = {row[1] for row in conn.execute(text("PRAGMA table_info('rescue_teams');")).fetchall()}

    for col_spec in TABLE_MIGRATIONS["incidents"]:
        assert col_spec.name in cols_incidents
    for col_spec in TABLE_MIGRATIONS["rescue_teams"]:
        assert col_spec.name in cols_teams

    # Verify existing records were preserved
    with Session(engine) as session:
        incident = session.get(Incident, 1)
        assert incident is not None
        assert incident.title == "Legacy Incident"
        assert incident.status == "reported"
        assert incident.severity is None
        assert incident.priority_score is None

        team = session.get(RescueTeam, 1)
        assert team is not None
        assert team.name == "Alpha Team"
        assert team.members_count is None

        closure = session.get(RoadClosure, 1)
        assert closure is not None
        assert closure.road_name == "Main St"
        assert closure.status == "active"

        # Verify SQLAlchemy select(Incident) query executes without error
        incidents = session.scalars(select(Incident)).all()
        assert len(incidents) == 1
        assert incidents[0].id == 1

    engine.dispose()


def test_migration_is_idempotent_when_run_multiple_times(tmp_path: Path) -> None:
    """Test that running migrations more than once is safe and makes no changes."""
    db_file = tmp_path / "test_idempotent.db"
    raw_conn = sqlite3.connect(str(db_file))
    create_legacy_schema(raw_conn)
    raw_conn.close()

    engine = create_engine(f"sqlite:///{db_file}")

    # First run: should apply migrations
    first_run_actions = run_migrations(engine)
    assert len(first_run_actions) > 0

    # Second run: should be a no-op
    second_run_actions = run_migrations(engine)
    assert second_run_actions == []

    # Third run: still a no-op
    third_run_actions = run_migrations(engine)
    assert third_run_actions == []

    # Records are still intact
    with Session(engine) as session:
        assert session.scalar(select(Incident.title).where(Incident.id == 1)) == "Legacy Incident"

    engine.dispose()
