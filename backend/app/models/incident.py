"""Rescue incidents associated with earthquake events."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reported', 'verified', 'assigned', 'in_progress', 'resolved', 'closed')",
            name="ck_incidents_status",
        ),
        CheckConstraint("severity BETWEEN 1 AND 5", name="ck_incidents_severity_range"),
        CheckConstraint("people_affected >= 0", name="ck_incidents_people_affected_nonnegative"),
        CheckConstraint("urgency BETWEEN 1 AND 5", name="ck_incidents_urgency_range"),
        CheckConstraint(
            "evidence_strength BETWEEN 1 AND 5",
            name="ck_incidents_evidence_strength_range",
        ),
        CheckConstraint(
            "priority_score BETWEEN 0 AND 100",
            name="ck_incidents_priority_score_range",
        ),
        CheckConstraint(
            "priority_level IS NULL OR priority_level IN ('Critical', 'High', 'Medium', 'Low')",
            name="ck_incidents_priority_level",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    earthquake_id: Mapped[int | None] = mapped_column(ForeignKey("earthquakes.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    people_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urgency: Mapped[int | None] = mapped_column(Integer)
    evidence_strength: Mapped[int | None] = mapped_column(Integer)
    priority_score: Mapped[float | None] = mapped_column(Float)
    priority_level: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="reported", nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    earthquake: Mapped[Earthquake | None] = relationship(back_populates="incidents")
    assignments: Mapped[list[Assignment]] = relationship(back_populates="incident")
