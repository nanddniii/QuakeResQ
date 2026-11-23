"""
FastAPI route handlers for road network graph, routing, and road closures.
Mounted under /api/v1/roads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    apply_safe_road_closure,
    apply_safe_road_reopen,
    get_closure_service,
    get_graph_service,
    get_graph_version,
    get_routing_service,
)
from app.models.road_closure import RoadClosure
from app.schemas.api_models import (
    RoadClosureCreateRequest,
    RoadClosureItemResponse,
    RoadClosureOperationResponse,
    RoadGraphAreaConfig,
    RoadGraphStatusResponse,
    RoadReopenRecordItem,
    RoadReopenRequest,
    RoadReopenResponse,
    RouteRequest,
    RouteResponse,
)
from app.services.closure_service import ClosureService
from app.services.graph_service import GraphService
from app.services.routing_service import RouteResult, RoutingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roads", tags=["Road Network & Routing"])


# ---------------------------------------------------------------------------
# 1. Road Graph Status
# ---------------------------------------------------------------------------


@router.get("/graph", response_model=RoadGraphStatusResponse)
def get_road_graph(
    graph_service: GraphService = Depends(get_graph_service),
    closure_service: ClosureService = Depends(get_closure_service),
) -> RoadGraphStatusResponse:
    """
    Retrieve road network graph status, topology metrics, and active version.
    """
    if not graph_service.is_loaded:
        return RoadGraphStatusResponse(
            status="unloaded",
            graph_version=get_graph_version(),
            node_count=0,
            edge_count=0,
            active_closures_count=0,
            travel_time_source="unloaded",
            area_config=None,
        )

    stats = graph_service.get_stats()
    cfg = graph_service.get_config()

    area_cfg = None
    if cfg is not None:
        center_coords = None
        if cfg.center_lon is not None and cfg.center_lat is not None:
            center_coords = [cfg.center_lon, cfg.center_lat]  # [lon, lat]

        area_cfg = RoadGraphAreaConfig(
            center_coordinates=center_coords,
            dist_meters=cfg.dist_meters,
            network_type=cfg.network_type,
            place_name=cfg.place_name,
        )

    default_speed = cfg.default_speed_kmh if cfg and cfg.default_speed_kmh else 40.0
    speed_tag = f"{int(default_speed) if default_speed.is_integer() else default_speed}kmh"

    return RoadGraphStatusResponse(
        status="loaded",
        graph_version=get_graph_version(),
        node_count=stats.get("node_count", 0),
        edge_count=stats.get("edge_count", 0),
        is_directed=stats.get("is_directed", True),
        is_multigraph=stats.get("is_multigraph", True),
        active_closures_count=closure_service.active_closure_count,
        default_speed_kmh=default_speed,
        travel_time_source=f"osm_maxspeed_with_fallback_{speed_tag}",
        area_config=area_cfg,
    )


# ---------------------------------------------------------------------------
# 2. Road Closures (Query)
# ---------------------------------------------------------------------------


@router.get("/closures", response_model=list[RoadClosureItemResponse])
def get_road_closures(
    status_filter: Annotated[
        str,
        Query(
            alias="status",
            description="Filter closures by status ('active', 'cleared', or 'all')",
        ),
    ] = "active",
    db: Session = Depends(get_db),
) -> list[RoadClosureItemResponse]:
    """
    List road closures from database with optional status filtering.
    Coordinates are formatted strictly as GeoJSON [longitude, latitude].
    """
    stmt = select(RoadClosure)
    if status_filter != "all":
        stmt = stmt.where(RoadClosure.status == status_filter)

    records = db.scalars(stmt).all()
    response_items: list[RoadClosureItemResponse] = []

    for item in records:
        coords = None
        if item.longitude is not None and item.latitude is not None:
            coords = [item.longitude, item.latitude]  # [lon, lat]

        response_items.append(
            RoadClosureItemResponse(
                id=item.id,
                road_name=item.road_name,
                start_node_id=item.start_node_id,
                end_node_id=item.end_node_id,
                edge_id=item.edge_id,
                reason=item.reason,
                status=item.status,
                coordinates=coords,
                created_at=item.created_at,
            )
        )

    return response_items


# ---------------------------------------------------------------------------
# 3. Create Road Closure
# ---------------------------------------------------------------------------


@router.post(
    "/closures",
    response_model=RoadClosureOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_road_closure(
    payload: RoadClosureCreateRequest,
    db: Session = Depends(get_db),
    closure_service: ClosureService = Depends(get_closure_service),
) -> RoadClosureOperationResponse:
    """
    Close one or more directed road segments in memory and record in SQLite.
    Safely rolls back in-memory changes if database persistence fails.
    """
    if not closure_service._graph_service.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Road network graph is not loaded. Cannot apply closures.",
        )

    base_graph = closure_service._graph_service._base_graph
    start_node: Any = payload.start_node_id
    end_node: Any = payload.end_node_id
    edge_key: Any = payload.edge_id

    # Resolve integer vs string node IDs if graph uses integers
    if base_graph is not None:
        if start_node not in base_graph and payload.start_node_id.isdigit():
            start_node = int(payload.start_node_id)
        if end_node not in base_graph and payload.end_node_id.isdigit():
            end_node = int(payload.end_node_id)
        if edge_key is not None and str(edge_key).isdigit():
            edge_key = int(edge_key)

    try:
        db_closures = apply_safe_road_closure(
            session=db,
            closure_service=closure_service,
            start_node=start_node,
            end_node=end_node,
            edge_key=edge_key,
            both_directions=payload.both_directions,
            road_name=payload.road_name,
            reason=payload.reason,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e

    items: list[RoadClosureItemResponse] = []
    for ent in db_closures:
        coords = None
        if ent.longitude is not None and ent.latitude is not None:
            coords = [ent.longitude, ent.latitude]

        items.append(
            RoadClosureItemResponse(
                id=ent.id,
                road_name=ent.road_name,
                start_node_id=ent.start_node_id,
                end_node_id=ent.end_node_id,
                edge_id=ent.edge_id,
                reason=ent.reason,
                status=ent.status,
                coordinates=coords,
                created_at=ent.created_at,
            )
        )

    return RoadClosureOperationResponse(
        status="success",
        closed_records=items,
        graph_version=get_graph_version(),
        active_closures_count=closure_service.active_closure_count,
        message=f"Successfully closed {len(items)} road segment(s).",
    )


# ---------------------------------------------------------------------------
# 4. Reopen Road Closure
# ---------------------------------------------------------------------------


@router.post("/reopen", response_model=RoadReopenResponse)
def reopen_road(
    payload: RoadReopenRequest,
    db: Session = Depends(get_db),
    closure_service: ClosureService = Depends(get_closure_service),
) -> RoadReopenResponse:
    """
    Reopen a previously closed road segment in working graph and mark status as cleared in SQLite.
    Supports reopening by closure_id or by node pair.
    """
    if not closure_service._graph_service.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Road network graph is not loaded. Cannot reopen roads.",
        )

    try:
        reopened_entities = apply_safe_road_reopen(
            session=db,
            closure_service=closure_service,
            closure_id=payload.closure_id,
            start_node_id=payload.start_node_id,
            end_node_id=payload.end_node_id,
            edge_id=payload.edge_id,
            both_directions=payload.both_directions,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e

    if not reopened_entities:
        return RoadReopenResponse(
            status="not_found",
            reopened_count=0,
            reopened_records=[],
            graph_version=get_graph_version(),
            active_closures_count=closure_service.active_closure_count,
            message="No active matching closures found to reopen.",
        )

    items = [
        RoadReopenRecordItem(
            closure_id=ent.id,
            start_node_id=ent.start_node_id,
            end_node_id=ent.end_node_id,
            edge_id=ent.edge_id,
            status="cleared",
        )
        for ent in reopened_entities
    ]

    return RoadReopenResponse(
        status="success",
        reopened_count=len(items),
        reopened_records=items,
        graph_version=get_graph_version(),
        active_closures_count=closure_service.active_closure_count,
        message=f"Successfully reopened {len(items)} road segment(s).",
    )


# ---------------------------------------------------------------------------
# 5. Route Calculation
# ---------------------------------------------------------------------------


@router.post("/route", response_model=RouteResponse)
def calculate_route(
    payload: RouteRequest,
    routing_service: RoutingService = Depends(get_routing_service),
    graph_service: GraphService = Depends(get_graph_service),
) -> RouteResponse:
    """
    Calculate the optimal route between start and destination coordinates.
    Returns distance, travel time, and coordinates formatted as GeoJSON [longitude, latitude].
    """
    if not graph_service.is_loaded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Road network graph is not loaded. Please load a road graph before routing.",
        )

    try:
        result: RouteResult = routing_service.find_route(
            start_lat=payload.start_lat,
            start_lon=payload.start_lon,
            dest_lat=payload.dest_lat,
            dest_lon=payload.dest_lon,
            weight=payload.weight,
            algorithm=payload.algorithm,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        ) from e

    # Convert coordinates from internal (lat, lon) to RFC 7946 GeoJSON [longitude, latitude]
    route_coords_geojson: list[list[float]] = [
        [float(pt[1]), float(pt[0])] for pt in result.route_coordinates
    ]

    travel_time_src = _determine_travel_time_source(graph_service, result.route_nodes)

    return RouteResponse(
        status=result.status,  # "success" or "no_route"
        distance_km=result.total_distance_km,
        estimated_time_minutes=result.estimated_time_minutes,
        route_coordinates=route_coords_geojson,
        calculated_at=datetime.now(timezone.utc),
        graph_version=get_graph_version(),
        travel_time_source=travel_time_src,
        start_node=result.start_node,
        destination_node=result.destination_node,
        route_nodes=result.route_nodes,
        algorithm=result.algorithm,
        message=result.message,
    )


def _determine_travel_time_source(
    graph_service: GraphService, route_nodes: list[Any]
) -> str:
    """
    Determine the accurate travel-time calculation source for a route.

    Reflects whether edges used OSM maxspeed attributes or fell back to the
    configured default speed (e.g. 40 km/h) when speed information was absent.
    """
    cfg = graph_service.get_config()
    default_speed = cfg.default_speed_kmh if cfg and cfg.default_speed_kmh else 40.0
    speed_tag = f"{int(default_speed) if default_speed.is_integer() else default_speed}kmh"

    if not route_nodes or len(route_nodes) < 2 or not graph_service.is_loaded:
        return f"osm_maxspeed_with_fallback_{speed_tag}"

    working_graph = graph_service.get_working_graph()
    has_osm_maxspeed = False
    has_fallback = False

    for i in range(len(route_nodes) - 1):
        u, v = route_nodes[i], route_nodes[i + 1]
        if not working_graph.has_edge(u, v):
            continue
        edge_dict = working_graph[u][v]
        for data in edge_dict.values():
            raw_speed = data.get("maxspeed")
            if raw_speed is not None and str(raw_speed).strip().lower() not in (
                "",
                "none",
                "signals",
            ):
                has_osm_maxspeed = True
            else:
                has_fallback = True

    if has_osm_maxspeed and not has_fallback:
        return "osm_maxspeed"
    if has_fallback and not has_osm_maxspeed:
        return f"default_speed_fallback_{speed_tag}"
    return f"osm_maxspeed_with_fallback_{speed_tag}"
