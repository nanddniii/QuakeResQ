"""
Unit tests for ClosureService in QuakeResQ.

Verifies road segment closures, reopenings, parallel edge handling, two-way roads,
idempotent duplicate closures, base graph immutability, and synchronization with
GraphService and RoutingService using small synthetic NetworkX graphs.
"""

from __future__ import annotations

import networkx as nx
import pytest

from app.services.closure_service import ClosureRecord, ClosureService
from app.services.graph_service import GraphService
from app.services.routing_service import RoutingService


@pytest.fixture
def synthetic_graph() -> nx.MultiDiGraph:
    """
    Creates a diamond-shaped test graph:
        (1) ---> (2) ---> (4)
         |                 ^
         v                 |
        (3) --------------+

    Route 1 -> 2 -> 4 is faster (10s + 10s = 20s).
    Route 1 -> 3 -> 4 is slower (30s + 30s = 60s).
    Road 1 <-> 2 is bidirectional.
    """
    g = nx.MultiDiGraph()
    # Node coordinates (Palo Alto region)
    g.add_node(1, y=37.428, x=-122.145)
    g.add_node(2, y=37.430, x=-122.140)
    g.add_node(3, y=37.420, x=-122.150)
    g.add_node(4, y=37.435, x=-122.135)

    # 1 -> 2 (fast forward)
    g.add_edge(1, 2, key=0, length=200.0, speed_kph=72.0, travel_time=10.0, name="Fast Ave")
    # 2 -> 1 (reverse for 1 <-> 2)
    g.add_edge(2, 1, key=0, length=200.0, speed_kph=72.0, travel_time=10.0, name="Fast Ave")

    # 2 -> 4
    g.add_edge(2, 4, key=0, length=200.0, speed_kph=72.0, travel_time=10.0, name="Summit Rd")

    # 1 -> 3 (slow detour)
    g.add_edge(1, 3, key=0, length=500.0, speed_kph=60.0, travel_time=30.0, name="Detour Blvd")
    # 3 -> 4
    g.add_edge(3, 4, key=0, length=500.0, speed_kph=60.0, travel_time=30.0, name="Detour Loop")

    return g


@pytest.fixture
def multigraph_with_parallel_edges() -> nx.MultiDiGraph:
    """
    Creates a graph with two parallel edges between node 1 and 2:
      Key 0: fast (100m, 10s)
      Key 1: slow (200m, 50s)
    """
    g = nx.MultiDiGraph()
    g.add_node(1, y=37.428, x=-122.145)
    g.add_node(2, y=37.430, x=-122.140)

    g.add_edge(1, 2, key=0, length=100.0, speed_kph=36.0, travel_time=10.0, name="Expressway")
    g.add_edge(1, 2, key=1, length=200.0, speed_kph=14.4, travel_time=50.0, name="Local Lane")

    return g


def test_closure_service_unloaded_graph_raises_error() -> None:
    """Unloaded GraphService must raise RuntimeError on closure operations."""
    gs = GraphService()
    service = ClosureService(gs)

    with pytest.raises(RuntimeError, match="Graph has not been loaded"):
        service.close_road(1, 2)

    with pytest.raises(RuntimeError, match="Graph has not been loaded"):
        service.reopen_road(1, 2)


def test_close_single_directed_edge_affects_dijkstra(synthetic_graph: nx.MultiDiGraph) -> None:
    """Closing the faster edge must force RoutingService to route via the slower detour."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    routing = RoutingService(gs)
    closures = ClosureService(gs)

    # Baseline: fast route 1 -> 2 -> 4
    route_before = routing.find_route_between_nodes(1, 4)
    assert route_before.status == "success"
    assert route_before.route_nodes == [1, 2, 4]
    assert route_before.estimated_time_minutes == pytest.approx(20.0 / 60.0, abs=0.01)

    # Close edge 2 -> 4
    closed = closures.close_road(2, 4, edge_key=0, reason="Collapsed bridge")
    assert len(closed) == 1
    assert closed[0].start_node == 2
    assert closed[0].end_node == 4
    assert closed[0].status == "active"
    assert closures.is_closed(2, 4) is True
    assert closures.active_closure_count == 1

    # Route after closure: should take 1 -> 3 -> 4
    route_after = routing.find_route_between_nodes(1, 4)
    assert route_after.status == "success"
    assert route_after.route_nodes == [1, 3, 4]
    assert route_after.estimated_time_minutes == pytest.approx(60.0 / 60.0, abs=0.01)


def test_reopen_edge_restores_dijkstra_route(synthetic_graph: nx.MultiDiGraph) -> None:
    """Reopening a closed road restores the original faster route and edge attributes."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    routing = RoutingService(gs)
    closures = ClosureService(gs)

    # Close and verify detour
    closures.close_road(2, 4, edge_key=0)
    assert routing.find_route_between_nodes(1, 4).route_nodes == [1, 3, 4]

    # Reopen edge 2 -> 4
    reopened = closures.reopen_road(2, 4, edge_key=0)
    assert len(reopened) == 1
    assert reopened[0].status == "reopened"
    assert reopened[0].reopened_at is not None
    assert closures.is_closed(2, 4) is False
    assert closures.active_closure_count == 0

    # Route should revert back to 1 -> 2 -> 4
    route_reopened = routing.find_route_between_nodes(1, 4)
    assert route_reopened.status == "success"
    assert route_reopened.route_nodes == [1, 2, 4]


