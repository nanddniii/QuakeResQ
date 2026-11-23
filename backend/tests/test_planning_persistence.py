from collections.abc import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.models import Assignment, Incident, PlanningRun, RescueTeam
from app.schemas.entities import AssignmentRequest, PlanningRunRequest
from app.services.planning_persistence import (
    PlanningPersistenceService,
    RecordNotFoundError,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def seed_references(
    factory: sessionmaker[Session], count: int = 1
) -> list[tuple[int, int, int]]:
    with factory() as session:
        references = []
        for index in range(count):
            planning_run = PlanningRun(summary=f"Planning run {index}")
            incident = Incident(
                title=f"Incident {index}",
                severity=3,
                people_affected=1,
                latitude=10.0,
                longitude=20.0,
            )
            rescue_team = RescueTeam(
                name=f"Team {index}",
                team_type="Search and rescue",
                members_count=2,
                latitude=10.0,
                longitude=20.0,
            )
            session.add_all([planning_run, incident, rescue_team])
            session.flush()
            references.append((planning_run.id, incident.id, rescue_team.id))
        session.commit()
        return references


def test_create_and_update_planning_run(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = PlanningPersistenceService(session)
        created = service.create_planning_run(
            PlanningRunRequest(summary="Initial plan")
        )
        planning_id = created.id

    with session_factory() as session:
        persisted = session.get(PlanningRun, planning_id)
        assert persisted is not None
        assert persisted.status == "pending"
        assert persisted.started_at is not None
        assert persisted.summary == "Initial plan"

    with session_factory() as session:
        updated = PlanningPersistenceService(session).update_planning_run(
            planning_id,
            PlanningRunRequest(status="running", summary="Revised plan"),
        )
        assert updated.status == "running"
        assert updated.summary == "Revised plan"

    with session_factory() as session:
        persisted = session.get(PlanningRun, planning_id)
        assert persisted is not None
        assert persisted.status == "running"
        assert persisted.summary == "Revised plan"


@pytest.mark.parametrize(
    "run_status", ["pending", "running", "completed", "failed"]
)
def test_planning_run_status_validation(run_status: str) -> None:
    assert PlanningRunRequest(status=run_status).status == run_status


def test_planning_run_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        PlanningRunRequest(status="queued")


def test_assignment_status_validation(
    session_factory: sessionmaker[Session],
) -> None:
    planning_id, incident_id, team_id = seed_references(session_factory)[0]
    for assignment_status in (
        "pending",
        "assigned",
        "in_progress",
        "completed",
        "cancelled",
        "failed",
    ):
        request = AssignmentRequest(
            planning_id=planning_id,
            incident_id=incident_id,
            rescue_team_id=team_id,
            status=assignment_status,
        )
        assert request.status == assignment_status

    with pytest.raises(ValidationError):
        AssignmentRequest(
            planning_id=planning_id,
            incident_id=incident_id,
            rescue_team_id=team_id,
            status="accepted",
        )


def test_create_and_update_assignment_relationships(
    session_factory: sessionmaker[Session],
) -> None:
    first_refs, second_refs = seed_references(session_factory, count=2)
    with session_factory() as session:
        service = PlanningPersistenceService(session)
        created = service.create_assignment(
            AssignmentRequest(
                planning_id=first_refs[0],
                incident_id=first_refs[1],
                rescue_team_id=first_refs[2],
                status="pending",
            )
        )
        assignment_id = created.id
        assert created.status == "pending"
        assert created.planning_run.id == first_refs[0]
        assert created.incident.id == first_refs[1]
        assert created.rescue_team.id == first_refs[2]

    with session_factory() as session:
        updated = PlanningPersistenceService(session).update_assignment(
            assignment_id,
            AssignmentRequest(
                planning_id=second_refs[0],
                incident_id=second_refs[1],
                rescue_team_id=second_refs[2],
                status="in_progress",
            ),
        )
        assert updated.status == "in_progress"
        assert updated.planning_run.id == second_refs[0]
        assert updated.incident.id == second_refs[1]
        assert updated.rescue_team.id == second_refs[2]

    with session_factory() as session:
        persisted = session.get(Assignment, assignment_id)
        assert persisted is not None
        assert persisted.status == "in_progress"
        assert persisted.planning_run.id == second_refs[0]
        assert persisted.incident.id == second_refs[1]
        assert persisted.rescue_team.id == second_refs[2]


@pytest.mark.parametrize("missing_reference", ["planning", "incident", "team"])
def test_assignment_create_rejects_missing_references_without_inserting(
    session_factory: sessionmaker[Session], missing_reference: str
) -> None:
    planning_id, incident_id, team_id = seed_references(session_factory)[0]
    payload_values = {
        "planning_id": planning_id,
        "incident_id": incident_id,
        "rescue_team_id": team_id,
    }
    if missing_reference == "planning":
        payload_values["planning_id"] = 999
    elif missing_reference == "incident":
        payload_values["incident_id"] = 999
    else:
        payload_values["rescue_team_id"] = 999

    with session_factory() as session:
        with pytest.raises(RecordNotFoundError):
            PlanningPersistenceService(session).create_assignment(
                AssignmentRequest(**payload_values)
            )
        assert session.scalar(select(func.count()).select_from(Assignment)) == 0


def test_failed_assignment_update_keeps_existing_record_unchanged(
    session_factory: sessionmaker[Session],
) -> None:
    planning_id, incident_id, team_id = seed_references(session_factory)[0]
    with session_factory() as session:
        assignment = PlanningPersistenceService(session).create_assignment(
            AssignmentRequest(
                planning_id=planning_id,
                incident_id=incident_id,
                rescue_team_id=team_id,
            )
        )
        assignment_id = assignment.id

    with session_factory() as session:
        with pytest.raises(RecordNotFoundError):
            PlanningPersistenceService(session).update_assignment(
                assignment_id,
                AssignmentRequest(
                    planning_id=planning_id,
                    incident_id=incident_id,
                    rescue_team_id=999,
                    status="completed",
                ),
            )

    with session_factory() as session:
        persisted = session.get(Assignment, assignment_id)
        assert persisted is not None
        assert persisted.planning_id == planning_id
        assert persisted.incident_id == incident_id
        assert persisted.rescue_team_id == team_id
        assert persisted.status == "assigned"


def test_missing_planning_run_update_raises_record_not_found(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        with pytest.raises(RecordNotFoundError):
            PlanningPersistenceService(session).update_planning_run(
                999, PlanningRunRequest(status="running")
            )
