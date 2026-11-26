"""Automated tests for dynamic replanning, safe preemption, and road-closure rerouting."""

import networkx as nx
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Assignment, Incident, PlanningRun, RescueTeam
from app.services.graph_service import GraphService
from app.services.replanning_service import (
    recalculate_active_routes,
    replan_incidents,
)
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


def create_mock_grid_graph():
    """
    Creates a simple multi-node grid for testing routing and closures:
    Node 1 (10.0, 20.0) -> Node 2 (10.01, 20.0) -> Node 3 (10.02, 20.0) [Main street]
    Node 1 (10.0, 20.0) -> Node 4 (10.0, 20.01) -> Node 3 (10.02, 20.0) [Detour route]
    Node 10 (30.0, 40.0) [Disconnected island]
    """
    g = nx.MultiDiGraph()
    g.add_node(1, y=10.0, x=20.0)
    g.add_node(2, y=10.01, x=20.0)
    g.add_node(3, y=10.02, x=20.0)
    g.add_node(4, y=10.0, x=20.01)
    g.add_node(10, y=30.0, x=40.0)

    # Primary path: 1 -> 2 -> 3 (total distance 200m, time 1.0 min)
    g.add_edge(1, 2, key=0, length=100.0, speed_kph=40.0, travel_time_minutes=0.5)
    g.add_edge(2, 3, key=0, length=100.0, speed_kph=40.0, travel_time_minutes=0.5)

    # Secondary detour path: 1 -> 4 -> 3 (total distance 400m, time 2.0 min)
    g.add_edge(1, 4, key=0, length=200.0, speed_kph=40.0, travel_time_minutes=1.0)
    g.add_edge(4, 3, key=0, length=200.0, speed_kph=40.0, travel_time_minutes=1.0)

    gs = GraphService()
    gs.set_graph(g)
    return gs


def test_replan_justified_preemption_for_critical_incident(db_session: Session):
    """
    A critical incident (score 95) justifies preempting a team assigned to a low-priority
    incident (score 40) when all teams are busy and the team has a valid route.
    """
    init_plan = PlanningRun(status="completed")
    db_session.add(init_plan)
    db_session.flush()

    # Team has capacity 1 and is currently assigned to a low priority incident
    team = RescueTeam(
        name="Paramedic Bravo",
        team_type="Medical",
        status="assigned",
        capacity=1,
        members_count=4,
        latitude=10.0,
        longitude=20.0,
    )
    inc_low = Incident(
        title="Minor Fence Collapse",
        severity=2,
        people_affected=1,
        priority_score=40.0,
        priority_level="Medium",
        status="assigned",
        latitude=10.01,
        longitude=20.0,
    )
    inc_critical = Incident(
        title="Catastrophic Hospital Collapse",
        severity=5,
        people_affected=50,
        priority_score=95.0,
        priority_level="Critical",
        status="reported",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, inc_low, inc_critical])
    db_session.flush()

    prior_assignment = Assignment(
        planning_id=init_plan.id,
        incident_id=inc_low.id,
        rescue_team_id=team.id,
        status="assigned",
    )
    db_session.add(prior_assignment)
    db_session.commit()

    gs = create_mock_grid_graph()
    rs = RoutingService(gs)

    replan_resp = replan_incidents(db_session, rs, allow_preemption=True)

    # 1. Critical incident receives new assignment
    assert len(replan_resp.new_assignments) == 1
    assert replan_resp.new_assignments[0].incident_id == inc_critical.id
    assert replan_resp.new_assignments[0].rescue_team_id == team.id
    assert replan_resp.new_assignments[0].is_reassignment is True

    # 2. Reassignment audit details recorded
    assert len(replan_resp.reassignments) == 1
    reassign_audit = replan_resp.reassignments[0]
    assert reassign_audit.preempted_incident_id == inc_low.id
    assert reassign_audit.new_incident_id == inc_critical.id
    assert "Priority upgrade" in reassign_audit.justification

    # 3. Old assignment status updated to 'reassigned'; old incident reverted to 'reported'
    db_session.refresh(prior_assignment)
    assert prior_assignment.status == "reassigned"

    db_session.refresh(inc_low)
    assert inc_low.status == "reported"

    db_session.refresh(inc_critical)
    assert inc_critical.status == "assigned"

    # 4. History preserved: exactly 2 assignment records in database
    total_assignments = db_session.scalar(select(func.count(Assignment.id)))
    assert total_assignments == 2


def test_replan_never_preempts_in_progress_operations(db_session: Session):
    """
    An assignment with status 'in_progress' (rescuers on scene) must NEVER be preempted,
    even if an arriving incident has a much higher priority.
    """
    init_plan = PlanningRun(status="completed")
    db_session.add(init_plan)
    db_session.flush()

    team = RescueTeam(
        name="Squad Alpha",
        team_type="USAR",
        status="assigned",
        capacity=1,
        members_count=6,
        latitude=10.0,
        longitude=20.0,
    )
    inc_active = Incident(
        title="Active Trench Rescue",
        severity=3,
        people_affected=2,
        priority_score=50.0,
        priority_level="Medium",
        status="in_progress",
        latitude=10.01,
        longitude=20.0,
    )
    inc_critical = Incident(
        title="Massive Factory Explosion",
        severity=5,
        people_affected=100,
        priority_score=98.0,
        priority_level="Critical",
        status="reported",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, inc_active, inc_critical])
    db_session.flush()

    # Crucial: status is 'in_progress'
    active_assignment = Assignment(
        planning_id=init_plan.id,
        incident_id=inc_active.id,
        rescue_team_id=team.id,
        status="in_progress",
    )
    db_session.add(active_assignment)
    db_session.commit()

    gs = create_mock_grid_graph()
    rs = RoutingService(gs)

    replan_resp = replan_incidents(db_session, rs, allow_preemption=True)

    # Preemption MUST NOT happen!
    assert replan_resp.reassignments == []
    assert len(replan_resp.unassigned_incidents) == 1
    assert replan_resp.unassigned_incidents[0].incident_id == inc_critical.id

    # in_progress assignment must remain completely undisturbed
    db_session.refresh(active_assignment)
    assert active_assignment.status == "in_progress"

    db_session.refresh(inc_active)
    assert inc_active.status == "in_progress"


