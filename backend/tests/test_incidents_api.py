from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Assignment, Earthquake, Incident, PlanningRun, RescueTeam


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


def valid_incident_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Building collapse",
        "description": "Multiple people may be trapped.",
        "severity": 4,
        "people_affected": 6,
        "urgency": 5,
        "evidence_strength": 3,
        "latitude": 10.0,
        "longitude": 20.0,
    }
    payload.update(overrides)
    return payload


def test_incident_crud_endpoints(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/incidents", json=valid_incident_payload()
    )

    assert create_response.status_code == 201
    created = create_response.json()
    incident_id = created["id"]
    assert created["status"] == "reported"
    assert created["earthquake_id"] is None
    assert created["priority_score"] is None
    assert created["priority_level"] is None

    list_response = client.get("/api/v1/incidents")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [incident_id]

    get_response = client.get(f"/api/v1/incidents/{incident_id}")
    assert get_response.status_code == 200
    assert get_response.json()["title"] == "Building collapse"

    update_response = client.put(
        f"/api/v1/incidents/{incident_id}",
        json=valid_incident_payload(
            title="Updated collapse",
            status="verified",
            people_affected=8,
        ),
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["title"] == "Updated collapse"
    assert updated["status"] == "verified"
    assert updated["people_affected"] == 8
    assert updated["priority_score"] is None

    delete_response = client.delete(f"/api/v1/incidents/{incident_id}")
    assert delete_response.status_code == 204
    assert delete_response.content == b""
    assert client.get(f"/api/v1/incidents/{incident_id}").status_code == 404


def test_incident_create_accepts_existing_earthquake(
    client: TestClient, test_db_session: Session
) -> None:
    earthquake = Earthquake(
        magnitude=5.0,
        location_name="Test location",
        latitude=10.0,
        longitude=20.0,
        occurred_at=datetime.now(timezone.utc),
    )
    test_db_session.add(earthquake)
    test_db_session.commit()

    response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(earthquake_id=earthquake.id),
    )

    assert response.status_code == 201
    assert response.json()["earthquake_id"] == earthquake.id


def test_incident_create_rejects_missing_earthquake_reference(
    client: TestClient, test_db_session: Session
) -> None:
    response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(earthquake_id=999),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Earthquake not found"
    assert test_db_session.scalar(select(func.count()).select_from(Incident)) == 0
    assert test_db_session.scalar(select(1)) == 1


@pytest.mark.parametrize(
    "priority_field", ["priority_score", "priority_level"]
)
def test_incident_create_rejects_client_priority_fields(
    client: TestClient, priority_field: str
) -> None:
    response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(**{priority_field: 50}),
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "invalid_values",
    [
        {"severity": 6},
        {"people_affected": -1},
        {"urgency": 0},
        {"evidence_strength": 6},
        {"status": "acknowledged"},
        {"title": ""},
    ],
)
def test_incident_create_rejects_invalid_payloads(
    client: TestClient, invalid_values: dict[str, object]
) -> None:
    response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(**invalid_values),
    )

    assert response.status_code == 422


@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_incident_endpoints_return_404_for_missing_ids(
    client: TestClient, method: str
) -> None:
    url = "/api/v1/incidents/999"
    if method == "put":
        response = client.put(url, json=valid_incident_payload())
    else:
        response = getattr(client, method)(url)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("inputs", "expected_score", "expected_level"),
    [
        ({"severity": 5, "urgency": 2, "evidence_strength": 1}, 66, "High"),
        ({"severity": 4, "urgency": 4, "evidence_strength": 4}, 80, "Critical"),
        ({"severity": 3, "urgency": 3, "evidence_strength": 3}, 60, "High"),
        ({"severity": 2, "urgency": 2, "evidence_strength": 2}, 40, "Medium"),
        ({"severity": 1, "urgency": 1, "evidence_strength": 1}, 20, "Low"),
    ],
)
def test_calculate_priority_formula_and_categories(
    client: TestClient,
    inputs: dict[str, int],
    expected_score: int,
    expected_level: str,
) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(**inputs),
    )
    incident_id = create_response.json()["id"]

    response = client.post(f"/api/v1/incidents/{incident_id}/calculate-priority")

    assert response.status_code == 200
    result = response.json()
    assert result["priority_score"] == expected_score
    assert result["priority_level"] == expected_level


@pytest.mark.parametrize("missing_field", ["urgency", "evidence_strength"])
def test_calculate_priority_rejects_missing_inputs(
    client: TestClient, missing_field: str
) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(**{missing_field: None}),
    )
    incident_id = create_response.json()["id"]

    response = client.post(f"/api/v1/incidents/{incident_id}/calculate-priority")

    assert response.status_code == 422
    assert missing_field in response.json()["detail"]


def test_calculate_priority_returns_404_for_missing_incident(
    client: TestClient,
) -> None:
    response = client.post("/api/v1/incidents/999/calculate-priority")

    assert response.status_code == 404


