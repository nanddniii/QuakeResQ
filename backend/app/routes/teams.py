"""CRUD endpoints for rescue teams."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.assignment import Assignment
from app.models.rescue_team import RescueTeam
from app.schemas.entities import RescueTeamRequest, RescueTeamResponse

router = APIRouter(prefix="/teams", tags=["Rescue Teams"])

ACTIVE_ASSIGNMENT_STATUSES = ("pending", "assigned", "in_progress")


def _get_team(db: Session, team_id: int) -> RescueTeam:
    team = db.get(RescueTeam, team_id)
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rescue team {team_id} not found",
        )
    return team


def _to_response(db: Session, team: RescueTeam) -> RescueTeamResponse:
    active_count = (
        db.scalar(
            select(func.count(Assignment.id)).where(
                Assignment.rescue_team_id == team.id,
                Assignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            )
        )
        or 0
    )
    remaining = max(0, team.capacity - active_count)
    resp = RescueTeamResponse.model_validate(team, from_attributes=True)
    resp.active_assignments_count = active_count
    resp.remaining_capacity = remaining
    return resp


@router.post(
    "",
    response_model=RescueTeamResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_team(payload: RescueTeamRequest, db: Session = Depends(get_db)) -> RescueTeamResponse:
    team = RescueTeam(**payload.model_dump())
    db.add(team)
    db.commit()
    db.refresh(team)
    return _to_response(db, team)


@router.get("", response_model=list[RescueTeamResponse])
def list_teams(db: Session = Depends(get_db)) -> list[RescueTeamResponse]:
    teams = list(db.scalars(select(RescueTeam).order_by(RescueTeam.id)).all())
    stmt = (
        select(Assignment.rescue_team_id, func.count(Assignment.id))
        .where(Assignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES))
        .group_by(Assignment.rescue_team_id)
    )
    workloads = dict(db.execute(stmt).all())
    result: list[RescueTeamResponse] = []
    for t in teams:
        active_count = workloads.get(t.id, 0)
        remaining = max(0, t.capacity - active_count)
        resp = RescueTeamResponse.model_validate(t, from_attributes=True)
        resp.active_assignments_count = active_count
        resp.remaining_capacity = remaining
        result.append(resp)
    return result


@router.get("/{team_id}", response_model=RescueTeamResponse)
def get_team(team_id: int, db: Session = Depends(get_db)) -> RescueTeamResponse:
    team = _get_team(db, team_id)
    return _to_response(db, team)


@router.put("/{team_id}", response_model=RescueTeamResponse)
def update_team(
    team_id: int,
    payload: RescueTeamRequest,
    db: Session = Depends(get_db),
) -> RescueTeamResponse:
    team = _get_team(db, team_id)
    for field, value in payload.model_dump().items():
        setattr(team, field, value)
    db.commit()
    db.refresh(team)
    return _to_response(db, team)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: int, db: Session = Depends(get_db)) -> Response:
    team = _get_team(db, team_id)
    has_assignment_history = db.scalar(
        select(Assignment.id)
        .where(Assignment.rescue_team_id == team_id)
        .limit(1)
    )
    if has_assignment_history is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete rescue team because it has assignment history",
        )
    db.delete(team)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete rescue team because it has assignment history",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