def test_replan_rejects_unjustified_preemption_low_delta(db_session: Session):
    """
    Preemption is not permitted if the priority delta between incidents is negligible (< 10 points).
    """
    init_plan = PlanningRun(status="completed")
    db_session.add(init_plan)
    db_session.flush()

    team = RescueTeam(
        name="Team 1",
        team_type="Medical",
        status="assigned",
        capacity=1,
        latitude=10.0,
        longitude=20.0,
    )
    inc_existing = Incident(
        title="Gas Leak",
        severity=4,
        priority_score=75.0,
        status="assigned",
        latitude=10.01,
        longitude=20.0,
    )
    inc_new = Incident(
        title="Minor Structure Crack",
        severity=4,
        priority_score=78.0,  # Delta is only 3.0 (< 10.0)
        status="reported",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, inc_existing, inc_new])
    db_session.flush()

    prior_assign = Assignment(
        planning_id=init_plan.id,
        incident_id=inc_existing.id,
        rescue_team_id=team.id,
        status="assigned",
    )
    db_session.add(prior_assign)
    db_session.commit()

    gs = create_mock_grid_graph()
    rs = RoutingService(gs)

    replan_resp = replan_incidents(db_session, rs, allow_preemption=True)

    # Reassignment not allowed because delta is too small
    assert replan_resp.reassignments == []
    assert len(replan_resp.unassigned_incidents) == 1
    assert replan_resp.unassigned_incidents[0].incident_id == inc_new.id


def test_replan_rejects_preemption_if_route_unreachable(db_session: Session):
    """
    Preemption is not permitted if the team cannot reach the new critical incident.
    """
    init_plan = PlanningRun(status="completed")
    db_session.add(init_plan)
    db_session.flush()

    team = RescueTeam(
        name="Team Delta",
        team_type="USAR",
        status="assigned",
        capacity=1,
        latitude=10.0,
        longitude=20.0,
    )
    inc_assigned = Incident(
        title="Local Incident",
        severity=2,
        priority_score=35.0,
        status="assigned",
        latitude=10.01,
        longitude=20.0,
    )
    inc_unreachable = Incident(
        title="Critical Incident on Island",
        severity=5,
        priority_score=99.0,
        status="reported",
        latitude=30.0,  # Node 10 is isolated
        longitude=40.0,
    )
    db_session.add_all([team, inc_assigned, inc_unreachable])
    db_session.flush()

    assign = Assignment(
        planning_id=init_plan.id,
        incident_id=inc_assigned.id,
        rescue_team_id=team.id,
        status="assigned",
    )
    db_session.add(assign)
    db_session.commit()

    gs = create_mock_grid_graph()
    rs = RoutingService(gs)

    replan_resp = replan_incidents(db_session, rs, allow_preemption=True)

    # Cannot preempt team if team cannot physically reach the new incident
    assert replan_resp.reassignments == []
    assert len(replan_resp.unassigned_incidents) == 1
    assert replan_resp.unassigned_incidents[0].incident_id == inc_unreachable.id


def test_replan_recalculates_routes_and_handles_severed_road_closures(db_session: Session):
    """
    When a road closure severs the only route for an active assignment,
    recalculate_active_routes cancels the severed assignment and reverts incident to 'reported'.
    """
    init_plan = PlanningRun(status="completed")
    db_session.add(init_plan)
    db_session.flush()

    team = RescueTeam(
        name="Bravo",
        team_type="USAR",
        status="assigned",
        capacity=1,
        latitude=10.0,
        longitude=20.0,
    )
    incident = Incident(
        title="Main Street Emergency",
        severity=4,
        priority_score=80.0,
        status="assigned",
        latitude=10.02,
        longitude=20.0,
    )
    db_session.add_all([team, incident])
    db_session.flush()

    assign = Assignment(
        planning_id=init_plan.id,
        incident_id=incident.id,
        rescue_team_id=team.id,
        status="assigned",
    )
    db_session.add(assign)
    db_session.commit()

    gs = create_mock_grid_graph()
    g = gs.get_working_graph()

    # Simulate closing both primary route (1->2->3) and detour route (1->4->3)
    g.remove_edge(1, 2, key=0)
    g.remove_edge(1, 4, key=0)

    rs = RoutingService(gs)

    # Route is now severed
    details = recalculate_active_routes(db_session, rs)

    assert len(details) == 1
    assert details[0].status == "severed"
    assert "severed" in details[0].message

    # Assignment cancelled and incident reverted to reported
    db_session.refresh(assign)
    assert assign.status == "cancelled"

    db_session.refresh(incident)
    assert incident.status == "reported"
