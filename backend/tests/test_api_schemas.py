"""
Unit tests for API-specific Pydantic schemas in QuakeResQ.

Verifies schema validation, coordinate ordering ([longitude, latitude]),
bounds checking, and serialization.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.api_models import (
    EarthquakeEventProperties,
    EarthquakeFeatureCollection,
    EarthquakeGeoJsonFeature,
    EarthquakeGeoJsonGeometry,
    EarthquakeGeoJsonMetadata,
    RoadClosureCreateRequest,
    RoadClosureItemResponse,
    RoadGraphStatusResponse,
    RoadReopenRequest,
    RouteRequest,
    RouteResponse,
)


def test_route_request_validation() -> None:
    """RouteRequest validates coordinate ranges and algorithm choices."""
    valid = RouteRequest(
        start_lat=37.428,
        start_lon=-122.145,
        dest_lat=37.435,
        dest_lon=-122.135,
        weight="travel_time",
    )
    assert valid.weight == "travel_time"
    assert valid.algorithm == "dijkstra"

    with pytest.raises(ValidationError):
        RouteRequest(
            start_lat=95.0,  # Invalid latitude > 90
            start_lon=-122.145,
            dest_lat=37.435,
            dest_lon=-122.135,
        )


def test_route_response_geojson_coordinates() -> None:
    """RouteResponse validates GeoJSON [longitude, latitude] coordinate sequences."""
    resp = RouteResponse(
        status="success",
        distance_km=1.45,
        estimated_time_minutes=2.18,
        route_coordinates=[
            [-122.145, 37.428],
            [-122.140, 37.431],
            [-122.135, 37.435],
        ],
        graph_version="v1.0",
        travel_time_source="osm_maxspeed_and_distance",
    )
    assert resp.status == "success"
    assert len(resp.route_coordinates) == 3
    assert resp.route_coordinates[0] == [-122.145, 37.428]
    assert resp.travel_time_source == "osm_maxspeed_and_distance"

    # Reject invalid coordinates (e.g. longitude out of bounds)
    with pytest.raises(ValidationError, match="Longitude out of bounds"):
        RouteResponse(
            status="success",
            distance_km=1.0,
            estimated_time_minutes=1.0,
            route_coordinates=[[-200.0, 37.0]],
        )


def test_road_closure_create_request_validation() -> None:
    """RoadClosureCreateRequest validates node IDs and optional coordinates."""
    req = RoadClosureCreateRequest(
        start_node_id="101",
        end_node_id="102",
        edge_id="0",
        road_name="Market St",
        reason="Flooding",
        both_directions=True,
        latitude=37.7749,
        longitude=-122.4194,
    )
    assert req.both_directions is True
    assert req.latitude == 37.7749
    assert req.longitude == -122.4194

    with pytest.raises(ValidationError):
        RoadClosureCreateRequest(
            start_node_id="",  # Empty node ID not allowed
            end_node_id="102",
            road_name="Market St",
        )


def test_road_reopen_request_defaults() -> None:
    """RoadReopenRequest supports reopening by closure_id or by node pair."""
    req_id = RoadReopenRequest(closure_id=42)
    assert req_id.closure_id == 42
    assert req_id.both_directions is True

    req_nodes = RoadReopenRequest(
        start_node_id="101", end_node_id="102", both_directions=False
    )
    assert req_nodes.start_node_id == "101"
    assert req_nodes.both_directions is False


def test_earthquake_feature_collection_geojson_conformance() -> None:
    """EarthquakeFeatureCollection conforms strictly to RFC 7946 GeoJSON format."""
    feature = EarthquakeGeoJsonFeature(
        id="us7000test",
        geometry=EarthquakeGeoJsonGeometry(
            coordinates=[-124.288, 40.732, 27.8]  # [lon, lat, depth]
        ),
        properties=EarthquakeEventProperties(
            external_id="us7000test",
            magnitude=5.4,
            location_name="12 km SW of Eureka, CA",
            occurred_at=datetime.now(timezone.utc),
            depth_km=27.8,
        ),
    )

    fc = EarthquakeFeatureCollection(
        metadata=EarthquakeGeoJsonMetadata(count=1, min_magnitude=5.0),
        features=[feature],
    )

    data = fc.model_dump()
    assert data["type"] == "FeatureCollection"
    assert data["features"][0]["geometry"]["coordinates"] == [-124.288, 40.732, 27.8]
    assert data["features"][0]["properties"]["magnitude"] == 5.4


def test_road_graph_status_response_defaults() -> None:
    """RoadGraphStatusResponse provides expected default structure."""
    status = RoadGraphStatusResponse(
        status="loaded",
        graph_version="v1.2",
        node_count=100,
        edge_count=200,
        active_closures_count=1,
    )
    assert status.status == "loaded"
    assert status.graph_version == "v1.2"
    assert status.default_speed_kmh == 40.0
    assert status.travel_time_source == "osm_maxspeed_with_fallback_40kmh"
