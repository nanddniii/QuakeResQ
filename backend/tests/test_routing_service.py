"""
Unit tests for the Dijkstra-based road routing service (RoutingService).

All tests use synthetic in-memory NetworkX graphs without internet access.
"""

import math
from typing import Any

import networkx as nx
import pytest

from app.services.graph_service import GraphService
from app.services.routing_service import RouteResult, RoutingService
from tests.test_graph_service import create_synthetic_road_graph


def create_directed_parallel_graph() -> nx.MultiDiGraph:
    """
    Synthetic graph with parallel edges exhibiting differing speed and distance trade-offs.

        (Node 1) ==== [Edge 0: Short but Slow (100m, 50s)] =====> (Node 2)
                 ==== [Edge 1: Long but Fast (200m, 10s)] ======>
    """
    g = nx.MultiDiGraph()
    g.add_node(1, y=37.77, x=-122.41)
    g.add_node(2, y=37.78, x=-122.42)

    # Edge 0: 100 meters, 50 seconds travel time
    g.add_edge(1, 2, key=0, length=100.0, speed_kph=7.2, travel_time=50.0, name="Narrow Alley")
    # Edge 1: 200 meters, 10 seconds travel time
    g.add_edge(1, 2, key=1, length=200.0, speed_kph=72.0, travel_time=10.0, name="Expressway")
    return g


def create_disconnected_graph() -> nx.MultiDiGraph:
    """Graph with two disconnected components."""
    g = nx.MultiDiGraph()
    g.add_node(1, y=27.71, x=85.31)
    g.add_node(2, y=27.72, x=85.32)
    g.add_edge(1, 2, key=0, length=200.0, travel_time=20.0)

    # Isolated island component
    g.add_node(99, y=27.80, x=85.40)
    return g


def test_routing_service_initialization() -> None:
    graph_service = GraphService()
    routing_service = RoutingService(graph_service)
    assert routing_service is not None


def test_find_route_success() -> None:
    graph_service = GraphService()
    graph_service.set_graph(create_synthetic_road_graph())
    routing_service = RoutingService(graph_service)

    # Start near Node 1 (27.7172, 85.3240), Destination near Node 4 (27.7145, 85.3275)
    result = routing_service.find_route(
        start_lat=27.71725,
        start_lon=85.32405,
        dest_lat=27.71445,
        dest_lon=85.32755,
        weight="travel_time",
        algorithm="dijkstra",
    )

    assert isinstance(result, RouteResult)
    assert result.status == "success"
    assert result.start_node == 1
    assert result.destination_node == 4
    assert len(result.route_nodes) >= 2
    assert result.route_nodes[0] == 1
    assert result.route_nodes[-1] == 4

    # Coordinates in (latitude, longitude) order
    assert len(result.route_coordinates) == len(result.route_nodes)
    for lat, lon in result.route_coordinates:
        assert isinstance(lat, float)
        assert isinstance(lon, float)
        assert 27.0 <= lat <= 28.0
        assert 85.0 <= lon <= 86.0

    assert result.total_distance_km > 0.0
    assert result.estimated_time_minutes > 0.0
    assert result.algorithm == "dijkstra"


def test_routing_parallel_edge_selection() -> None:
    graph_service = GraphService()
    graph_service.set_graph(create_directed_parallel_graph())
    routing_service = RoutingService(graph_service)

    # 1. Routing by travel_time should select Edge 1 (10s vs 50s)
    time_route = routing_service.find_route(
        start_lat=37.77, start_lon=-122.41,
        dest_lat=37.78, dest_lon=-122.42,
        weight="travel_time",
    )
    assert time_route.status == "success"
    # 10s / 60 = 0.17 minutes, 200m = 0.2 km
    assert time_route.total_distance_km == 0.2
    assert time_route.estimated_time_minutes == 0.17

    # 2. Routing by length should select Edge 0 (100m vs 200m)
    dist_route = routing_service.find_route(
        start_lat=37.77, start_lon=-122.41,
        dest_lat=37.78, dest_lon=-122.42,
        weight="length",
    )
    assert dist_route.status == "success"
    # 100m = 0.1 km, 50s / 60 = 0.83 minutes
    assert dist_route.total_distance_km == 0.1
    assert dist_route.estimated_time_minutes == 0.83


def test_routing_same_start_and_destination() -> None:
    graph_service = GraphService()
    graph_service.set_graph(create_synthetic_road_graph())
    routing_service = RoutingService(graph_service)

    result = routing_service.find_route(
        start_lat=27.7172, start_lon=85.3240,
        dest_lat=27.7172, dest_lon=85.3240,
    )

    assert result.status == "success"
    assert result.start_node == 1
    assert result.destination_node == 1
    assert result.route_nodes == [1]
    assert len(result.route_coordinates) == 1
    assert result.total_distance_km == 0.0
    assert result.estimated_time_minutes == 0.0


def test_routing_no_path_disconnected_component() -> None:
    graph_service = GraphService()
    graph_service.set_graph(create_disconnected_graph())
    routing_service = RoutingService(graph_service)

    result = routing_service.find_route(
        start_lat=27.71, start_lon=85.31,  # snaps to Node 1
        dest_lat=27.80, dest_lon=85.40,   # snaps to Node 99
    )

    assert result.status == "no_route"
    assert result.start_node == 1
    assert result.destination_node == 99
    assert result.route_nodes == []
    assert result.route_coordinates == []
    assert result.total_distance_km == 0.0
    assert result.estimated_time_minutes == 0.0
    assert result.message is not None


