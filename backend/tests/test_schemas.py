from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Incident, PlanningRun, RescueTeam
from app.schemas.entities import (
    AssignmentRequest,
    IncidentRequest,
    IncidentResponse,
    PlanningRunRequest,
    RescueTeamRequest,
    RescueTeamResponse,
)


def valid_incident_request(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "title": "Building collapse",
        "severity": 3,
        "people_affected": 4,
        "latitude": 10.0,
        "longitude": 20.0,
    }
    values.update(overrides)
    return values


def valid_rescue_team_request(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "Team Alpha",
        "team_type": "Search and rescue",
        "members_count": 5,
        "latitude": 10.0,
        "longitude": 20.0,
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "status",
    ["reported", "verified", "assigned", "in_progress", "resolved", "closed"],
)
def test_incident_request_accepts_agreed_statuses(status: str) -> None:
    request = IncidentRequest(**valid_incident_request(status=status))

    assert request.status == status


@pytest.mark.parametrize("status", ["acknowledged", "pending", "invalid"])
def test_incident_request_rejects_invalid_statuses(status: str) -> None:
    with pytest.raises(ValidationError):
        IncidentRequest(**valid_incident_request(status=status))


@pytest.mark.parametrize("severity", [1, 5])
def test_incident_request_accepts_severity_boundaries(severity: int) -> None:
    request = IncidentRequest(**valid_incident_request(severity=severity))

    assert request.severity == severity


@pytest.mark.parametrize("severity", [0, 6])
def test_incident_request_rejects_out_of_range_severity(severity: int) -> None:
    with pytest.raises(ValidationError):
        IncidentRequest(**valid_incident_request(severity=severity))


@pytest.mark.parametrize("people_affected", [0, 2])
def test_incident_request_accepts_nonnegative_people_affected(people_affected: int) -> None:
    request = IncidentRequest(**valid_incident_request(people_affected=people_affected))

    assert request.people_affected == people_affected


def test_incident_request_rejects_negative_people_affected() -> None:
    with pytest.raises(ValidationError):
        IncidentRequest(**valid_incident_request(people_affected=-1))


@pytest.mark.parametrize(
    ("field", "value"),
    [("urgency", 0), ("urgency", 6), ("evidence_strength", 0), ("evidence_strength", 6)],
)
def test_incident_request_rejects_out_of_range_priority_inputs(
    field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        IncidentRequest(**valid_incident_request(**{field: value}))


def test_incident_request_allows_unset_priority_calculation_inputs() -> None:
    request = IncidentRequest(**valid_incident_request())

    assert request.urgency is None
    assert request.evidence_strength is None


@pytest.mark.parametrize("priority_field", ["priority_score", "priority_level"])
def test_incident_request_rejects_client_priority_fields(priority_field: str) -> None:
    with pytest.raises(ValidationError, match="cannot be supplied"):
        IncidentRequest(**valid_incident_request(**{priority_field: None}))


def test_incident_response_allows_nullable_priority_fields() -> None:
    response = IncidentResponse(
        id=1,
        earthquake_id=None,
        title="Building collapse",
        description=None,
        severity=None,
        people_affected=None,
        urgency=None,
        evidence_strength=None,
        priority_score=None,
        priority_level=None,
        status="reported",
        latitude=10.0,
        longitude=20.0,
        created_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
    )

    assert response.urgency is None
    assert response.evidence_strength is None
    assert response.priority_score is None
    assert response.priority_level is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("severity", 0),
        ("severity", 6),
        ("people_affected", -1),
        ("urgency", 0),
        ("urgency", 6),
        ("evidence_strength", 0),
        ("evidence_strength", 6),
        ("priority_score", -0.1),
        ("priority_score", 100.1),
    ],
)
def test_incident_response_rejects_out_of_range_values(field: str, value: float) -> None:
    values: dict[str, object] = {
        "id": 1,
        "earthquake_id": None,
        "title": "Building collapse",
        "description": None,
        "severity": 3,
        "people_affected": 4,
        "urgency": 3,
        "evidence_strength": 3,
        "priority_score": 50.0,
        "priority_level": "High",
        "status": "reported",
        "latitude": 10.0,
        "longitude": 20.0,
        "created_at": datetime(2026, 11, 10, tzinfo=timezone.utc),
    }
    values[field] = value

    with pytest.raises(ValidationError):
        IncidentResponse(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("severity", 0),
        ("severity", 6),
        ("people_affected", -1),
        ("urgency", 0),
        ("urgency", 6),
        ("evidence_strength", 0),
        ("evidence_strength", 6),
        ("priority_score", -0.1),
        ("priority_score", 100.1),
    ],
)
def test_incident_model_rejects_out_of_range_values(field: str, value: float) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    incident_values: dict[str, object] = {
        "title": "Building collapse",
        "severity": 3,
        "people_affected": 4,
        "urgency": 3,
        "evidence_strength": 3,
        "priority_score": 50.0,
        "priority_level": "High",
        "latitude": 10.0,
        "longitude": 20.0,
    }
    incident_values[field] = value

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(Incident(**incident_values))
            session.flush()


