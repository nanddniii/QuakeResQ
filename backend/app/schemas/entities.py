"""Pydantic schemas for shared project entities."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EarthquakeStatus = Literal["active", "resolved", "cancelled"]
IncidentStatus = Literal[
    "reported", "acknowledged", "in_progress", "resolved", "closed"
]
RescueTeamStatus = Literal[
    "available", "assigned", "deployed", "unavailable", "off_duty"
]
RoadClosureStatus = Literal["active", "cleared", "scheduled"]
PlanningRunStatus = Literal["pending", "running", "completed", "failed"]
AssignmentStatus = Literal[
    "assigned", "accepted", "in_progress", "completed", "cancelled"
]


class EntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EarthquakeRequest(BaseModel):
    external_id: str | None = Field(default=None, max_length=100)
    magnitude: float = Field(ge=0)
    location_name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    depth_km: float | None = Field(default=None, ge=0)
    occurred_at: datetime
    status: EarthquakeStatus = "active"


class EarthquakeResponse(EntityResponse):
    id: int
    external_id: str | None = Field(max_length=100)
    magnitude: float
    location_name: str = Field(max_length=200)
    latitude: float
    longitude: float
    depth_km: float | None
    occurred_at: datetime
    status: EarthquakeStatus


class IncidentRequest(BaseModel):
    earthquake_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: IncidentStatus = "reported"
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class IncidentResponse(EntityResponse):
    id: int
    earthquake_id: int | None
    title: str = Field(max_length=200)
    description: str | None
    status: IncidentStatus
    latitude: float
    longitude: float
    created_at: datetime


class RescueTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    team_type: str = Field(min_length=1, max_length=80)
    status: RescueTeamStatus = "available"
    capacity: int = Field(default=1, ge=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RescueTeamResponse(EntityResponse):
    id: int
    name: str = Field(max_length=150)
    team_type: str = Field(max_length=80)
    status: RescueTeamStatus
    capacity: int
    latitude: float
    longitude: float


class RoadClosureRequest(BaseModel):
    road_name: str = Field(min_length=1, max_length=200)
    start_node_id: str = Field(min_length=1, max_length=100)
    end_node_id: str = Field(min_length=1, max_length=100)
    edge_id: str = Field(min_length=1, max_length=100)
    reason: str | None = None
    status: RoadClosureStatus = "active"
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class RoadClosureResponse(EntityResponse):
    id: int
    road_name: str = Field(max_length=200)
    start_node_id: str = Field(max_length=100)
    end_node_id: str = Field(max_length=100)
    edge_id: str = Field(max_length=100)
    reason: str | None
    status: RoadClosureStatus
    latitude: float | None
    longitude: float | None
    created_at: datetime


class PlanningRunRequest(BaseModel):
    status: PlanningRunStatus = "pending"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    summary: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_started_at(cls, value: object) -> object:
        if isinstance(value, dict) and "started_at" in value and value["started_at"] is None:
            raise ValueError("started_at cannot be null; omit it to use the default timestamp")
        return value

    def to_model_kwargs(self) -> dict[str, object]:
        """Omit unspecified fields so SQLAlchemy can apply model defaults."""
        return self.model_dump(exclude_unset=True)


class PlanningRunResponse(EntityResponse):
    id: int
    status: PlanningRunStatus
    started_at: datetime
    completed_at: datetime | None
    summary: str | None


class AssignmentRequest(BaseModel):
    planning_id: int
    incident_id: int
    rescue_team_id: int
    status: AssignmentStatus = "assigned"


class AssignmentResponse(EntityResponse):
    id: int
    planning_id: int
    incident_id: int
    rescue_team_id: int
    status: AssignmentStatus
    assigned_at: datetime
