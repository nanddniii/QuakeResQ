"""CRUD endpoints for rescue teams."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.assignment import Assignment
from app.models.rescue_team import RescueTeam
from app.schemas.entities import RescueTeamRequest, RescueTeamResponse

router = APIRouter(prefix="/teams", tags=["Rescue Teams"])


def _get_team(db: Session, team_id: int) -> RescueTeam:
    team = db.get(RescueTeam, team_id)
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rescue team {team_id} not found",
        )
    return team


@router.post(
    "",
    response_model=RescueTeamResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_team(payload: RescueTeamRequest, db: Session = Depends(get_db)) -> RescueTeam:
    team = RescueTeam(**payload.model_dump())
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@router.get("", response_model=list[RescueTeamResponse])
def list_teams(db: Session = Depends(get_db)) -> list[RescueTeam]:
    statement = select(RescueTeam).order_by(RescueTeam.id)
    return list(db.scalars(statement).all())


@router.get("/{team_id}", response_model=RescueTeamResponse)
def get_team(team_id: int, db: Session = Depends(get_db)) -> RescueTeam:
    return _get_team(db, team_id)


@router.put("/{team_id}", response_model=RescueTeamResponse)
def update_team(
    team_id: int,
    payload: RescueTeamRequest,
    db: Session = Depends(get_db),
) -> RescueTeam:
    team = _get_team(db, team_id)
    for field, value in payload.model_dump().items():
        setattr(team, field, value)
    db.commit()
    db.refresh(team)
    return team


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