def test_calculate_priority_persists_values(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(
            severity=5,
            urgency=4,
            evidence_strength=3,
        ),
    )
    incident_id = create_response.json()["id"]

    calculation_response = client.post(
        f"/api/v1/incidents/{incident_id}/calculate-priority"
    )
    retrieved_response = client.get(f"/api/v1/incidents/{incident_id}")

    assert calculation_response.status_code == 200
    assert retrieved_response.status_code == 200
    assert retrieved_response.json()["priority_score"] == 86
    assert retrieved_response.json()["priority_level"] == "Critical"


@pytest.mark.parametrize(
    ("field", "value", "expected_score"),
    [
        ("severity", 5, 90),
        ("urgency", 5, 86),
        ("evidence_strength", 5, 84),
    ],
)
def test_incident_update_recalculates_priority_when_inputs_change(
    client: TestClient,
    field: str,
    value: int,
    expected_score: int,
) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(
            severity=4,
            urgency=4,
            evidence_strength=4,
        ),
    )
    incident_id = create_response.json()["id"]
    assert client.post(
        f"/api/v1/incidents/{incident_id}/calculate-priority"
    ).status_code == 200

    update_values = {
        "severity": 4,
        "urgency": 4,
        "evidence_strength": 4,
    }
    update_values[field] = value
    response = client.put(
        f"/api/v1/incidents/{incident_id}",
        json=valid_incident_payload(**update_values),
    )

    assert response.status_code == 200
    assert response.json()["priority_score"] == expected_score
    assert response.json()["priority_level"] == "Critical"


def test_incident_update_clears_priority_when_input_becomes_missing(
    client: TestClient,
) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(),
    )
    incident_id = create_response.json()["id"]
    assert client.post(
        f"/api/v1/incidents/{incident_id}/calculate-priority"
    ).status_code == 200

    response = client.put(
        f"/api/v1/incidents/{incident_id}",
        json=valid_incident_payload(urgency=None),
    )

    assert response.status_code == 200
    assert response.json()["priority_score"] is None
    assert response.json()["priority_level"] is None


def test_incident_update_preserves_priority_for_unrelated_fields(
    client: TestClient,
) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(),
    )
    incident_id = create_response.json()["id"]
    assert client.post(
        f"/api/v1/incidents/{incident_id}/calculate-priority"
    ).status_code == 200

    response = client.put(
        f"/api/v1/incidents/{incident_id}",
        json=valid_incident_payload(title="Updated title", status="verified"),
    )

    assert response.status_code == 200
    assert response.json()["priority_score"] == 82
    assert response.json()["priority_level"] == "Critical"


def test_incident_rankings_order_scored_before_unscored(client: TestClient) -> None:
    incidents = [
        {"severity": 1, "urgency": 1, "evidence_strength": 1},
        {"severity": 4, "urgency": 4, "evidence_strength": 4},
        {"severity": 3, "urgency": 3, "evidence_strength": 3},
        {"severity": 4, "urgency": 4, "evidence_strength": 4},
        {"severity": 2, "urgency": None, "evidence_strength": 2},
    ]
    incident_ids = []
    for inputs in incidents:
        create_response = client.post(
            "/api/v1/incidents",
            json=valid_incident_payload(**inputs),
        )
        incident_ids.append(create_response.json()["id"])
        if inputs["urgency"] is not None:
            response = client.post(
                f"/api/v1/incidents/{incident_ids[-1]}/calculate-priority"
            )
            assert response.status_code == 200

    response = client.get("/api/v1/incidents/rankings")

    assert response.status_code == 200
    ranked_incidents = response.json()
    assert [incident["id"] for incident in ranked_incidents] == [
        incident_ids[1],
        incident_ids[3],
        incident_ids[2],
        incident_ids[0],
        incident_ids[4],
    ]
    assert [incident["priority_score"] for incident in ranked_incidents] == [
        80,
        80,
        60,
        20,
        None,
    ]


def test_incident_delete_rejects_records_with_assignment_history(
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

    response = client.delete(f"/api/v1/incidents/{incident.id}")

    assert response.status_code == 409
    assert "assignment history" in response.json()["detail"]
    assert test_db_session.get(Incident, incident.id) is not None
    assert test_db_session.get(Assignment, assignment.id) is not None


def test_incident_delete_rolls_back_integrity_error(
    client: TestClient,
    test_db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_response = client.post(
        "/api/v1/incidents",
        json=valid_incident_payload(),
    )
    incident_id = create_response.json()["id"]

    def fail_commit() -> None:
        raise IntegrityError("DELETE", {}, RuntimeError("database detail"))

    monkeypatch.setattr(test_db_session, "commit", fail_commit)
    response = client.delete(f"/api/v1/incidents/{incident_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Cannot delete incident because it has assignment history"
    )
    assert "database detail" not in response.text
    assert test_db_session.scalar(select(1)) == 1
    assert test_db_session.get(Incident, incident_id) is not None
