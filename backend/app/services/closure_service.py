"""
Road closure and reopening management service for QuakeResQ.

Manages dynamic road segment closures and reopenings on the active working graph
maintained by GraphService. Supports one-way and two-way closures, parallel edge
resolution, and restores pristine edge attributes from the immutable base graph.
Independent of FastAPI and database models.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import networkx as nx

from app.services.graph_service import GraphService


@dataclass
class ClosureRecord:
    """Structured record of an active or reopened road segment closure."""

    start_node: Any
    end_node: Any
    edge_key: Any = 0
    road_name: str | None = None
    reason: str | None = None
    status: str = "active"  # "active" or "reopened"
    latitude: float | None = None
    longitude: float | None = None
    closed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reopened_at: datetime | None = None
    edge_attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert record to a dictionary representation."""
        return {
            "start_node": self.start_node,
            "end_node": self.end_node,
            "edge_key": self.edge_key,
            "road_name": self.road_name,
            "reason": self.reason,
            "status": self.status,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "reopened_at": self.reopened_at.isoformat() if self.reopened_at else None,
        }


class ClosureService:
    """
    Manages active road closures and reopenings on a road network graph.

    Interacts with the GraphService's working graph by removing edges during closures
    and restoring them from the base graph upon reopening.
    """

    def __init__(self, graph_service: GraphService) -> None:
        self._graph_service = graph_service
        self._active_closures: dict[tuple[Any, Any, Any], ClosureRecord] = {}

        # Register callback so resetting the working graph in GraphService
        # automatically synchronizes and clears the active closure tracking.
        if hasattr(self._graph_service, "register_reset_callback"):
            self._graph_service.register_reset_callback(self._on_graph_reset)

    @property
    def active_closure_count(self) -> int:
        """Return the count of currently active road closures."""
        return len(self._active_closures)

    def get_active_closures(self) -> list[ClosureRecord]:
        """Return a copy list of all currently active closure records."""
        return list(self._active_closures.values())

    def get_closure(
        self, start_node: Any, end_node: Any, edge_key: Any | None = None
    ) -> ClosureRecord | None:
        """
        Get an active closure record for a specific edge if one exists.

        If edge_key is None and multiple closures exist between the nodes, returns the first.
        """
        if edge_key is not None:
            closure_key = (start_node, end_node, edge_key)
            if closure_key in self._active_closures:
                return self._active_closures[closure_key]
            # Try key normalization (e.g. 0 vs "0")
            for (u, v, k), rec in self._active_closures.items():
                if u == start_node and v == end_node and str(k) == str(edge_key):
                    return rec
            return None

        for (u, v, _k), rec in self._active_closures.items():
            if u == start_node and v == end_node:
                return rec
        return None

    def is_closed(
        self, start_node: Any, end_node: Any, edge_key: Any | None = None
    ) -> bool:
        """
        Check if a road segment is currently closed in active closures.

        If edge_key is specified, checks that exact edge.
        If edge_key is None, returns True if ANY edge between start_node and end_node is closed.
        """
        return self.get_closure(start_node, end_node, edge_key=edge_key) is not None

    def close_road(
        self,
        start_node: Any,
        end_node: Any,
        edge_key: Any | None = None,
        both_directions: bool = False,
        reason: str | None = None,
        road_name: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[ClosureRecord]:
        """
        Close one or more directed edges in the working graph.

        Args:
            start_node: Start node ID.
            end_node: End node ID.
            edge_key: Specific parallel edge key to close. If None, all edges between
                      start_node and end_node are closed.
            both_directions: If True, also close reverse edges from end_node to start_node.
            reason: Optional explanation (e.g. "Structural damage", "Flooding").
            road_name: Optional custom road name. Falls back to edge attribute or synthetic name.
            latitude: Optional latitude override for the closure point.
            longitude: Optional longitude override for the closure point.

        Returns:
            List of ClosureRecord objects for the closed (or already closed) road segments.
        """
        if not self._graph_service.is_loaded:
            raise RuntimeError("Graph has not been loaded into GraphService.")

        base_graph = self._graph_service._base_graph
        working_graph = self._graph_service.get_working_graph()

        if base_graph is None:
            raise RuntimeError("Base graph is not available in GraphService.")

        if start_node not in base_graph:
            raise ValueError(f"Start node {start_node} not found in graph.")
        if end_node not in base_graph:
            raise ValueError(f"End node {end_node} not found in graph.")

        if not base_graph.has_edge(start_node, end_node):
            raise ValueError(
                f"Road segment from {start_node} to {end_node} does not exist in base graph."
            )

        if both_directions and not base_graph.has_edge(end_node, start_node):
            raise ValueError(
                f"Cannot close both directions: reverse road segment from {end_node} to {start_node} does not exist in base graph."
            )

        # Determine forward edge keys to close
        fwd_keys = self._resolve_target_keys(base_graph, start_node, end_node, edge_key)
        edges_to_close: list[tuple[Any, Any, Any]] = [(start_node, end_node, k) for k in fwd_keys]

        # Determine reverse edge keys if both_directions requested
        if both_directions:
            rev_keys = self._resolve_target_keys(base_graph, end_node, start_node, edge_key)
            edges_to_close.extend([(end_node, start_node, k) for k in rev_keys])

        closed_records: list[ClosureRecord] = []

        for u, v, k in edges_to_close:
            closure_key = (u, v, k)

            # Idempotent handling: if already closed, return existing record safely
            if closure_key in self._active_closures:
                existing = self._active_closures[closure_key]
                if reason:
                    existing.reason = reason
                if road_name:
                    existing.road_name = road_name
                closed_records.append(existing)
                continue

            # Remove from working graph so Dijkstra cannot use it
            if working_graph.has_edge(u, v, key=k):
                working_graph.remove_edge(u, v, key=k)

            # Retrieve original edge attributes from base graph (never mutate base graph)
            base_edge_attrs = copy.deepcopy(base_graph[u][v][k])

            # Resolve display name
            name_val = road_name or base_edge_attrs.get("name") or f"Road {u}-{v}"
            if isinstance(name_val, (list, tuple)):
                name_val = ", ".join(str(n) for n in name_val)

            # Resolve coordinates
            cl_lat = latitude
            cl_lon = longitude
            if cl_lat is None or cl_lon is None:
                node_data = base_graph.nodes.get(u, {})
                raw_lat = node_data.get("y", node_data.get("lat", node_data.get("latitude")))
                raw_lon = node_data.get("x", node_data.get("lon", node_data.get("longitude")))
                if raw_lat is not None and raw_lon is not None:
                    try:
                        cl_lat = float(raw_lat)
                        cl_lon = float(raw_lon)
                    except (ValueError, TypeError):
                        pass

            record = ClosureRecord(
                start_node=u,
                end_node=v,
                edge_key=k,
                road_name=str(name_val),
                reason=reason,
                status="active",
                latitude=cl_lat,
                longitude=cl_lon,
                edge_attributes=base_edge_attrs,
            )

            self._active_closures[closure_key] = record
            closed_records.append(record)

        return closed_records

    def reopen_road(
        self,
        start_node: Any,
        end_node: Any,
        edge_key: Any | None = None,
        both_directions: bool = False,
    ) -> list[ClosureRecord]:
        """
        Reopen one or more closed road segments and restore original attributes in working graph.

        Args:
            start_node: Start node ID.
            end_node: End node ID.
            edge_key: Specific parallel edge key to reopen. If None, all closed edges between
                      start_node and end_node are reopened.
            both_directions: If True, also reopen reverse closed edges from end_node to start_node.

        Returns:
            List of ClosureRecord objects that were successfully reopened.
        """
        if not self._graph_service.is_loaded:
            raise RuntimeError("Graph has not been loaded into GraphService.")

        base_graph = self._graph_service._base_graph
        working_graph = self._graph_service.get_working_graph()

        if base_graph is None:
            raise RuntimeError("Base graph is not available in GraphService.")

        # Identify candidate closure keys matching start_node and end_node
        candidate_keys = self._find_matching_closure_keys(
            start_node, end_node, edge_key=edge_key
        )

        if both_directions:
            candidate_keys.extend(
                self._find_matching_closure_keys(end_node, start_node, edge_key=edge_key)
            )

        reopened_records: list[ClosureRecord] = []

        for closure_key in candidate_keys:
            if closure_key not in self._active_closures:
                continue

            record = self._active_closures.pop(closure_key)
            u, v, k = closure_key

            # Restore original edge attributes from base graph (fallback to recorded attrs)
            if base_graph.has_edge(u, v, key=k):
                attrs_to_restore = copy.deepcopy(base_graph[u][v][k])
            else:
                attrs_to_restore = copy.deepcopy(record.edge_attributes)

            # Restore edge into working graph
            working_graph.add_edge(u, v, key=k, **attrs_to_restore)

            record.status = "reopened"
            record.reopened_at = datetime.now(timezone.utc)
            reopened_records.append(record)

        return reopened_records

    def reset_working_graph(self) -> None:
        """Reset the working graph to the pristine base graph and clear all closures."""
        self._graph_service.reset_working_graph()
        self._active_closures.clear()

    def clear_all_closures(self) -> int:
        """
        Reopen all active closures in the working graph and clear closure tracking.

        Returns the number of closures cleared.
        """
        count = len(self._active_closures)
        self.reset_working_graph()
        return count

    def _on_graph_reset(self) -> None:
        """Callback invoked when GraphService resets or reloads its working graph."""
        self._active_closures.clear()

    @staticmethod
    def _resolve_target_keys(
        base_graph: nx.MultiDiGraph, start_node: Any, end_node: Any, edge_key: Any | None
    ) -> list[Any]:
        """Resolve valid edge keys from base graph for the given node pair."""
        available_edges = base_graph[start_node][end_node]
        if edge_key is None:
            return list(available_edges.keys())

        # Exact match
        if edge_key in available_edges:
            return [edge_key]

        # Numeric/string normalized match
        if isinstance(edge_key, str) and edge_key.isdigit() and int(edge_key) in available_edges:
            return [int(edge_key)]
        if isinstance(edge_key, int) and str(edge_key) in available_edges:
            return [str(edge_key)]

        raise ValueError(
            f"Edge key {edge_key} does not exist between {start_node} and {end_node} in base graph."
        )

    def _find_matching_closure_keys(
        self, start_node: Any, end_node: Any, edge_key: Any | None
    ) -> list[tuple[Any, Any, Any]]:
        """Find keys in active closures matching the node pair and optional edge key."""
        matches: list[tuple[Any, Any, Any]] = []
        for (u, v, k) in list(self._active_closures.keys()):
            if u == start_node and v == end_node:
                if edge_key is None:
                    matches.append((u, v, k))
                elif k == edge_key or str(k) == str(edge_key):
                    matches.append((u, v, k))
        return matches
