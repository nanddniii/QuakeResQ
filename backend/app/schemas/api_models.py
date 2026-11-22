"""
API-specific Pydantic schemas for QuakeResQ /api/v1 endpoints.

Defines request and response schemas for Road Network Graph, Routing,
Road Closures, and Earthquake events. All coordinate sequences strictly
follow the GeoJSON RFC 7946 standard: [longitude, latitude].
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Road Graph Schemas
# ---------------------------------------------------------------------------


class RoadGraphAreaConfig(BaseModel):
    """Configuration metadata for the geographic area loaded into the graph."""

    center_coordinates: list[float] | None = None  # [longitude, latitude]
    dist_meters: float = 1000.0
    network_type: str = "drive"
    place_name: str | None = None


class RoadGraphStatusResponse(BaseModel):
    """Status summary and topology metadata of the active road graph."""

    status: Literal["loaded", "unloaded"]
    graph_version: str = "v1.0"
    node_count: int = 0
    edge_count: int = 0
    is_directed: bool = True
    is_multigraph: bool = True
    active_closures_count: int = 0
    default_speed_kmh: float = 40.0
    travel_time_source: str = "osm_maxspeed_with_fallback_40kmh"
    area_config: RoadGraphAreaConfig | None = None


# ---------------------------------------------------------------------------
# Road Routing Schemas
# ---------------------------------------------------------------------------


class RouteRequest(BaseModel):
    """Request payload to calculate optimal route between two points."""

    start_lat: float = Field(ge=-90.0, le=90.0, description="Start latitude in degrees")
    start_lon: float = Field(ge=-180.0, le=180.0, description="Start longitude in degrees")
    dest_lat: float = Field(ge=-90.0, le=90.0, description="Destination latitude in degrees")
    dest_lon: float = Field(ge=-180.0, le=180.0, description="Destination longitude in degrees")
    weight: Literal["travel_time", "length"] = Field(
        default="travel_time", description="Primary routing metric"
    )
    algorithm: Literal["dijkstra"] = Field(default="dijkstra", description="Routing algorithm")


class RouteResponse(BaseModel):
    """
    Response schema for calculated route with full metadata and GeoJSON coordinates.

    Coordinates strictly follow GeoJSON [longitude, latitude] ordering.
    """

    status: Literal["success", "no_route"]
    distance_km: float = Field(ge=0.0, description="Total route distance in kilometers")
    estimated_time_minutes: float = Field(
        ge=0.0, description="Estimated travel time in minutes"
    )
    route_coordinates: list[list[float]] = Field(
        default_factory=list,
        description="Path node coordinates in GeoJSON [longitude, latitude] order",
    )
    calculated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of route calculation",
    )
    graph_version: str = Field(
        default="v1.0", description="Active graph version at calculation time"
    )
    travel_time_source: str = Field(
        default="osm_maxspeed_with_fallback_40kmh",
        description="Source of edge speed/travel time calculation",
    )
    start_node: Any | None = None
    destination_node: Any | None = None
    route_nodes: list[Any] = Field(
        default_factory=list, description="Ordered node IDs traversed"
    )
    algorithm: str = "dijkstra"
    message: str | None = None

    @field_validator("route_coordinates")
    @classmethod
    def validate_geojson_coordinate_pairs(
        cls, v: list[list[float]]
    ) -> list[list[float]]:
        """Ensure each coordinate is a 2D [longitude, latitude] pair."""
        for pt in v:
            if len(pt) < 2:
                raise ValueError(
                    f"Invalid GeoJSON coordinate {pt}: must contain at least [lon, lat]"
                )
            lon, lat = pt[0], pt[1]
            if not (-180.0 <= lon <= 180.0):
                raise ValueError(f"Longitude out of bounds [-180, 180]: {lon}")
            if not (-90.0 <= lat <= 90.0):
                raise ValueError(f"Latitude out of bounds [-90, 90]: {lat}")
        return v


# ---------------------------------------------------------------------------
# Road Closure Schemas
# ---------------------------------------------------------------------------


class RoadClosureCreateRequest(BaseModel):
    """Request payload to close a road segment."""

    start_node_id: str = Field(min_length=1, max_length=100)
    end_node_id: str = Field(min_length=1, max_length=100)
    edge_id: str | None = Field(
        default=None,
        max_length=100,
        description="Parallel edge key. If null, closes all edges between node pair.",
    )
    road_name: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)
    both_directions: bool = Field(
        default=False,
        description="If true, closes both forward and reverse edges for two-way roads.",
    )
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)


class RoadClosureItemResponse(BaseModel):
    """Representation of an active or historical road closure record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    road_name: str
    start_node_id: str
    end_node_id: str
    edge_id: str
    reason: str | None = None
    status: str = "active"
    coordinates: list[float] | None = None  # [longitude, latitude]
    created_at: datetime