def test_routing_one_way_street_directionality() -> None:
    graph_service = GraphService()
    g = nx.MultiDiGraph()
    g.add_node("A", y=10.0, x=20.0)
    g.add_node("B", y=10.1, x=20.1)
    # Directed edge from A to B only
    g.add_edge("A", "B", key=0, length=500.0, travel_time=30.0)
    graph_service.set_graph(g)

    routing_service = RoutingService(graph_service)

    # A -> B succeeds
    forward = routing_service.find_route_between_nodes("A", "B")
    assert forward.status == "success"
    assert forward.route_nodes == ["A", "B"]

    # B -> A returns no_route because the road is one-way
    reverse = routing_service.find_route_between_nodes("B", "A")
    assert reverse.status == "no_route"
    assert reverse.route_nodes == []


def test_routing_missing_edge_weights_fallback() -> None:
    graph_service = GraphService()
    g = nx.MultiDiGraph()
    g.add_node(1, y=0.0, x=0.0)
    g.add_node(2, y=0.1, x=0.1)
    # Edge completely missing travel_time and length
    g.add_edge(1, 2, key=0)
    graph_service.set_graph(g)

    routing_service = RoutingService(graph_service)
    result = routing_service.find_route_between_nodes(1, 2)

    assert result.status == "success"
    assert result.route_nodes == [1, 2]
    assert result.total_distance_km > 0.0
    assert result.estimated_time_minutes > 0.0


def test_routing_invalid_coordinates() -> None:
    graph_service = GraphService()
    graph_service.set_graph(create_synthetic_road_graph())
    routing_service = RoutingService(graph_service)

    # Out of bounds
    with pytest.raises(ValueError, match="Invalid latitude"):
        routing_service.find_route(95.0, 85.0, 27.0, 85.0)

    with pytest.raises(ValueError, match="Invalid latitude"):
        routing_service.find_route(-95.0, 85.0, 27.0, 85.0)

    with pytest.raises(ValueError, match="Invalid longitude"):
        routing_service.find_route(27.0, 185.0, 27.0, 85.0)

    with pytest.raises(ValueError, match="Invalid latitude"):
        routing_service.find_route(float("nan"), 85.0, 27.0, 85.0)

    # Non-numeric types (None, string, bool, etc.) must raise ValueError (not TypeError)
    with pytest.raises(ValueError, match="Invalid latitude"):
        routing_service.find_route(None, 85.0, 27.0, 85.0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid longitude"):
        routing_service.find_route(27.0, None, 27.0, 85.0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid latitude"):
        routing_service.find_route("27.0", 85.0, 27.0, 85.0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid longitude"):
        routing_service.find_route(27.0, "invalid", 27.0, 85.0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid latitude"):
        routing_service.find_route(True, 85.0, 27.0, 85.0)  # type: ignore[arg-type]


def test_parallel_edge_fallback_consistency() -> None:
    """Verify parallel edge selection with fallback travel times matches Dijkstra's evaluation."""
    graph_service = GraphService()
    g = nx.MultiDiGraph()
    g.add_node(1, y=37.77, x=-122.41)
    g.add_node(2, y=37.78, x=-122.42)

    # Edge 0: length=100m, missing travel_time, speed_kph=60 km/h -> fallback travel_time = 6.0s
    g.add_edge(1, 2, key=0, length=100.0, speed_kph=60.0)
    # Edge 1: length=120m, explicit travel_time=15.0s
    g.add_edge(1, 2, key=1, length=120.0, travel_time=15.0)

    graph_service.set_graph(g)
    routing_service = RoutingService(graph_service)

    # Weight by travel_time: Edge 0 (6.0s fallback) < Edge 1 (15.0s) -> Edge 0 must be chosen
    res = routing_service.find_route(
        start_lat=37.77, start_lon=-122.41,
        dest_lat=37.78, dest_lon=-122.42,
        weight="travel_time",
    )
    assert res.status == "success"
    assert res.total_distance_km == 0.1  # 100m
    assert res.estimated_time_minutes == 0.1  # 6.0s / 60 = 0.1 min

    # Weight by length: Edge 0 (100m) < Edge 1 (120m) -> Edge 0 must be chosen
    res_dist = routing_service.find_route(
        start_lat=37.77, start_lon=-122.41,
        dest_lat=37.78, dest_lon=-122.42,
        weight="length",
    )
    assert res_dist.status == "success"
    assert res_dist.total_distance_km == 0.1
    assert res_dist.estimated_time_minutes == 0.1


def test_routing_unloaded_graph_raises_runtime_error() -> None:
    graph_service = GraphService()
    routing_service = RoutingService(graph_service)

    with pytest.raises(RuntimeError, match="Graph has not been loaded"):
        routing_service.find_route(27.0, 85.0, 27.1, 85.1)


def test_routing_empty_graph_raises_value_error() -> None:
    graph_service = GraphService()
    graph_service.set_graph(nx.MultiDiGraph())
    routing_service = RoutingService(graph_service)

    with pytest.raises(ValueError, match="contains no nodes"):
        routing_service.find_route(27.0, 85.0, 27.1, 85.1)


def test_routing_unsupported_algorithm_and_weight() -> None:
    graph_service = GraphService()
    graph_service.set_graph(create_synthetic_road_graph())
    routing_service = RoutingService(graph_service)

    with pytest.raises(ValueError, match="Unsupported routing algorithm"):
        routing_service.find_route(27.7, 85.3, 27.8, 85.4, algorithm="astar")

    with pytest.raises(ValueError, match="Unsupported routing weight"):
        routing_service.find_route(27.7, 85.3, 27.8, 85.4, weight="scenic_view")
