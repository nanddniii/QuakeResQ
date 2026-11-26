from collections.abc import Callable, Iterator

import networkx as nx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_routing_service
from app.main import app
from app.models import Assignment, Incident, PlanningRun, RescueTeam
from app.schemas.entities import PlanningRunRequest
from app.services.graph_service import GraphService
from app.services.planning_persistence import PlanningPersistenceService
from app.services.routing_service import RoutingService


@pytest.fixture
def test_db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(test_db_session: Session) -> Iterator[TestClient]:
    original_db_override = app.dependency_overrides.get(get_db)
    original_routing_override = app.dependency_overrides.get(get_routing_service)

    def override_get_db() -> Iterator[Session]:
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)

    if original_db_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = original_db_override
    if original_routing_override is None:
        app.dependency_overrides.pop(get_routing_service, None)
    else:
        app.dependency_overrides[get_routing_service] = original_routing_override


@pytest.fixture
def use_test_graph() -> Iterator[Callable[[nx.MultiDiGraph | None], RoutingService]]:
    original_override = app.dependency_overrides.get(get_routing_service)

    def install(graph: nx.MultiDiGraph | None) -> RoutingService:
        graph_service = GraphService()
        if graph is not None:
            graph_service.set_graph(graph)
        routing_service = RoutingService(graph_service)
        app.dependency_overrides[get_routing_service] = lambda: routing_service
        return routing_service

    yield install

    if original_override is None:
        app.dependency_overrides.pop(get_routing_service, None)
    else:
        app.dependency_overrides[get_routing_service] = original_override


def add_incident(
    session: Session,
    title: str,
    latitude: float,
    longitude: float,
    priority_score: float | None = None,
    priority_level: str | None = None,
    status: str = "reported",
) -> Incident:
    incident = Incident(
        title=title,
        severity=3,
        people_affected=1,
        urgency=3,
        evidence_strength=3,
        priority_score=priority_score,
        priority_level=priority_level,
        status=status,
        latitude=latitude,
        longitude=longitude,
    )
    session.add(incident)
    session.flush()
    return incident


def add_team(
    session: Session,
    name: str,
    latitude: float,
    longitude: float,
    capacity: int = 1,
    members_count: int = 3,
    status: str = "available",
) -> RescueTeam:
    team = RescueTeam(
        name=name,
        team_type="Search and rescue",
        capacity=capacity,
        members_count=members_count,
        status=status,
        latitude=latitude,
        longitude=longitude,
    )
    session.add(team)
    session.flush()
    return team


def create_route_graph(
    team_locations: list[tuple[float, float]],
    incident_locations: list[tuple[float, float]],
    travel_times_seconds: list[list[float | None]],
    distances_meters: list[list[float | None]] | None = None,
) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    for index, (latitude, longitude) in enumerate(team_locations):
        graph.add_node(f"team-{index}", y=latitude, x=longitude)
    for index, (latitude, longitude) in enumerate(incident_locations):
        graph.add_node(f"incident-{index}", y=latitude, x=longitude)

    for team_index, times in enumerate(travel_times_seconds):
        for incident_index, travel_time in enumerate(times):
            if travel_time is None:
                continue
            distance = (
                distances_meters[team_index][incident_index]
                if distances_meters is not None
                else travel_time * 10
            )
            graph.add_edge(
                f"team-{team_index}",
                f"incident-{incident_index}",
                key=0,
                length=distance,
                travel_time=travel_time,
            )
    return graph


