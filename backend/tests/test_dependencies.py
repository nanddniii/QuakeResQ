"""
Unit tests for app/dependencies.py safeguards.

Tests:
1. Missing cache handling (Safeguard 1: safe startup, no auto-download).
2. Idempotent closure replay from SQLite.
3. Transactional closure and reopening rollback on database failure (Safeguard 2).
4. Service singletons and version increments.
"""

from __future__ import annotations

import networkx as nx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.dependencies import (
    apply_safe_road_closure,
    apply_safe_road_reopen,
    get_closure_service,
    get_earthquake_service,
    get_graph_service,
    get_graph_version,
    get_routing_service,
    increment_graph_version,
    init_graph_service,
    replay_active_closures,
    reset_services_for_testing,
)
from app.models.road_closure import RoadClosure
from app.services.graph_service import GraphService


@pytest.fixture(autouse=True)
def clean_singletons() -> None:
    reset_services_for_testing()
    yield
    reset_services_for_testing()


@pytest.fixture
def test_db_session() -> Session:
    """In-memory SQLite database session for testing dependencies."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def synthetic_graph() -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    g.add_node(1, y=37.428, x=-122.145)
    g.add_node(2, y=37.430, x=-122.140)
    g.add_node(3, y=37.435, x=-122.135)
    g.add_edge(1, 2, key=0, length=100.0, speed_kph=40.0, travel_time=9.0, name="Road 1-2")
    g.add_edge(2, 1, key=0, length=100.0, speed_kph=40.0, travel_time=9.0, name="Road 1-2")
    g.add_edge(2, 3, key=0, length=200.0, speed_kph=40.0, travel_time=18.0, name="Road 2-3")
    return g


def test_missing_cache_startup_safeguard(tmp_path) -> None:
    """Safeguard 1: Missing cache does not crash and does not trigger network download."""
    nonexistent = tmp_path / "does_not_exist.graphml"
    gs = init_graph_service(cache_file=nonexistent, auto_load_cache=True)
    assert gs.is_loaded is False


def test_version_tracking() -> None:
    """Graph version increments monotonically and can be reset."""
    v0 = get_graph_version()
    assert v0 == "v1.0"
    v1 = increment_graph_version()
    assert v1 == "v1.1"


def test_dependency_providers() -> None:
    """Dependency providers return initialized singletons."""
    gs = get_graph_service()
    rs = get_routing_service(gs)
    cs = get_closure_service(gs)
    es = get_earthquake_service()

    assert gs is not None
    assert rs is not None
    assert cs is not None
    assert es is not None
    assert rs._graph_service is gs
    assert cs._graph_service is gs


def test_apply_safe_road_closure_success(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Closure updates both in-memory working graph and SQLite database."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    db_closures = apply_safe_road_closure(
        session=test_db_session,
        closure_service=cs,
        start_node=1,
        end_node=2,
        edge_key=0,
        both_directions=True,
        road_name="Road 1-2",
        reason="Earthquake fissuring",
        latitude=37.428,
        longitude=-122.145,
    )

    assert len(db_closures) == 2
    assert cs.is_closed(1, 2) is True
    assert cs.is_closed(2, 1) is True
    assert cs.active_closure_count == 2

    # Query from SQLite to ensure records committed
    saved = test_db_session.query(RoadClosure).all()
    assert len(saved) == 2
    assert all(c.status == "active" for c in saved)


def test_apply_safe_road_closure_rollback_on_db_failure(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph, monkeypatch
) -> None:
    """Safeguard 2: In-memory graph is rolled back if database commit fails."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    # Force database commit failure
    def mock_commit():
        raise RuntimeError("Simulated SQLite write error")

    monkeypatch.setattr(test_db_session, "commit", mock_commit)

    with pytest.raises(RuntimeError, match="Database error during closure"):
        apply_safe_road_closure(
            session=test_db_session,
            closure_service=cs,
            start_node=1,
            end_node=2,
            edge_key=0,
            both_directions=False,
            road_name="Road 1-2",
            reason="Test failure",
            latitude=None,
            longitude=None,
        )

    # Verify working graph was rolled back and road is STILL OPEN
    assert cs.is_closed(1, 2) is False
    assert cs.active_closure_count == 0
    assert gs.get_working_graph().has_edge(1, 2, key=0) is True


def test_apply_safe_road_reopen_success(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Reopening clears SQLite records and restores edges in working graph."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    # First close
    db_closures = apply_safe_road_closure(
        session=test_db_session,
        closure_service=cs,
        start_node=1,
        end_node=2,
        edge_key=0,
        both_directions=False,
        road_name="Road 1-2",
        reason="Debris",
        latitude=None,
        longitude=None,
    )
    assert cs.is_closed(1, 2) is True

    # Then reopen
    reopened = apply_safe_road_reopen(
        session=test_db_session,
        closure_service=cs,
        closure_id=db_closures[0].id,
    )
    assert len(reopened) == 1
    assert reopened[0].status == "cleared"
    assert cs.is_closed(1, 2) is False
    assert gs.get_working_graph().has_edge(1, 2, key=0) is True


def test_replay_active_closures_idempotent(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Replay from SQLite on startup applies active closures idempotently."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    # Seed SQLite with 2 active closures
    c1 = RoadClosure(
        road_name="Road 1-2",
        start_node_id="1",
        end_node_id="2",
        edge_id="0",
        reason="Blocked",
        status="active",
    )
    c2 = RoadClosure(
        road_name="Road 2-3",
        start_node_id="2",
        end_node_id="3",
        edge_id="0",
        reason="Blocked",
        status="active",
    )
    test_db_session.add_all([c1, c2])
    test_db_session.commit()

    # Replay
    count1 = replay_active_closures(test_db_session, cs)
    assert count1 == 2
    assert cs.is_closed(1, 2) is True
    assert cs.is_closed(2, 3) is True

    # Second replay (reboot simulation): should be idempotent
    count2 = replay_active_closures(test_db_session, cs)
    assert count2 == 0  # No duplicate applications
    assert cs.active_closure_count == 2


def test_reopen_already_cleared_closure_returns_empty(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Regression test: reopening an already-cleared closure must return [] and not process."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    # 1. Close road
    db_closures = apply_safe_road_closure(
        session=test_db_session,
        closure_service=cs,
        start_node=1,
        end_node=2,
        edge_key=0,
        both_directions=False,
        road_name="Road 1-2",
        reason="Test",
        latitude=None,
        longitude=None,
    )
    closure_id = db_closures[0].id

    # 2. First reopen: success
    reopened_first = apply_safe_road_reopen(
        session=test_db_session,
        closure_service=cs,
        closure_id=closure_id,
    )
    assert len(reopened_first) == 1
    assert reopened_first[0].status == "cleared"

    # 3. Second reopen on already-cleared record: must return empty list []
    reopened_second = apply_safe_road_reopen(
        session=test_db_session,
        closure_service=cs,
        closure_id=closure_id,
    )
    assert reopened_second == []