def test_close_parallel_edge_selective(multigraph_with_parallel_edges: nx.MultiDiGraph) -> None:
    """Closing only one parallel edge leaves the other parallel edge operational."""
    gs = GraphService()
    gs.set_graph(multigraph_with_parallel_edges)
    routing = RoutingService(gs)
    closures = ClosureService(gs)

    # Baseline route uses fast key 0 (10s)
    res_base = routing.find_route_between_nodes(1, 2)
    assert res_base.status == "success"
    assert res_base.estimated_time_minutes == pytest.approx(10.0 / 60.0, abs=0.01)

    # Close only key 0
    closed = closures.close_road(1, 2, edge_key=0)
    assert len(closed) == 1
    assert closed[0].edge_key == 0
    assert closures.is_closed(1, 2, edge_key=0) is True
    assert closures.is_closed(1, 2, edge_key=1) is False

    # Route now uses slow key 1 (50s)
    res_key1 = routing.find_route_between_nodes(1, 2)
    assert res_key1.status == "success"
    assert res_key1.estimated_time_minutes == pytest.approx(50.0 / 60.0, abs=0.01)

    # Close key 1 as well
    closed_2 = closures.close_road(1, 2, edge_key=1)
    assert len(closed_2) == 1
    assert closures.active_closure_count == 2

    # Now no route should exist
    res_none = routing.find_route_between_nodes(1, 2)
    assert res_none.status == "no_route"


def test_close_all_parallel_edges_when_key_is_none(
    multigraph_with_parallel_edges: nx.MultiDiGraph,
) -> None:
    """When edge_key is None, all parallel edges between start and end node are closed."""
    gs = GraphService()
    gs.set_graph(multigraph_with_parallel_edges)
    routing = RoutingService(gs)
    closures = ClosureService(gs)

    closed_all = closures.close_road(1, 2, edge_key=None, reason="Full highway closure")
    assert len(closed_all) == 2
    assert {c.edge_key for c in closed_all} == {0, 1}
    assert closures.active_closure_count == 2

    # Route fails immediately
    res = routing.find_route_between_nodes(1, 2)
    assert res.status == "no_route"

    # Reopen all parallel edges
    reopened_all = closures.reopen_road(1, 2, edge_key=None)
    assert len(reopened_all) == 2
    assert closures.active_closure_count == 0

    # Route restored
    res_restored = routing.find_route_between_nodes(1, 2)
    assert res_restored.status == "success"
    assert res_restored.estimated_time_minutes == pytest.approx(10.0 / 60.0, abs=0.01)


def test_two_way_road_closure(synthetic_graph: nx.MultiDiGraph) -> None:
    """Closing both directions removes both directed edges from the working graph."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    routing = RoutingService(gs)
    closures = ClosureService(gs)

    # Close both directions for road 1 <-> 2
    closed = closures.close_road(1, 2, both_directions=True, reason="Landslide")
    assert len(closed) == 2
    assert {(c.start_node, c.end_node) for c in closed} == {(1, 2), (2, 1)}
    assert closures.is_closed(1, 2) is True
    assert closures.is_closed(2, 1) is True

    # Check routing in both directions
    # 1 -> 2 can only go 1 -> 3 -> 4, but 4 has no back edge to 2 in this synthetic graph, so no route
    assert routing.find_route_between_nodes(2, 1).status == "no_route"

    # Reopen both directions
    reopened = closures.reopen_road(1, 2, both_directions=True)
    assert len(reopened) == 2
    assert closures.is_closed(1, 2) is False
    assert closures.is_closed(2, 1) is False
    assert routing.find_route_between_nodes(2, 1).status == "success"


def test_two_way_closure_on_one_way_road_raises_error(synthetic_graph: nx.MultiDiGraph) -> None:
    """Requesting both_directions on a strictly one-way road must raise ValueError."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    # Edge 2 -> 4 exists, but 4 -> 2 does not exist
    with pytest.raises(ValueError, match="reverse road segment.*does not exist"):
        closures.close_road(2, 4, both_directions=True)

    # Edge 2 -> 4 must remain open
    assert closures.is_closed(2, 4) is False
    assert gs.get_working_graph().has_edge(2, 4, key=0) is True


