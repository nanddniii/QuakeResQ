"""
Dijkstra-based road routing service for QuakeResQ.

Calculates shortest and fastest paths on the directed MultiDiGraph maintained by GraphService.
Independent of FastAPI and database models.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import networkx as nx

from app.services.graph_service import GraphService


@dataclass
class RouteResult:
    """Structured result returned by route calculations."""

    status: str  # "success" or "no_route"
    start_node: Any | None
    destination_node: Any | None
    route_nodes: list[Any] = field(default_factory=list)
    route_coordinates: list[tuple[float, float]] = field(default_factory=list)  # (lat, lon)
    total_distance_km: float = 0.0
    estimated_time_minutes: float = 0.0
    algorithm: str = "dijkstra"
    message: str | None = None


class RoutingService:
    """Calculates shortest and fastest routes using NetworkX graph pathfinding."""

    SUPPORTED_ALGORITHMS = {"dijkstra"}
    SUPPORTED_WEIGHTS = {"travel_time", "length"}

    def __init__(self, graph_service: GraphService) -> None:
        self._graph_service = graph_service

    def find_route(
        self,
        start_lat: float,
        start_lon: float,
        dest_lat: float,
        dest_lon: float,
        weight: str = "travel_time",
        algorithm: str = "dijkstra",
    ) -> RouteResult:
        """
        Calculate route between two geographic coordinates.

        Snaps start and destination coordinates to the nearest road network nodes,
        then finds the shortest/fastest path using the specified algorithm and weight.
        """
        self._validate_coordinates(start_lat, start_lon)
        self._validate_coordinates(dest_lat, dest_lon)
        self._validate_algorithm(algorithm)
        self._validate_weight(weight)

        if not self._graph_service.is_loaded:
            raise RuntimeError("Graph has not been loaded into GraphService.")

        working_graph = self._graph_service.get_working_graph()
        if working_graph.number_of_nodes() == 0:
            raise ValueError("Road network graph contains no nodes.")

        start_node = self._graph_service.find_nearest_node(start_lat, start_lon)
        dest_node = self._graph_service.find_nearest_node(dest_lat, dest_lon)

        return self.find_route_between_nodes(
            start_node=start_node,
            dest_node=dest_node,
            weight=weight,
            algorithm=algorithm,
        )

    def find_route_between_nodes(
        self,
        start_node: Any,
        dest_node: Any,
        weight: str = "travel_time",
        algorithm: str = "dijkstra",
    ) -> RouteResult:
        """
        Calculate route between two node IDs in the active road network graph.
        """
        self._validate_algorithm(algorithm)
        self._validate_weight(weight)

        if not self._graph_service.is_loaded:
            raise RuntimeError("Graph has not been loaded into GraphService.")

        graph = self._graph_service.get_working_graph()

        if start_node not in graph:
            return RouteResult(
                status="no_route",
                start_node=start_node,
                destination_node=dest_node,
                algorithm=algorithm,
                message=f"Start node {start_node} not found in graph.",
            )

        if dest_node not in graph:
            return RouteResult(
                status="no_route",
                start_node=start_node,
                destination_node=dest_node,
                algorithm=algorithm,
                message=f"Destination node {dest_node} not found in graph.",
            )

        # Handle start == destination trivial case
        if start_node == dest_node:
            node_coords = self._extract_node_coordinates(graph, start_node)
            coords = [node_coords] if node_coords else []
            return RouteResult(
                status="success",
                start_node=start_node,
                destination_node=dest_node,
                route_nodes=[start_node],
                route_coordinates=coords,
                total_distance_km=0.0,
                estimated_time_minutes=0.0,
                algorithm=algorithm,
            )

        # Weight function that safely resolves parallel edges and missing attributes
        weight_func = self._create_weight_function(weight)

        try:
            path = nx.shortest_path(
                graph,
                source=start_node,
                target=dest_node,
                weight=weight_func,
                method="dijkstra",
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return RouteResult(
                status="no_route",
                start_node=start_node,
                destination_node=dest_node,
                algorithm=algorithm,
                message="No route found between specified points.",
            )

        # Accumulate metrics along chosen consecutive edges
        total_distance_m, total_time_s = self._calculate_route_metrics(graph, path, weight)
        route_coords = self._extract_path_coordinates(graph, path)

        return RouteResult(
            status="success",
            start_node=start_node,
            destination_node=dest_node,
            route_nodes=path,
            route_coordinates=route_coords,
            total_distance_km=round(total_distance_m / 1000.0, 4),
            estimated_time_minutes=round(total_time_s / 60.0, 2),
            algorithm=algorithm,
        )

    def _create_weight_function(self, weight_attr: str) -> Callable[[Any, Any, dict[Any, dict[str, Any]]], float]:
        """
        Create a weight function for NetworkX MultiDiGraph pathfinding.

        In a MultiDiGraph, data passed is {edge_key: edge_attributes_dict}.
        Picks the minimum weight across all parallel edges, providing safe fallbacks
        if edge attributes are absent or invalid.
        """
        def weight_func(_u: Any, _v: Any, edge_dict: dict[Any, dict[str, Any]]) -> float:
            best = float("inf")
            for data in edge_dict.values():
                val = self._extract_edge_metric(data, weight_attr)
                if val < best:
                    best = val
            return best if best != float("inf") else 1.0

        return weight_func

    def _calculate_route_metrics(
        self, graph: nx.MultiDiGraph, path: list[Any], primary_weight: str
    ) -> tuple[float, float]:
        """
        Calculate total distance in meters and total travel time in seconds for a path,
        selecting the optimal parallel edge between each consecutive node pair.
        """
        total_dist_m = 0.0
        total_time_s = 0.0
        secondary_weight = "length" if primary_weight != "length" else "travel_time"

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            edge_dict = graph[u][v]

            # Choose the specific parallel edge that minimized the primary routing weight,
            # using the exact same weight evaluation and fallback logic as Dijkstra.
            best_key = min(
                edge_dict.keys(),
                key=lambda k: (
                    self._extract_edge_metric(edge_dict[k], primary_weight),
                    self._extract_edge_metric(edge_dict[k], secondary_weight),
                    str(k),
                ),
            )
            edge_data = edge_dict[best_key]

            dist_m = self._extract_edge_metric(edge_data, "length")
            time_s = self._extract_edge_metric(edge_data, "travel_time")

            total_dist_m += dist_m
            total_time_s += time_s

        return total_dist_m, total_time_s

    @staticmethod
    def _extract_edge_metric(edge_data: dict[str, Any], metric_name: str) -> float:
        """Extract numeric metric with fallback calculation if missing or invalid."""
        val = edge_data.get(metric_name)
        if val is not None:
            try:
                num = float(val)
                if not math.isnan(num) and not math.isinf(num) and num >= 0:
                    return num
            except (ValueError, TypeError):
                pass

        # Fallback calculations if attribute was absent or invalid
        raw_len = edge_data.get("length")
        length_m = 100.0
        if raw_len is not None:
            try:
                parsed_len = float(raw_len)
                if not math.isnan(parsed_len) and not math.isinf(parsed_len) and parsed_len >= 0:
                    length_m = parsed_len
            except (ValueError, TypeError):
                length_m = 100.0

        raw_speed = edge_data.get("speed_kph", edge_data.get("maxspeed"))
        speed_kph = GraphService._parse_speed_kph(raw_speed, default_speed=40.0)

        if metric_name == "travel_time":
            speed_m_s = max(speed_kph * (1000.0 / 3600.0), 0.1)
            return length_m / speed_m_s

        return length_m

    @staticmethod
    def _extract_node_coordinates(graph: nx.MultiDiGraph, node: Any) -> tuple[float, float] | None:
        """Extract (latitude, longitude) coordinate tuple for a single node."""
        if node not in graph:
            return None
        data = graph.nodes[node]
        lat = data.get("y", data.get("lat", data.get("latitude")))
        lon = data.get("x", data.get("lon", data.get("longitude")))
        if lat is not None and lon is not None:
            try:
                return (float(lat), float(lon))
            except (ValueError, TypeError):
                return None
        return None

    def _extract_path_coordinates(
        self, graph: nx.MultiDiGraph, path: list[Any]
    ) -> list[tuple[float, float]]:
        """Extract (latitude, longitude) coordinate sequence for all nodes along a path."""
        coords: list[tuple[float, float]] = []
        for node in path:
            pt = self._extract_node_coordinates(graph, node)
            if pt is not None:
                coords.append(pt)
        return coords

    @staticmethod
    def _validate_coordinates(lat: Any, lon: Any) -> None:
        """Validate latitude and longitude bounds and numeric types."""
        if lat is None or isinstance(lat, (str, bool)) or not isinstance(lat, (int, float)):
            raise ValueError(
                f"Invalid latitude: {lat!r}. Must be a numeric value between -90 and 90 degrees."
            )
        if lon is None or isinstance(lon, (str, bool)) or not isinstance(lon, (int, float)):
            raise ValueError(
                f"Invalid longitude: {lon!r}. Must be a numeric value between -180 and 180 degrees."
            )

        lat_f = float(lat)
        lon_f = float(lon)

        if math.isnan(lat_f) or math.isinf(lat_f) or not (-90.0 <= lat_f <= 90.0):
            raise ValueError(f"Invalid latitude: {lat}. Must be between -90 and 90 degrees.")
        if math.isnan(lon_f) or math.isinf(lon_f) or not (-180.0 <= lon_f <= 180.0):
            raise ValueError(f"Invalid longitude: {lon}. Must be between -180 and 180 degrees.")

    def _validate_algorithm(self, algorithm: str) -> None:
        """Validate algorithm name."""
        if algorithm.lower() not in self.SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"Unsupported routing algorithm '{algorithm}'. Supported: {sorted(self.SUPPORTED_ALGORITHMS)}"
            )

    def _validate_weight(self, weight: str) -> None:
        """Validate routing weight name."""
        if weight.lower() not in self.SUPPORTED_WEIGHTS:
            raise ValueError(
                f"Unsupported routing weight '{weight}'. Supported: {sorted(self.SUPPORTED_WEIGHTS)}"
            )
