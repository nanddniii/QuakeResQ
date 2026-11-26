"""Unit tests for capacity-aware allocation and routable departure node snapping."""

import math
import networkx as nx
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Assignment, Incident, PlanningRun, RescueTeam
from app.services.allocation_service import allocate_incidents
from app.services.graph_service import GraphService
from app.services.routing_service import RoutingService


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _connection_record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def create_test_directed_graph():
    """
    Construct a directed graph:
    Node 1 (10.0, 20.0) -> Node 2 (10.01, 20.0) -> Node 3 (10.02, 20.0)
    Node 99 (10.0001, 20.0001) is a sink node with in_degree=1, out_degree=0.
    """
    g = nx.MultiDiGraph()
    g.add_node(1, y=10.0, x=20.0)
    g.add_node(2, y=10.01, x=20.0)
    g.add_node(3, y=10.02, x=20.0)
    g.add_node(99, y=10.0001, x=20.0001)  # Sink node

    # Add directed edges
    g.add_edge(1, 2, key=0, length=1000.0, speed_kph=40.0, travel_time_minutes=1.5)
    g.add_edge(2, 3, key=0, length=1000.0, speed_kph=40.0, travel_time_minutes=1.5)
    g.add_edge(1, 99, key=0, length=15.0, speed_kph=40.0, travel_time_minutes=0.1)  # Leads into sink

    gs = GraphService()
    gs.set_graph(g)
    return gs


def test_capacity_team_receives_multiple_assignments(db_session: Session):
    """A team with capacity 2 should receive two assignments if two incidents exist."""
    team = RescueTeam(
        name="Heavy USAR",
        team_type="Search & Rescue",
        status="available",
        capacity=2,
        members_count=10,
        latitude=10.0,
        longitude=20.0,
    )
    inc1 = Incident(
        title="High Priority Incident",
        severity=5,
        people_affected=20,
        urgency=5,
        evidence_strength=4,
        priority_score=96.0,
        priority_level="Critical",
        status="reported",
        latitude=10.01,
        longitude=20.0,
    )
    inc2 = Incident(
        title="Medium Priority Incident",
        severity=3,
        people_affected=5,
        urgency=3,
        evidence_strength=3,
        priority_score=60.0,
        priority_level="High",
        status="reported",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, inc1, inc2])
    db_session.commit()

    gs = create_test_directed_graph()
    rs = RoutingService(gs)

    response = allocate_incidents(db_session, rs)

    assert len(response.assignments) == 2
    assert response.assignments[0].incident_id == inc1.id
    assert response.assignments[0].rescue_team_id == team.id
    assert response.assignments[1].incident_id == inc2.id
    assert response.assignments[1].rescue_team_id == team.id
    assert response.unassigned_incidents == []

    # Team reached full capacity, so status is updated to assigned
    refreshed_team = db_session.get(RescueTeam, team.id)
    assert refreshed_team.status == "assigned"
    assert db_session.scalar(select(func.count()).select_from(Assignment)) == 2


def test_team_reaching_full_capacity_excluded_from_further_allocation(db_session: Session):
    """Once a team exhausts its capacity, remaining incidents cannot be assigned to it."""
    team = RescueTeam(
        name="Single Slot Team",
        team_type="Medical",
        status="available",
        capacity=1,
        members_count=4,
        latitude=10.0,
        longitude=20.0,
    )
    inc1 = Incident(
        title="First Incident",
        severity=5,
        people_affected=10,
        priority_score=90.0,
        status="reported",
        latitude=10.01,
        longitude=20.0,
    )
    inc2 = Incident(
        title="Second Incident",
        severity=4,
        people_affected=5,
        priority_score=70.0,
        status="reported",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, inc1, inc2])
    db_session.commit()

    gs = create_test_directed_graph()
    rs = RoutingService(gs)

    response = allocate_incidents(db_session, rs)

    assert len(response.assignments) == 1
    assert response.assignments[0].incident_id == inc1.id
    assert len(response.unassigned_incidents) == 1
    assert response.unassigned_incidents[0].incident_id == inc2.id
    assert "maximum capacity" in response.unassigned_incidents[0].reason


def test_already_assigned_incidents_not_in_unassigned(db_session: Session):
    """Incidents with active assignments must not appear in unassigned_incidents."""
    planning = PlanningRun(status="completed")
    db_session.add(planning)
    db_session.flush()

    team = RescueTeam(
        name="Team Alpha",
        team_type="USAR",
        status="available",
        capacity=2,
        members_count=8,
        latitude=10.0,
        longitude=20.0,
    )
    inc_assigned = Incident(
        title="Already Assigned Incident",
        severity=5,
        people_affected=10,
        priority_score=95.0,
        status="assigned",
        latitude=10.01,
        longitude=20.0,
    )
    inc_pending = Incident(
        title="Pending Incident",
        severity=4,
        people_affected=2,
        priority_score=75.0,
        status="reported",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, inc_assigned, inc_pending])
    db_session.flush()

    prior_assign = Assignment(
        planning_id=planning.id,
        incident_id=inc_assigned.id,
        rescue_team_id=team.id,
        status="assigned",
    )
    db_session.add(prior_assign)
    db_session.commit()

    gs = create_test_directed_graph()
    rs = RoutingService(gs)

    response = allocate_incidents(db_session, rs)

    # Only inc_pending should be allocated; inc_assigned must NOT be in unassigned_incidents!
    assert len(response.assignments) == 1
    assert response.assignments[0].incident_id == inc_pending.id
    assert response.unassigned_incidents == []
    assert "1 incident(s) already assigned" in response.summary


