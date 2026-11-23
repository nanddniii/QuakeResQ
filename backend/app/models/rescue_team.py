"""Rescue team records."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RescueTeam(Base):
    __tablename__ = "rescue_teams"
    __table_args__ = (
        CheckConstraint(
            "status IN ('available', 'assigned', 'deployed', 'unavailable', 'off_duty')",
            name="ck_rescue_teams_status",
        ),
        CheckConstraint("capacity >= 1", name="ck_rescue_teams_capacity_positive"),
        CheckConstraint(
            "members_count IS NULL OR members_count >= 1",
            name="ck_rescue_teams_members_count_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    team_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="available", nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    members_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    assignments: Mapped[list[Assignment]] = relationship(back_populates="rescue_team")