def test_incident_model_rejects_invalid_status() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(
                Incident(
                    title="Building collapse",
                    severity=3,
                    people_affected=4,
                    latitude=10.0,
                    longitude=20.0,
                    status="acknowledged",
                )
            )
            session.flush()


def test_rescue_team_request_requires_at_least_one_member() -> None:
    with pytest.raises(ValidationError):
        RescueTeamRequest(**valid_rescue_team_request(members_count=0))
    with pytest.raises(ValidationError):
        RescueTeamRequest(**valid_rescue_team_request(members_count=None))
    with pytest.raises(ValidationError):
        RescueTeamRequest(**valid_rescue_team_request(capacity=0))

    request = RescueTeamRequest(
        **valid_rescue_team_request(capacity=7, members_count=3)
    )
    assert request.members_count == 3
    assert request.capacity == 7


def test_rescue_team_response_preserves_capacity_and_members_count() -> None:
    response = RescueTeamResponse(
        id=1,
        name="Team Alpha",
        team_type="Search and rescue",
        status="available",
        capacity=7,
        members_count=3,
        latitude=10.0,
        longitude=20.0,
    )

    assert response.capacity == 7
    assert response.members_count == 3


@pytest.mark.parametrize(
    "status", ["available", "assigned", "deployed", "unavailable", "off_duty"]
)
def test_rescue_team_request_accepts_agreed_statuses(status: str) -> None:
    request = RescueTeamRequest(**valid_rescue_team_request(status=status))

    assert request.status == status


def test_rescue_team_request_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        RescueTeamRequest(**valid_rescue_team_request(status="on_call"))


@pytest.mark.parametrize("field", ["capacity", "members_count"])
def test_rescue_team_response_rejects_nonpositive_values(field: str) -> None:
    values: dict[str, object] = {
        "id": 1,
        "name": "Team Alpha",
        "team_type": "Search and rescue",
        "status": "available",
        "capacity": 7,
        "members_count": 3,
        "latitude": 10.0,
        "longitude": 20.0,
    }
    values[field] = 0

    with pytest.raises(ValidationError):
        RescueTeamResponse(**values)


@pytest.mark.parametrize("field", ["capacity", "members_count"])
def test_rescue_team_model_rejects_nonpositive_values(field: str) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    team_values: dict[str, object] = {
        "name": "Team Alpha",
        "team_type": "Search and rescue",
        "capacity": 7,
        "members_count": 3,
        "latitude": 10.0,
        "longitude": 20.0,
    }
    team_values[field] = 0

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(RescueTeam(**team_values))
            session.flush()


def test_rescue_team_model_rejects_invalid_status() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            session.add(
                RescueTeam(
                    name="Team Alpha",
                    team_type="Search and rescue",
                    members_count=3,
                    latitude=10.0,
                    longitude=20.0,
                    status="on_call",
                )
            )
            session.flush()


@pytest.mark.parametrize(
    "status",
    ["pending", "assigned", "in_progress", "completed", "cancelled", "failed"],
)
def test_assignment_request_accepts_agreed_statuses(status: str) -> None:
    request = AssignmentRequest(
        planning_id=1,
        incident_id=2,
        rescue_team_id=3,
        status=status,
    )

    assert request.status == status


def test_assignment_request_rejects_accepted_status() -> None:
    with pytest.raises(ValidationError):
        AssignmentRequest(
            planning_id=1,
            incident_id=2,
            rescue_team_id=3,
            status="accepted",
        )


def test_planning_run_omitted_started_at_uses_model_default() -> None:
    request = PlanningRunRequest(summary="Initial run")

    assert "started_at" not in request.to_model_kwargs()
    assert request.to_model_kwargs() == {"summary": "Initial run"}

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        planning_run = PlanningRun(**request.to_model_kwargs())
        session.add(planning_run)
        session.flush()

        assert planning_run.started_at is not None
        assert planning_run.summary == "Initial run"


def test_planning_run_explicit_null_started_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="started_at cannot be null"):
        PlanningRunRequest(started_at=None)


def test_planning_run_preserves_explicit_started_at() -> None:
    started_at = datetime(2026, 11, 10, tzinfo=timezone.utc)
    request = PlanningRunRequest(started_at=started_at, summary="Initial run")

    assert request.to_model_kwargs() == {
        "started_at": started_at,
        "summary": "Initial run",
    }