def test_allocation_prioritizes_incidents_and_uses_road_metrics(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    team_locations = [(10.0, 20.0), (10.0, 20.02), (10.0, 20.04)]
    incident_locations = [(10.02, 20.0), (10.02, 20.02), (10.02, 20.04)]
    teams = [
        add_team(test_db_session, f"Team {index}", *location)
        for index, location in enumerate(team_locations)
    ]
    low_priority = add_incident(
        test_db_session, "Low", *incident_locations[0], 40, "Medium"
    )
    unscored = add_incident(test_db_session, "Unscored", *incident_locations[1])
    high_priority = add_incident(
        test_db_session, "High", *incident_locations[2], 80, "Critical"
    )
    test_db_session.commit()

    graph = create_route_graph(
        team_locations,
        incident_locations,
        [[10, 10, 10], [20, 20, 20], [30, 30, 30]],
        [[100, 100, 100], [200, 200, 200], [300, 300, 300]],
    )
    use_test_graph(graph)

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 200
    result = response.json()
    allocations = result["assignments"]
    assert [item["incident_id"] for item in allocations] == [
        high_priority.id,
        low_priority.id,
        unscored.id,
    ]
    assert [item["rescue_team_id"] for item in allocations] == [
        team.id for team in teams
    ]
    assert [item["road_distance_km"] for item in allocations] == [0.1, 0.2, 0.3]
    assert [item["estimated_time_minutes"] for item in allocations] == [
        0.17,
        0.33,
        0.5,
    ]
    assert result["unassigned_incidents"] == []

    planning_run = test_db_session.get(PlanningRun, result["planning_run_id"])
    assert planning_run is not None
    assert planning_run.status == "completed"
    assert len(planning_run.assignments) == 3
    assert {item.status for item in planning_run.assignments} == {"assigned"}
    assert {item.status for item in planning_run.assignments[0].incident.assignments} == {
        "assigned"
    }
    assert all(team.status == "assigned" for team in teams)
    assert all(
        test_db_session.get(Incident, incident_id).status == "assigned"
        for incident_id in (high_priority.id, low_priority.id, unscored.id)
    )


def test_unreachable_routes_leave_incidents_unassigned(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    team_location = (10.0, 20.0)
    incident_location = (10.02, 20.0)
    team = add_team(test_db_session, "Team", *team_location)
    incident = add_incident(test_db_session, "Isolated", *incident_location)
    test_db_session.commit()
    use_test_graph(create_route_graph([team_location], [incident_location], [[None]]))

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 200
    result = response.json()
    assert result["assignments"] == []
    assert result["unassigned_incidents"] == [
        {
            "incident_id": incident.id,
            "title": "Isolated",
            "reason": "No remaining team has a reachable road route.",
        }
    ]
    assert test_db_session.scalar(select(func.count()).select_from(Assignment)) == 0
    assert test_db_session.get(Incident, incident.id).status == "reported"
    assert test_db_session.get(RescueTeam, team.id).status == "available"


def test_allocation_handles_no_available_teams(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    incident = add_incident(test_db_session, "Incident", 10.02, 20.0)
    add_team(
        test_db_session,
        "Unavailable team",
        10.0,
        20.0,
        status="unavailable",
    )
    test_db_session.commit()
    use_test_graph(None)

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 200
    result = response.json()
    assert result["assignments"] == []
    assert result["unassigned_incidents"][0]["incident_id"] == incident.id
    assert "No rescue teams" in result["unassigned_incidents"][0]["reason"]
    assert test_db_session.scalar(select(func.count()).select_from(PlanningRun)) == 1


def test_allocation_handles_no_eligible_incidents(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    incident = add_incident(
        test_db_session, "Resolved", 10.02, 20.0, status="resolved"
    )
    add_team(test_db_session, "Available team", 10.0, 20.0)
    test_db_session.commit()
    use_test_graph(None)

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 200
    result = response.json()
    assert result["summary"] == "No eligible incidents were found."
    assert result["assignments"] == []
    assert result["unassigned_incidents"] == []
    assert test_db_session.get(Incident, incident.id).status == "resolved"


def test_capacity_is_one_unit_per_incident_not_member_count(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    team_location = (10.0, 20.0)
    incident_locations = [(10.02, 20.0), (10.02, 20.02)]
    team = add_team(
        test_db_session,
        "Small capacity, large team",
        *team_location,
        capacity=1,
        members_count=20,
    )
    incidents = [
        add_incident(test_db_session, f"Incident {index}", *location)
        for index, location in enumerate(incident_locations)
    ]
    test_db_session.commit()
    use_test_graph(
        create_route_graph([team_location], incident_locations, [[10, 20]])
    )

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 200
    result = response.json()
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["rescue_team_id"] == team.id
    assert len(result["unassigned_incidents"]) == 1
    assert result["unassigned_incidents"][0]["incident_id"] == incidents[1].id
    assert test_db_session.get(RescueTeam, team.id).members_count == 20


def test_allocation_avoids_existing_active_assignments(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    team_locations = [(10.0, 20.0), (10.0, 20.02)]
    incident_locations = [(10.02, 20.0), (10.02, 20.02)]
    planning_run = PlanningRun()
    existing_incident = add_incident(
        test_db_session,
        "Already assigned",
        *incident_locations[0],
        status="assigned",
    )
    remaining_incident = add_incident(
        test_db_session,
        "Needs assignment",
        *incident_locations[1],
    )
    busy_team = add_team(test_db_session, "Busy team", *team_locations[0])
    free_team = add_team(test_db_session, "Free team", *team_locations[1])
    test_db_session.add(planning_run)
    test_db_session.flush()
    prior_assignment = Assignment(
        planning_id=planning_run.id,
        incident_id=existing_incident.id,
        rescue_team_id=busy_team.id,
        status="assigned",
    )
    test_db_session.add(prior_assignment)
    test_db_session.flush()
    prior_assignment_id = prior_assignment.id
    test_db_session.commit()
    use_test_graph(
        create_route_graph(
            team_locations,
            incident_locations,
            [[10, 10], [20, 20]],
        )
    )

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 200
    result = response.json()
    assert [item["incident_id"] for item in result["assignments"]] == [
        remaining_incident.id
    ]
    assert [item["rescue_team_id"] for item in result["assignments"]] == [
        free_team.id
    ]
    assert result["unassigned_incidents"] == []
    assert "already assigned" in result["summary"]
    assert test_db_session.scalar(select(func.count()).select_from(Assignment)) == 2
    assert test_db_session.get(Assignment, prior_assignment_id).status == "assigned"


def test_allocation_returns_service_unavailable_if_graph_is_unloaded(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
) -> None:
    incident = add_incident(test_db_session, "Incident", 10.02, 20.0)
    add_team(test_db_session, "Team", 10.0, 20.0)
    test_db_session.commit()
    use_test_graph(None)

    response = client.post("/api/v1/planning/allocate")

    assert response.status_code == 503
    assert "Road routing is unavailable" in response.json()["detail"]
    assert test_db_session.scalar(select(func.count()).select_from(PlanningRun)) == 0
    assert test_db_session.scalar(select(func.count()).select_from(Assignment)) == 0
    assert test_db_session.get(Incident, incident.id).status == "reported"


def test_allocation_rolls_back_run_assignments_and_statuses_on_failure(
    client: TestClient,
    test_db_session: Session,
    use_test_graph: Callable[[nx.MultiDiGraph | None], RoutingService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    team_location = (10.0, 20.0)
    incident_location = (10.02, 20.0)
    team = add_team(test_db_session, "Team", *team_location)
    incident = add_incident(test_db_session, "Incident", *incident_location)
    test_db_session.commit()
    use_test_graph(create_route_graph([team_location], [incident_location], [[10]]))

    original_update = PlanningPersistenceService.update_planning_run

    def fail_after_update(
        service: PlanningPersistenceService,
        planning_id: int,
        payload: PlanningRunRequest,
    ) -> PlanningRun:
        original_update(service, planning_id, payload)
        raise RuntimeError("forced allocation failure")

    monkeypatch.setattr(
        PlanningPersistenceService, "update_planning_run", fail_after_update
    )

    with pytest.raises(RuntimeError, match="forced allocation failure"):
        client.post("/api/v1/planning/allocate")

    assert test_db_session.scalar(select(func.count()).select_from(PlanningRun)) == 0
    assert test_db_session.scalar(select(func.count()).select_from(Assignment)) == 0
    assert test_db_session.get(Incident, incident.id).status == "reported"
    assert test_db_session.get(RescueTeam, team.id).status == "available"