class RoadClosureOperationResponse(BaseModel):
    """Response returned upon creating road closures."""

    status: Literal["success", "partial", "error"]
    closed_records: list[RoadClosureItemResponse] = Field(default_factory=list)
    graph_version: str = "v1.0"
    active_closures_count: int = 0
    message: str | None = None


class RoadReopenRequest(BaseModel):
    """Request payload to reopen a previously closed road segment."""

    closure_id: int | None = Field(
        default=None, description="Database primary key of the closure"
    )
    start_node_id: str | None = Field(
        default=None, description="Start node if reopening by nodes"
    )
    end_node_id: str | None = Field(
        default=None, description="End node if reopening by nodes"
    )
    edge_id: str | None = Field(
        default=None, description="Edge key if reopening specific parallel edge"
    )
    both_directions: bool = Field(
        default=True, description="Reopen reverse edge if applicable"
    )


class RoadReopenRecordItem(BaseModel):
    """Summary of a single reopened road edge."""

    closure_id: int | None = None
    start_node_id: str
    end_node_id: str
    edge_id: str
    status: str = "cleared"


class RoadReopenResponse(BaseModel):
    """Response returned upon reopening road closures."""

    status: Literal["success", "not_found", "error"]
    reopened_count: int = 0
    reopened_records: list[RoadReopenRecordItem] = Field(default_factory=list)
    graph_version: str = "v1.0"
    active_closures_count: int = 0
    message: str | None = None


# ---------------------------------------------------------------------------
# Earthquake Event Schemas (GeoJSON RFC 7946 Standard)
# ---------------------------------------------------------------------------


class EarthquakeEventProperties(BaseModel):
    """Properties for an individual earthquake feature."""

    external_id: str
    magnitude: float
    location_name: str
    occurred_at: datetime
    url: str | None = None
    status: str = "active"
    depth_km: float | None = None


class EarthquakeGeoJsonGeometry(BaseModel):
    """Point geometry with GeoJSON [longitude, latitude, depth_km] coordinates."""

    type: Literal["Point"] = "Point"
    coordinates: list[float]  # [longitude, latitude, depth_km]

    @field_validator("coordinates")
    @classmethod
    def validate_geojson_point(cls, coords: list[float]) -> list[float]:
        if len(coords) < 2:
            raise ValueError("GeoJSON point requires at least [longitude, latitude]")
        lon, lat = coords[0], coords[1]
        if not (-180.0 <= lon <= 180.0):
            raise ValueError(f"Longitude out of bounds [-180, 180]: {lon}")
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(f"Latitude out of bounds [-90, 90]: {lat}")
        return coords


class EarthquakeGeoJsonFeature(BaseModel):
    """GeoJSON Feature representing a single earthquake event."""

    type: Literal["Feature"] = "Feature"
    id: str
    geometry: EarthquakeGeoJsonGeometry
    properties: EarthquakeEventProperties


class EarthquakeGeoJsonMetadata(BaseModel):
    """Header metadata for an earthquake feature collection."""

    count: int
    min_magnitude: float | None = None
    queried_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    synced_to_db: bool = False
    inserted_count: int | None = None


class EarthquakeFeatureCollection(BaseModel):
    """RFC 7946 compliant GeoJSON FeatureCollection response for earthquake events."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: EarthquakeGeoJsonMetadata
    features: list[EarthquakeGeoJsonFeature] = Field(default_factory=list)
