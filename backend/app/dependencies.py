"""
Shared service dependencies and application lifecycle providers for QuakeResQ.

Manages application-level singletons for GraphService, RoutingService,
ClosureService, and EarthquakeService. Implements robust safeguards:
  1. Safe startup without automatic network downloads on missing cache.
  2. Transactional closure and reopening with atomic graph/database rollbacks.
  3. Idempotent closure replay from SQLite on server reboot.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.road_closure import RoadClosure
from app.services.closure_service import ClosureRecord, ClosureService
from app.services.earthquake_service import EarthquakeService
from app.services.graph_service import GraphService
from app.services.routing_service import RoutingService

logger = logging.getLogger(__name__)

# Application-level service singletons
_graph_service: GraphService | None = None
_routing_service: RoutingService | None = None
_closure_service: ClosureService | None = None
_earthquake_service: EarthquakeService | None = None

# Monotonically increasing graph topology version
_graph_version_counter: int = 0


def get_graph_version() -> str:
    """Return current graph version string, e.g. 'v1.0', 'v1.1'."""
    return f"v1.{_graph_version_counter}"


def increment_graph_version() -> str:
    """Increment and return the new graph topology version."""
    global _graph_version_counter
    _graph_version_counter += 1
    return get_graph_version()


def reset_graph_version() -> str:
    """Reset graph version back to initial 'v1.0'."""
    global _graph_version_counter
    _graph_version_counter = 0
    return get_graph_version()


def init_graph_service(
    cache_file: str | Path | None = None, auto_load_cache: bool = True
) -> GraphService:
    """
    Initialize the application GraphService.

    Safeguard 1: If the cache file does not exist, does NOT trigger an automatic
    large OpenStreetMap download. The graph remains safely unloaded until explicitly
    configured or loaded by the user.
    """
    global _graph_service, _routing_service, _closure_service

    if _graph_service is None:
        _graph_service = GraphService()

    if auto_load_cache:
        target_cache = (
            Path(cache_file)
            if cache_file is not None
            else _graph_service.cache_dir / "road_network.graphml"
        )

        if target_cache.exists():
            try:
                logger.info("Loading cached road network from %s", target_cache)
                _graph_service.load_from_cache(target_cache)
            except Exception as e:
                logger.warning(
                    "Failed to load cached graphml file %s: %s. Graph remains unloaded.",
                    target_cache,
                    e,
                )
        else:
            logger.info(
                "No graph cache found at %s. Graph remains unloaded until explicit configuration.",
                target_cache,
            )

    # Initialize dependent services
    _routing_service = RoutingService(_graph_service)
    _closure_service = ClosureService(_graph_service)

    return _graph_service


def get_graph_service() -> GraphService:
    """FastAPI dependency provider for the singleton GraphService."""
    global _graph_service
    if _graph_service is None:
        init_graph_service(auto_load_cache=True)
    assert _graph_service is not None
    return _graph_service


def get_routing_service(
    graph_service: GraphService = Depends(get_graph_service),
) -> RoutingService:
    """FastAPI dependency provider for RoutingService."""
    global _routing_service
    if _routing_service is None or _routing_service._graph_service is not graph_service:
        _routing_service = RoutingService(graph_service)
    return _routing_service


def get_closure_service(
    graph_service: GraphService = Depends(get_graph_service),
) -> ClosureService:
    """FastAPI dependency provider for ClosureService."""
    global _closure_service
    if _closure_service is None or _closure_service._graph_service is not graph_service:
        _closure_service = ClosureService(graph_service)
    return _closure_service


def get_earthquake_service() -> EarthquakeService:
    """FastAPI dependency provider for EarthquakeService."""
    global _earthquake_service
    if _earthquake_service is None:
        _earthquake_service = EarthquakeService()
    return _earthquake_service


def replay_active_closures(session: Session, closure_service: ClosureService) -> int:
    """
    Replay active road closure records from SQLite into the in-memory working graph.

    Safeguard 2: Idempotent replay on server reboot. Does not re-apply closures
    already active in the in-memory graph. Gracefully ignores edges not found in
    the current graph without aborting startup.
    """
    if not closure_service._graph_service.is_loaded:
        logger.info("Skipping closure replay: road graph is not currently loaded.")
        return 0

    replayed_count = 0
    stmt = select(RoadClosure).where(RoadClosure.status == "active")
    active_db_closures = session.scalars(stmt).all()

    for item in active_db_closures:
        start_id: Any = item.start_node_id
        end_id: Any = item.end_node_id
        edge_id: Any = item.edge_id

        # Convert to int if node keys in graph are numeric
        base_graph = closure_service._graph_service._base_graph
        if base_graph is not None:
            if start_id not in base_graph and str(start_id).isdigit():
                start_id = int(start_id)
            if end_id not in base_graph and str(end_id).isdigit():
                end_id = int(end_id)
            if edge_id is not None and str(edge_id).isdigit():
                edge_id = int(edge_id)

        # Idempotent check: skip if already closed in memory
        if closure_service.is_closed(start_id, end_id, edge_key=edge_id):
            continue

        try:
            closure_service.close_road(
                start_node=start_id,
                end_node=end_id,
                edge_key=edge_id,
                both_directions=False,
                reason=item.reason,
                road_name=item.road_name,
                latitude=item.latitude,
                longitude=item.longitude,
            )
            replayed_count += 1
        except Exception as e:
            logger.warning(
                "Could not replay closure %s (%s -> %s, edge=%s): %s",
                item.id,
                start_id,
                end_id,
                edge_id,
                e,
            )

    if replayed_count > 0:
        increment_graph_version()
        logger.info("Successfully replayed %d active road closures from database.", replayed_count)

    return replayed_count


def apply_safe_road_closure(
    session: Session,
    closure_service: ClosureService,
    start_node: Any,
    end_node: Any,
    edge_key: Any | None,
    both_directions: bool,
    road_name: str,
    reason: str | None,
    latitude: float | None,
    longitude: float | None,
) -> list[RoadClosure]:
    """
    Atomically close road edge(s) in the in-memory graph and record in SQLite.

    Safeguard 2: If the database operation fails, the in-memory graph modification
    is cleanly rolled back (reopened) before re-raising the error.
    """
    # 1. Apply in-memory closure
    closed_records: list[ClosureRecord] = closure_service.close_road(
        start_node=start_node,
        end_node=end_node,
        edge_key=edge_key,
        both_directions=both_directions,
        reason=reason,
        road_name=road_name,
        latitude=latitude,
        longitude=longitude,
    )

    db_entities: list[RoadClosure] = []

    # 2. Persist to SQLite
    try:
        for rec in closed_records:
            # Check if active record already exists in DB
            stmt = select(RoadClosure).where(
                RoadClosure.start_node_id == str(rec.start_node),
                RoadClosure.end_node_id == str(rec.end_node),
                RoadClosure.edge_id == str(rec.edge_key),
                RoadClosure.status == "active",
            )
            existing = session.scalars(stmt).first()

            if existing is not None:
                if reason and not existing.reason:
                    existing.reason = reason
                db_entities.append(existing)
            else:
                db_closure = RoadClosure(
                    road_name=rec.road_name or road_name,
                    start_node_id=str(rec.start_node),
                    end_node_id=str(rec.end_node),
                    edge_id=str(rec.edge_key),
                    reason=reason,
                    status="active",
                    latitude=rec.latitude if rec.latitude is not None else latitude,
                    longitude=rec.longitude if rec.longitude is not None else longitude,
                )
                session.add(db_closure)
                db_entities.append(db_closure)

        session.commit()
        for ent in db_entities:
            session.refresh(ent)

        increment_graph_version()
        return db_entities

    except Exception as db_err:
        session.rollback()
        # Rollback in-memory graph: restore any edges that were just closed
        for rec in closed_records:
            try:
                closure_service.reopen_road(
                    start_node=rec.start_node,
                    end_node=rec.end_node,
                    edge_key=rec.edge_key,
                    both_directions=False,
                )
            except Exception as rollback_err:
                logger.error(
                    "Critical: failed in-memory rollback for %s -> %s: %s",
                    rec.start_node,
                    rec.end_node,
                    rollback_err,
                )
        raise RuntimeError(f"Database error during closure; graph modification rolled back: {db_err}") from db_err


def apply_safe_road_reopen(
    session: Session,
    closure_service: ClosureService,
    closure_id: int | None = None,
    start_node_id: str | None = None,
    end_node_id: str | None = None,
    edge_id: str | None = None,
    both_directions: bool = True,
) -> list[RoadClosure]:
    """
    Atomically reopen road edge(s) in the in-memory graph and mark as cleared in SQLite.

    Safeguard 2: If the database operation fails, the in-memory edge removal is
    re-applied so graph and database states do not diverge.
    """
    db_to_clear: list[RoadClosure] = []

    if closure_id is not None:
        closure_obj = session.get(RoadClosure, closure_id)
        if closure_obj is not None and closure_obj.status == "active":
            db_to_clear.append(closure_obj)
            if both_directions:
                rev_stmt = select(RoadClosure).where(
                    RoadClosure.start_node_id == closure_obj.end_node_id,
                    RoadClosure.end_node_id == closure_obj.start_node_id,
                    RoadClosure.status == "active",
                )
                rev_closures = session.scalars(rev_stmt).all()
                db_to_clear.extend(rev_closures)
    elif start_node_id is not None and end_node_id is not None:
        stmt = select(RoadClosure).where(
            RoadClosure.start_node_id == start_node_id,
            RoadClosure.end_node_id == end_node_id,
            RoadClosure.status == "active",
        )
        if edge_id is not None:
            stmt = stmt.where(RoadClosure.edge_id == edge_id)
        db_to_clear.extend(session.scalars(stmt).all())

        if both_directions:
            rev_stmt = select(RoadClosure).where(
                RoadClosure.start_node_id == end_node_id,
                RoadClosure.end_node_id == start_node_id,
                RoadClosure.status == "active",
            )
            if edge_id is not None:
                rev_stmt = rev_stmt.where(RoadClosure.edge_id == edge_id)
            db_to_clear.extend(session.scalars(rev_stmt).all())

    if not db_to_clear:
        return []

    # 1. Reopen in working graph
    reopened_records: list[ClosureRecord] = []
    for item in db_to_clear:
        u: Any = item.start_node_id
        v: Any = item.end_node_id
        k: Any = item.edge_id

        base_graph = closure_service._graph_service._base_graph
        if base_graph is not None:
            if u not in base_graph and str(u).isdigit():
                u = int(u)
            if v not in base_graph and str(v).isdigit():
                v = int(v)
            if k is not None and str(k).isdigit():
                k = int(k)

        recs = closure_service.reopen_road(start_node=u, end_node=v, edge_key=k)
        reopened_records.extend(recs)

    # 2. Update database
    try:
        for item in db_to_clear:
            item.status = "cleared"
        session.commit()
        for item in db_to_clear:
            session.refresh(item)

        increment_graph_version()
        return db_to_clear

    except Exception as db_err:
        session.rollback()
        # Rollback: re-close edges in working graph
        for rec in reopened_records:
            try:
                closure_service.close_road(
                    start_node=rec.start_node,
                    end_node=rec.end_node,
                    edge_key=rec.edge_key,
                )
            except Exception as rollback_err:
                logger.error(
                    "Critical: failed in-memory re-close rollback for %s -> %s: %s",
                    rec.start_node,
                    rec.end_node,
                    rollback_err,
                )
        raise RuntimeError(f"Database error during reopen; graph state rolled back: {db_err}") from db_err


def reset_services_for_testing() -> None:
    """Helper to reset singletons in unit and integration test fixtures."""
    global _graph_service, _routing_service, _closure_service, _earthquake_service
    _graph_service = None
    _routing_service = None
    _closure_service = None
    _earthquake_service = None
    reset_graph_version()
