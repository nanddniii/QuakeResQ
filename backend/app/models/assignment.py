"""Links a rescue team to an incident within a planning run."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Assignment(Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    planning_id: Mapped[int] = mapped_column(ForeignKey("planning_runs.id"), nullable=False)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), nullable=False)
    rescue_team_id: Mapped[int] = mapped_column(ForeignKey("rescue_teams.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="assigned", nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    planning_run: Mapped[PlanningRun] = relationship(back_populates="assignments")
    incident: Mapped[Incident] = relationship(back_populates="assignments")
    rescue_team: Mapped[RescueTeam] = relationship(back_populates="assignments")