def test_no_duplicate_assignments_across_repeated_runs(db_session: Session):
    """Repeated allocation calls must not create duplicate assignments."""
    team = RescueTeam(
        name="Alpha",
        team_type="USAR",
        status="available",
        capacity=1,
        members_count=5,
        latitude=10.0,
        longitude=20.0,
    )
    inc = Incident(
        title="Sole Incident",
        severity=4,
        people_affected=3,
        priority_score=80.0,
        status="reported",
        latitude=10.01,
        longitude=20.0,
    )
    db_session.add_all([team, inc])
    db_session.commit()

    gs = create_test_directed_graph()
    rs = RoutingService(gs)

    # First run
    res1 = allocate_incidents(db_session, rs)
    assert len(res1.assignments) == 1
    total_assignments_first = db_session.scalar(select(func.count()).select_from(Assignment))
    assert total_assignments_first == 1

    # Second run immediately after
    res2 = allocate_incidents(db_session, rs)
    assert len(res2.assignments) == 0
    assert res2.unassigned_incidents == []
    total_assignments_second = db_session.scalar(select(func.count()).select_from(Assignment))
    assert total_assignments_second == 1  # No duplicate record created!


def test_departure_node_snapping_avoids_terminal_sink_nodes():
    """find_nearest_departure_node skips sink nodes (out_degree=0) and picks nearest routable node."""
    gs = create_test_directed_graph()
    g = gs.get_working_graph()

    # (10.0001, 20.0001) is closest to sink node 99 (out_degree=0)
    sink_node = gs.find_nearest_node(10.0001, 20.0001)
    assert sink_node == 99
    assert g.out_degree(99) == 0

    # find_nearest_departure_node must skip node 99 and pick node 1 or 2 (out_degree > 0)
    dep_node = gs.find_nearest_departure_node(10.0001, 20.0001, max_distance_meters=100.0)
    assert dep_node != 99
    assert g.out_degree(dep_node) > 0


def test_unreachable_incidents_reported_correctly(db_session: Session):
    """An incident in a disconnected graph component is properly marked unassigned with reason."""
    team = RescueTeam(
        name="Team",
        team_type="USAR",
        status="available",
        capacity=1,
        members_count=5,
        latitude=10.0,
        longitude=20.0,
    )
    # Incident at (30.0, 40.0) is disconnected
    inc = Incident(
        title="Island Incident",
        severity=4,
        people_affected=1,
        priority_score=70.0,
        status="reported",
        latitude=30.0,
        longitude=40.0,
    )
    db_session.add_all([team, inc])
    db_session.commit()

    g = nx.MultiDiGraph()
    g.add_node(1, y=10.0, x=20.0)
    g.add_node(2, y=10.01, x=20.0)
    g.add_edge(1, 2, key=0, length=100.0, speed_kph=40.0, travel_time_minutes=0.5)

    g.add_node(10, y=30.0, x=40.0)  # Isolated island

    gs = GraphService()
    gs.set_graph(g)
    rs = RoutingService(gs)

    response = allocate_incidents(db_session, rs)

    assert len(response.assignments) == 0
    assert len(response.unassigned_incidents) == 1
    assert response.unassigned_incidents[0].incident_id == inc.id
    assert "No remaining team has a reachable road route" in response.unassigned_incidents[0].reason


def test_bengaluru_team1_finds_routes_with_safe_departure_snapping():
    """Verify that Team 1 in Bengaluru finds routes to all incidents with departure node snapping."""
    import os
    cache_path = os.path.join("backend", "data", "graph_cache", "road_network.graphml")
    if not os.path.exists(cache_path):
        pytest.skip("Bengaluru graph cache not found")

    gs = GraphService(cache_dir="backend/data/graph_cache")
    gs.load_from_cache(cache_path)
    rs = RoutingService(gs)

    # Team 1 coordinates near BGS Flyover
    t1_lat, t1_lon = 12.9635, 77.5750

    # Incident 4 coordinates (Metro Collapse)
    i4_lat, i4_lon = 12.9755, 77.6060

    route = rs.find_route(t1_lat, t1_lon, i4_lat, i4_lon)
    assert route.status == "success"
    assert route.total_distance_km > 0
    assert route.estimated_time_minutes > 0
    assert len(route.route_coordinates) > 1
