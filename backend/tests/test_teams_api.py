from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Assignment, Incident, PlanningRun, RescueTeam


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
    original_override = app.dependency_overrides.get(get_db)

    def override_get_db() -> Iterator[Session]:
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)

    if original_override is None:
        app.dependency_overrides.pop(get_db, None)
    else:
        app.dependency_overrides[get_db] = original_override


def valid_team_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Team Alpha",
        "team_type": "Search and rescue",
        "members_count": 5,
        "latitude": 10.0,
        "longitude": 20.0,
    }
    payload.update(overrides)
    return payload


def test_team_crud_endpoints(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/teams",
        json=valid_team_payload(capacity=7),
    )

    assert create_response.status_code == 201
    created = create_response.json()
    team_id = created["id"]
    assert created["status"] == "available"
    assert created["capacity"] == 7
    assert created["members_count"] == 5

    list_response = client.get("/api/v1/teams")
    assert list_response.status_code == 200
    assert [team["id"] for team in list_response.json()] == [team_id]

    get_response = client.get(f"/api/v1/teams/{team_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Team Alpha"

    update_response = client.put(
        f"/api/v1/teams/{team_id}",
        json=valid_team_payload(
            name="Team Bravo",
            status="deployed",
            capacity=9,
            members_count=4,
        ),
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["name"] == "Team Bravo"
    assert updated["status"] == "deployed"
    assert updated["capacity"] == 9
    assert updated["members_count"] == 4

    delete_response = client.delete(f"/api/v1/teams/{team_id}")
    assert delete_response.status_code == 204
    assert delete_response.content == b""
    assert client.get(f"/api/v1/teams/{team_id}").status_code == 404


def test_team_create_preserves_default_capacity(client: TestClient) -> None:
    response = client.post("/api/v1/teams", json=valid_team_payload())

    assert response.status_code == 201
    assert response.json()["capacity"] == 1


@pytest.mark.parametrize(
    "team_status",
    ["available", "assigned", "deployed", "unavailable", "off_duty"],
)
def test_team_create_accepts_agreed_statuses(
    client: TestClient, team_status: str
) -> None:
    response = client.post(
        "/api/v1/teams",
        json=valid_team_payload(status=team_status),
    )

    assert response.status_code == 201
    assert response.json()["status"] == team_status


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"name": ""},
        {"team_type": ""},
        {"members_count": 0},
        {"members_count": None},
        {"capacity": 0},
        {"latitude": 91},
        {"status": "on_call"},
    ],
)
def test_team_create_rejects_invalid_payloads(
    client: TestClient, invalid_values: dict[str, object]
) -> None:
    response = client.post(
        "/api/v1/teams",
        json=valid_team_payload(**invalid_values),
    )

    assert response.status_code == 422


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_team_endpoints_return_404_for_missing_ids(
    client: TestClient, method: str
) -> None:
    url = "/api/v1/teams/999"
    if method == "put":
        response = client.put(url, json=valid_team_payload())
    else:
        response = getattr(client, method)(url)

    assert response.status_code == 404


def test_team_delete_rejects_records_with_assignment_history(
    client: TestClient,
    test_db_session: Session,
) -> None:
    planning_run = PlanningRun()
    incident = Incident(
        title="Assigned incident",
        severity=3,
        people_affected=1,
        latitude=10.0,
        longitude=20.0,
    )
    team = RescueTeam(
        name="Team Alpha",
        team_type="Search and rescue",
        members_count=2,
        latitude=10.0,
        longitude=20.0,
    )
    test_db_session.add_all([planning_run, incident, team])
    test_db_session.flush()
    assignment = Assignment(
        planning_id=planning_run.id,
        incident_id=incident.id,
        rescue_team_id=team.id,
        status="completed",
    )
    test_db_session.add(assignment)
    test_db_session.commit()

    response = client.delete(f"/api/v1/teams/{team.id}")

    assert response.status_code == 409
    assert "assignment history" in response.json()["detail"]
    assert test_db_session.get(RescueTeam, team.id) is not None
    assert test_db_session.get(Assignment, assignment.id) is not None


def test_team_delete_rolls_back_integrity_error(
    client: TestClient,
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_response = client.post(
        "/api/v1/teams",
        json=valid_team_payload(),
    )
    team_id = create_response.json()["id"]

    def fail_commit() -> None:
        raise IntegrityError("DELETE", {}, RuntimeError("database detail"))

    monkeypatch.setattr(test_db_session, "commit", fail_commit)
    response = client.delete(f"/api/v1/teams/{team_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Cannot delete rescue team because it has assignment history"
    )
    assert "database detail" not in response.text
    assert test_db_session.scalar(select(1)) == 1
    assert test_db_session.get(RescueTeam, team_id) is not None
