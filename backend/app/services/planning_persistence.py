"""Persistence operations for planning runs and rescue assignments."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.models import Assignment, Incident, PlanningRun, RescueTeam
from app.schemas.entities import AssignmentRequest, PlanningRunRequest


class RecordNotFoundError(LookupError):
    """Raised when a planning record or referenced entity does not exist."""

    def __init__(self, entity: str, record_id: int) -> None:
        super().__init__(f"{entity} with id {record_id} does not exist")
        self.entity = entity
        self.record_id = record_id


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    """Join caller-owned transactions, or commit this operation when standalone."""
    if session.in_transaction():
        yield
    else:
        with session.begin():
            yield


class PlanningPersistenceService:
    """Create and update planning runs and assignments atomically."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_planning_run(self, payload: PlanningRunRequest) -> PlanningRun:
        with _transaction(self._session):
            planning_run = PlanningRun(**payload.to_model_kwargs())
            self._session.add(planning_run)
            self._session.flush()
        return planning_run

    def update_planning_run(
        self, planning_id: int, payload: PlanningRunRequest
    ) -> PlanningRun:
        with _transaction(self._session):
            planning_run = self._session.get(PlanningRun, planning_id)
            if planning_run is None:
                raise RecordNotFoundError("PlanningRun", planning_id)
            for field, value in payload.to_model_kwargs().items():
                setattr(planning_run, field, value)
            self._session.flush()
        return planning_run

    def create_assignment(self, payload: AssignmentRequest) -> Assignment:
        with _transaction(self._session):
            self._require_references(payload)
            assignment = Assignment(**payload.model_dump(exclude_unset=True))
            self._session.add(assignment)
            self._session.flush()
        return assignment

    def update_assignment(
        self, assignment_id: int, payload: AssignmentRequest
    ) -> Assignment:
        with _transaction(self._session):
            assignment = self._session.get(Assignment, assignment_id)
            if assignment is None:
                raise RecordNotFoundError("Assignment", assignment_id)
            self._require_references(payload)
            for field, value in payload.model_dump(exclude_unset=True).items():
                setattr(assignment, field, value)
            self._session.flush()
        return assignment

    def _require_references(self, payload: AssignmentRequest) -> None:
        references = (
            (PlanningRun, payload.planning_id, "PlanningRun"),
            (Incident, payload.incident_id, "Incident"),
            (RescueTeam, payload.rescue_team_id, "RescueTeam"),
        )
        for model, record_id, entity_name in references:
            if self._session.get(model, record_id) is None:
                raise RecordNotFoundError(entity_name, record_id)