def test_duplicate_closure_handling_safe_idempotent(synthetic_graph: nx.MultiDiGraph) -> None:
    """Duplicate closure requests must be handled safely without error or duplicated tracking."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    rec1 = closures.close_road(1, 2, edge_key=0, reason="Flooding")
    assert len(rec1) == 1
    assert closures.active_closure_count == 1
    assert rec1[0].reason == "Flooding"

    # Request closure again for the same edge
    rec2 = closures.close_road(1, 2, edge_key=0, reason="Updated reason: Heavy flooding")
    assert len(rec2) == 1
    assert closures.active_closure_count == 1
    assert closures.get_closure(1, 2, 0).reason == "Updated reason: Heavy flooding"


def test_reopen_non_closed_road_safe(synthetic_graph: nx.MultiDiGraph) -> None:
    """Reopening an already open road segment returns an empty list without raising an error."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    result = closures.reopen_road(1, 2, edge_key=0)
    assert result == []
    assert closures.active_closure_count == 0


def test_reset_working_graph_clears_active_closures(synthetic_graph: nx.MultiDiGraph) -> None:
    """Resetting the working graph via GraphService must clear active closures tracking."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    routing = RoutingService(gs)
    closures = ClosureService(gs)

    # Close both forward paths from node 1 (1->2 and 1->3)
    closures.close_road(1, 2, edge_key=0)
    closures.close_road(1, 3, edge_key=0)
    assert closures.active_closure_count == 2
    assert routing.find_route_between_nodes(1, 4).status == "no_route"

    # Reset working graph on GraphService directly
    gs.reset_working_graph()

    # Active closures must now be empty and route functional
    assert closures.active_closure_count == 0
    assert closures.is_closed(1, 2) is False
    assert closures.is_closed(1, 3) is False

    res = routing.find_route_between_nodes(1, 4)
    assert res.status == "success"
    assert res.route_nodes == [1, 2, 4]


def test_clear_all_closures_method(synthetic_graph: nx.MultiDiGraph) -> None:
    """Calling clear_all_closures on ClosureService resets working graph and returns count."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    closures.close_road(1, 2, edge_key=0)
    closures.close_road(2, 4, edge_key=0)
    assert closures.active_closure_count == 2

    cleared_count = closures.clear_all_closures()
    assert cleared_count == 2
    assert closures.active_closure_count == 0
    assert closures.get_active_closures() == []


def test_base_graph_remains_unmodified(synthetic_graph: nx.MultiDiGraph) -> None:
    """Base graph must remain completely unmodified through closures, modifications, and reopenings."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    base_before = gs.get_base_graph()
    num_nodes_before = base_before.number_of_nodes()
    num_edges_before = base_before.number_of_edges()
    edge_attrs_before = dict(base_before[2][4][0])

    # Perform active operations
    closures.close_road(2, 4, edge_key=0)
    closures.close_road(1, 2, both_directions=True)
    closures.reopen_road(2, 4, edge_key=0)

    # Base graph snapshot after
    base_after = gs.get_base_graph()
    assert base_after.number_of_nodes() == num_nodes_before
    assert base_after.number_of_edges() == num_edges_before
    assert dict(base_after[2][4][0]) == edge_attrs_before
    assert base_after.has_edge(1, 2, key=0) is True
    assert base_after.has_edge(2, 1, key=0) is True


def test_closure_record_metadata_and_coordinates(synthetic_graph: nx.MultiDiGraph) -> None:
    """ClosureRecord captures coordinates, road name, reason, and dictionary export."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    closed = closures.close_road(
        1,
        2,
        edge_key=0,
        reason="Debris and electrical wire down",
        road_name="Custom Fast Ave",
    )
    rec = closed[0]
    assert rec.road_name == "Custom Fast Ave"
    assert rec.reason == "Debris and electrical wire down"
    assert rec.status == "active"
    assert rec.latitude == pytest.approx(37.428)
    assert rec.longitude == pytest.approx(-122.145)

    data = rec.to_dict()
    assert data["start_node"] == 1
    assert data["end_node"] == 2
    assert data["road_name"] == "Custom Fast Ave"
    assert data["status"] == "active"
    assert data["closed_at"] is not None


def test_invalid_nodes_and_edges_raise_value_error(synthetic_graph: nx.MultiDiGraph) -> None:
    """Invalid node IDs, non-existent edges, and invalid edge keys raise clear ValueErrors."""
    gs = GraphService()
    gs.set_graph(synthetic_graph)
    closures = ClosureService(gs)

    # Non-existent start node
    with pytest.raises(ValueError, match="Start node 999 not found"):
        closures.close_road(999, 2)

    # Non-existent end node
    with pytest.raises(ValueError, match="End node 999 not found"):
        closures.close_road(1, 999)

    # Nodes exist but no directed edge exists between them
    with pytest.raises(ValueError, match="Road segment.*does not exist in base graph"):
        closures.close_road(3, 1)

    # Edge exists but specific key does not exist
    with pytest.raises(ValueError, match="Edge key 99 does not exist"):
        closures.close_road(1, 2, edge_key=99)
