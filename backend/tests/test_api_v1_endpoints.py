"""
Comprehensive unit and endpoint tests for QuakeResQ /api/v1 routes.

Tests:
1. Router path mapping and registration under /api/v1.
2. GET /api/v1/roads/graph (loaded and unloaded states).
3. POST /api/v1/roads/route (GeoJSON [lon, lat], distance_km, travel_time_source, graph_version).
4. POST /api/v1/roads/closures (in-memory removal, SQLite persistence, GeoJSON coordinates).
5. GET /api/v1/roads/closures (status filtering).
6. POST /api/v1/roads/reopen (SQLite update, graph edge restoration, route recovery).
7. GET /api/v1/earthquakes/events (GeoJSON FeatureCollection, RFC 7946 coordinates, SQLite sync).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import networkx as nx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base
from app.dependencies import (
    get_closure_service,
    get_graph_service,
    get_routing_service,
    reset_services_for_testing,
)
from app.main import app
from app.models.earthquake import Earthquake
from app.models.road_closure import RoadClosure
from app.routes import api_v1_router
from app.routes.earthquakes import get_earthquake_events
from app.routes.roads import (
    calculate_route,
    create_road_closure,
    get_road_closures,
    get_road_graph,
    reopen_road,
)
from app.schemas.api_models import (
    RoadClosureCreateRequest,
    RoadReopenRequest,
    RouteRequest,
)
from app.services.closure_service import ClosureService
from app.services.earthquake_service import NormalizedEarthquake
from app.services.graph_service import GraphService
from app.services.routing_service import RoutingService


@pytest.fixture(autouse=True)
def clean_singletons() -> None:
    reset_services_for_testing()
    yield
    reset_services_for_testing()


@pytest.fixture
def test_db_session() -> Session:
    """In-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def synthetic_graph() -> nx.MultiDiGraph:
    """
    Test diamond graph:
      (1) ---> (2) ---> (3)
       |                 ^
       v                 |
      (4) ---------------+
    Path 1->2->3 is fast (10s + 10s = 20s).
    Path 1->4->3 is slow (40s + 40s = 80s).
    """
    g = nx.MultiDiGraph()
    g.add_node(1, y=37.428, x=-122.145)
    g.add_node(2, y=37.430, x=-122.140)
    g.add_node(3, y=37.435, x=-122.135)
    g.add_node(4, y=37.420, x=-122.150)

    # Fast branch
    g.add_edge(1, 2, key=0, length=200.0, speed_kph=72.0, travel_time=10.0, name="Fast Ave")
    g.add_edge(2, 1, key=0, length=200.0, speed_kph=72.0, travel_time=10.0, name="Fast Ave")
    g.add_edge(2, 3, key=0, length=200.0, speed_kph=72.0, travel_time=10.0, name="Summit Rd")

    # Slow detour branch
    g.add_edge(1, 4, key=0, length=600.0, speed_kph=54.0, travel_time=40.0, name="Detour Blvd")
    g.add_edge(4, 3, key=0, length=600.0, speed_kph=54.0, travel_time=40.0, name="Detour Loop")

    return g


# ---------------------------------------------------------------------------
# Route Registration Tests
# ---------------------------------------------------------------------------


def test_api_v1_router_paths_registered() -> None:
    """Verify all 6 Member 1 agreed routes are registered under /api/v1 on the app."""
    openapi_paths = app.openapi()["paths"]

    assert "/api/v1/roads/graph" in openapi_paths
    assert "get" in openapi_paths["/api/v1/roads/graph"]

    assert "/api/v1/roads/closures" in openapi_paths
    assert "get" in openapi_paths["/api/v1/roads/closures"]
    assert "post" in openapi_paths["/api/v1/roads/closures"]

    assert "/api/v1/roads/reopen" in openapi_paths
    assert "post" in openapi_paths["/api/v1/roads/reopen"]

    assert "/api/v1/roads/route" in openapi_paths
    assert "post" in openapi_paths["/api/v1/roads/route"]

    assert "/api/v1/earthquakes/events" in openapi_paths
    assert "get" in openapi_paths["/api/v1/earthquakes/events"]


# ---------------------------------------------------------------------------
# Road Graph Endpoint Tests
# ---------------------------------------------------------------------------


def test_get_road_graph_unloaded() -> None:
    """When graph is not loaded, /roads/graph reports unloaded status safely."""
    gs = GraphService(cache_dir="/nonexistent")
    cs = ClosureService(gs)

    resp = get_road_graph(graph_service=gs, closure_service=cs)
    assert resp.status == "unloaded"
    assert resp.node_count == 0
    assert resp.edge_count == 0
    assert resp.active_closures_count == 0


