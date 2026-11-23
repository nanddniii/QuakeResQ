"""CRUD endpoints for rescue incidents."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.assignment import Assignment
from app.models.earthquake import Earthquake
from app.models.incident import Incident
from app.schemas.entities import IncidentRequest, IncidentResponse

router = APIRouter(prefix="/incidents", tags=["Incidents"])

_PRIORITY_INPUT_FIELDS = ("severity", "urgency", "evidence_strength")


def _get_incident(db: Session, incident_id: int) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )
    return incident


def _calculate_priority(
    severity: int, urgency: int, evidence_strength: int
) -> tuple[float, str]:
    score = float(
        (
            Decimal(severity) * Decimal("0.5")
            + Decimal(urgency) * Decimal("0.3")
            + Decimal(evidence_strength) * Decimal("0.2")
        )
        * Decimal("20")
    )
    if score >= 80:
        level = "Critical"
    elif score >= 60:
        level = "High"
    elif score >= 40:
        level = "Medium"
    else:
        level = "Low"
    return score, level


@router.post(
    "",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_incident(payload: IncidentRequest, db: Session = Depends(get_db)) -> Incident:
    if (
        payload.earthquake_id is not None
        and db.get(Earthquake, payload.earthquake_id) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Earthquake not found",
        )
    incident = Incident(**payload.model_dump())
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


@router.get("", response_model=list[IncidentResponse])
def list_incidents(db: Session = Depends(get_db)) -> list[Incident]:
    statement = select(Incident).order_by(Incident.id)
    return list(db.scalars(statement).all())


@router.get("/rankings", response_model=list[IncidentResponse])
def get_incident_rankings(db: Session = Depends(get_db)) -> list[Incident]:
    statement = select(Incident).order_by(
        Incident.priority_score.is_(None).asc(),
        Incident.priority_score.desc(),
        Incident.id.asc(),
    )
    return list(db.scalars(statement).all())


@router.post("/{incident_id}/calculate-priority", response_model=IncidentResponse)
def calculate_incident_priority(
    incident_id: int,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_incident(db, incident_id)
    severity = incident.severity
    urgency = incident.urgency
    evidence_strength = incident.evidence_strength
    if severity is None or urgency is None or evidence_strength is None:
        missing_fields = [
            field
            for field, value in (
                ("severity", severity),
                ("urgency", urgency),
                ("evidence_strength", evidence_strength),
            )
            if value is None
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Cannot calculate priority; missing required inputs: "
                f"{', '.join(missing_fields)}"
            ),
        )

    priority_inputs = {
        "severity": severity,
        "urgency": urgency,
        "evidence_strength": evidence_strength,
    }
    invalid_fields = [
        field
        for field, value in priority_inputs.items()
        if not isinstance(value, int) or not 1 <= value <= 5
    ]
    if invalid_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Priority inputs must be integers from 1 to 5: "
                f"{', '.join(invalid_fields)}"
            ),
        )

    score, level = _calculate_priority(severity, urgency, evidence_strength)
    incident.priority_score = score
    incident.priority_level = level
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: int, db: Session = Depends(get_db)) -> Incident:
    return _get_incident(db, incident_id)


@router.put("/{incident_id}", response_model=IncidentResponse)
def update_incident(
    incident_id: int,
    payload: IncidentRequest,
    db: Session = Depends(get_db),
) -> Incident:
    incident = _get_incident(db, incident_id)
    values = payload.model_dump()
    priority_inputs_changed = any(
        getattr(incident, field) != values[field] for field in _PRIORITY_INPUT_FIELDS
    )
    for field, value in values.items():
        setattr(incident, field, value)
    if priority_inputs_changed:
        severity = incident.severity
        urgency = incident.urgency
        evidence_strength = incident.evidence_strength
        if (
            severity is not None
            and urgency is not None
            and evidence_strength is not None
        ):
            incident.priority_score, incident.priority_level = _calculate_priority(
                severity, urgency, evidence_strength
            )
        else:
            incident.priority_score = None
            incident.priority_level = None
    db.commit()
    db.refresh(incident)
    return incident


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_incident(incident_id: int, db: Session = Depends(get_db)) -> Response:
    incident = _get_incident(db, incident_id)
    has_assignment_history = db.scalar(
        select(Assignment.id)
        .where(Assignment.incident_id == incident_id)
        .limit(1)
    )
    if has_assignment_history is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete incident because it has assignment history",
        )
    db.delete(incident)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete incident because it has assignment history",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
