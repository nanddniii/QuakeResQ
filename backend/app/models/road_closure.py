"""Road closure records; the road graph itself remains in memory."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RoadClosure(Base):
    __tablename__ = "road_closures"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    road_name: Mapped[str] = mapped_column(String(200), nullable=False)
    start_node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    end_node_id: Mapped[str] = mapped_column(String(100), nullable=False)
    edge_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