def test_get_road_graph_loaded(synthetic_graph: nx.MultiDiGraph) -> None:
    """When graph is loaded, /roads/graph returns metrics and area metadata."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    resp = get_road_graph(graph_service=gs, closure_service=cs)
    assert resp.status == "loaded"
    assert resp.node_count == 4
    assert resp.edge_count == 5
    assert resp.travel_time_source == "osm_maxspeed_with_fallback_40kmh"
    assert resp.graph_version.startswith("v1.")


# ---------------------------------------------------------------------------
# Route Calculation Endpoint Tests
# ---------------------------------------------------------------------------


def test_calculate_route_endpoint_unloaded_raises_400() -> None:
    """Route calculation when graph is not loaded raises HTTP 400."""
    gs = GraphService(cache_dir="/nonexistent")
    rs = RoutingService(gs)

    req = RouteRequest(
        start_lat=37.428, start_lon=-122.145, dest_lat=37.435, dest_lon=-122.135
    )
    with pytest.raises(HTTPException) as exc_info:
        calculate_route(payload=req, routing_service=rs, graph_service=gs)

    assert exc_info.value.status_code == 400
    assert "graph is not loaded" in exc_info.value.detail.lower()


def test_calculate_route_endpoint_success(synthetic_graph: nx.MultiDiGraph) -> None:
    """Route calculation returns required fields and GeoJSON [lon, lat] coordinates."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    rs = get_routing_service(gs)

    req = RouteRequest(
        start_lat=37.428, start_lon=-122.145, dest_lat=37.435, dest_lon=-122.135
    )
    resp = calculate_route(payload=req, routing_service=rs, graph_service=gs)

    assert resp.status == "success"
    assert resp.distance_km == pytest.approx(0.40, abs=0.01)
    assert resp.estimated_time_minutes == pytest.approx(20.0 / 60.0, abs=0.01)
    assert resp.route_nodes == [1, 2, 3]

    # Verify GeoJSON coordinate format: [longitude, latitude]
    assert len(resp.route_coordinates) == 3
    for pt in resp.route_coordinates:
        assert len(pt) == 2
        lon, lat = pt[0], pt[1]
        assert -122.2 <= lon <= -122.0
        assert 37.0 <= lat <= 38.0

    # Verify metadata fields
    assert resp.calculated_at is not None
    assert resp.graph_version.startswith("v1.")
    # Accurate source: synthetic graph edges did not have OSM maxspeed attribute, so default 40 km/h fallback is reported
    assert resp.travel_time_source == "default_speed_fallback_40kmh"


# ---------------------------------------------------------------------------
# Road Closure & Reopening Endpoint Tests
# ---------------------------------------------------------------------------


def test_create_closure_and_reopen_lifecycle(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Test full cycle: Route -> Close Edge -> Route Detours -> Reopen -> Route Restores."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    rs = get_routing_service(gs)
    cs = get_closure_service(gs)

    # 1. Baseline route: fast branch 1 -> 2 -> 3
    base_route = calculate_route(
        payload=RouteRequest(
            start_lat=37.428, start_lon=-122.145, dest_lat=37.435, dest_lon=-122.135
        ),
        routing_service=rs,
        graph_service=gs,
    )
    assert base_route.route_nodes == [1, 2, 3]

    # 2. Close road 2 -> 3
    close_req = RoadClosureCreateRequest(
        start_node_id="2",
        end_node_id="3",
        edge_id="0",
        road_name="Summit Rd",
        reason="Landslide",
        both_directions=False,
        latitude=37.430,
        longitude=-122.140,
    )
    close_resp = create_road_closure(
        payload=close_req, db=test_db_session, closure_service=cs
    )
    assert close_resp.status == "success"
    assert len(close_resp.closed_records) == 1
    assert close_resp.active_closures_count == 1
    closure_db_id = close_resp.closed_records[0].id

    # Verify GeoJSON coordinates in closure response
    assert close_resp.closed_records[0].coordinates == [-122.140, 37.430]

    # 3. Route after closure: should detour via 1 -> 4 -> 3
    detour_route = calculate_route(
        payload=RouteRequest(
            start_lat=37.428, start_lon=-122.145, dest_lat=37.435, dest_lon=-122.135
        ),
        routing_service=rs,
        graph_service=gs,
    )
    assert detour_route.status == "success"
    assert detour_route.route_nodes == [1, 4, 3]

    # 4. Query closures
    active_closures = get_road_closures(
        status_filter="active", db=test_db_session
    )
    assert len(active_closures) == 1
    assert active_closures[0].id == closure_db_id
    assert active_closures[0].status == "active"

    # 5. Reopen road 2 -> 3
    reopen_req = RoadReopenRequest(closure_id=closure_db_id)
    reopen_resp = reopen_road(
        payload=reopen_req, db=test_db_session, closure_service=cs
    )
    assert reopen_resp.status == "success"
    assert reopen_resp.reopened_count == 1
    assert reopen_resp.active_closures_count == 0

    # 6. Route after reopen: reverts to fast branch 1 -> 2 -> 3
    restored_route = calculate_route(
        payload=RouteRequest(
            start_lat=37.428, start_lon=-122.145, dest_lat=37.435, dest_lon=-122.135
        ),
        routing_service=rs,
        graph_service=gs,
    )
    assert restored_route.route_nodes == [1, 2, 3]


def test_reopen_nonexistent_closure_returns_not_found(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Reopening a non-existent or already-cleared closure returns status 'not_found'."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    resp = reopen_road(
        payload=RoadReopenRequest(closure_id=9999),
        db=test_db_session,
        closure_service=cs,
    )
    assert resp.status == "not_found"
    assert resp.reopened_count == 0


def test_reopen_already_cleared_closure_endpoint_returns_zero(
    test_db_session: Session, synthetic_graph: nx.MultiDiGraph
) -> None:
    """Regression test: Reopening an already cleared closure via POST /roads/reopen returns reopened_count=0."""
    gs = get_graph_service()
    gs.set_graph(synthetic_graph)
    cs = get_closure_service(gs)

    # 1. Close road
    close_resp = create_road_closure(
        payload=RoadClosureCreateRequest(
            start_node_id="1",
            end_node_id="2",
            edge_id="0",
            road_name="Road 1-2",
        ),
        db=test_db_session,
        closure_service=cs,
    )
    closure_id = close_resp.closed_records[0].id

    # 2. First reopen: success
    resp1 = reopen_road(
        payload=RoadReopenRequest(closure_id=closure_id),
        db=test_db_session,
        closure_service=cs,
    )
    assert resp1.status == "success"
    assert resp1.reopened_count == 1

    # 3. Second reopen on the same cleared closure: must return reopened_count=0 and status="not_found"
    resp2 = reopen_road(
        payload=RoadReopenRequest(closure_id=closure_id),
        db=test_db_session,
        closure_service=cs,
    )
    assert resp2.status == "not_found"
    assert resp2.reopened_count == 0
    assert resp2.reopened_records == []


def test_route_travel_time_source_with_explicit_maxspeed() -> None:
    """When edges contain explicit OSM maxspeed, travel_time_source accurately reports 'osm_maxspeed'."""
    g = nx.MultiDiGraph()
    g.add_node(1, y=37.428, x=-122.145)
    g.add_node(2, y=37.430, x=-122.140)
    g.add_edge(1, 2, key=0, length=200.0, maxspeed="50 km/h", travel_time=14.4)

    gs = get_graph_service()
    gs.set_graph(g)
    rs = get_routing_service(gs)

    resp = calculate_route(
        payload=RouteRequest(
            start_lat=37.428, start_lon=-122.145, dest_lat=37.430, dest_lon=-122.140
        ),
        routing_service=rs,
        graph_service=gs,
    )
    assert resp.status == "success"
    assert resp.travel_time_source == "osm_maxspeed"


# ---------------------------------------------------------------------------
# Earthquake Events Endpoint Tests
# ---------------------------------------------------------------------------


def test_get_earthquake_events_endpoint(test_db_session: Session) -> None:
    """GET /earthquakes/events returns GeoJSON FeatureCollection and syncs to SQLite."""
    # Mock EarthquakeService
    mock_es = MagicMock()
    mock_es.fetch_earthquakes.return_value = [
        NormalizedEarthquake(
            external_id="us7000test1",
            magnitude=5.8,
            location_name="10 km N of San Jose, CA",
            latitude=37.40,
            longitude=-121.90,
            depth_km=12.5,
            occurred_at=datetime.now(timezone.utc),
            url="https://earthquake.usgs.gov/earthquakes/eventpage/us7000test1",
            status="active",
        ),
        NormalizedEarthquake(
            external_id="us7000test2",
            magnitude=3.4,
            location_name="Santa Cruz Mountains",
            latitude=37.10,
            longitude=-122.05,
            depth_km=8.0,
            occurred_at=datetime.now(timezone.utc),
            url=None,
            status="active",
        ),
    ]

    fc = get_earthquake_events(
        min_magnitude=3.0,
        limit=10,
        sync_db=True,
        db=test_db_session,
        earthquake_service=mock_es,
    )

    assert fc.type == "FeatureCollection"
    assert fc.metadata.count == 2
    assert fc.metadata.synced_to_db is True
    assert fc.metadata.inserted_count == 2

    # Check GeoJSON RFC 7946 coordinates [longitude, latitude, depth_km]
    feat1 = fc.features[0]
    assert feat1.geometry.coordinates == [-121.90, 37.40, 12.5]
    assert feat1.properties.magnitude == 5.8
    assert feat1.properties.location_name == "10 km N of San Jose, CA"

    # Verify SQLite database was synced
    db_quakes = test_db_session.query(Earthquake).all()
    assert len(db_quakes) == 2
    assert {q.external_id for q in db_quakes} == {"us7000test1", "us7000test2"}
