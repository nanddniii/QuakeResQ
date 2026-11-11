"""
Unit tests for the road graph engine (GraphService).

All tests use synthetic in-memory NetworkX graphs without internet access.
"""

from pathlib import Path

import networkx as nx
import pytest

from app.services.graph_service import MPH_TO_KPH, GraphConfig, GraphService


def create_synthetic_road_graph() -> nx.MultiDiGraph:
    """
    Create a small synthetic road network with 4 nodes, directed roads,
    parallel edges, and coordinate attributes.

        (Node 1) === [Parallel Edges 0 & 1] ===> (Node 2)
           |                                        |
       [Edge 0]                                 [Edge 0]
           v                                        v
        (Node 3) ------------ [Edge 0] ---------> (Node 4)
    """
    g = nx.MultiDiGraph()

    # Nodes with latitude (y) and longitude (x) coordinates (Kathmandu area)
    g.add_node(1, y=27.7172, x=85.3240, osmid=101)
    g.add_node(2, y=27.7190, x=85.3260, osmid=102)
    g.add_node(3, y=27.7150, x=85.3235, osmid=103)
    g.add_node(4, y=27.7145, x=85.3275, osmid=104)

    # Parallel directed edges between node 1 and node 2 (e.g. main road and express lane)
    g.add_edge(1, 2, key=0, length=250.0, speed_kph=50.0, name="Main Ave")
    g.add_edge(1, 2, key=1, length=270.0, speed_kph=60.0, name="Express Bypass")

    # Directed edges
    g.add_edge(1, 3, key=0, length=260.0, speed_kph=30.0, name="South St")
    g.add_edge(2, 4, key=0, length=520.0, speed_kph=40.0, name="East Blvd")
    g.add_edge(3, 4, key=0, length=400.0, speed_kph=30.0, name="Ring Connector")

    return g


def test_graph_service_initial_state() -> None:
    service = GraphService()
    assert not service.is_loaded
    stats = service.get_stats()
    assert stats == {"loaded": False}

    with pytest.raises(RuntimeError, match="not been loaded"):
        service.get_base_graph()

    with pytest.raises(RuntimeError, match="not been loaded"):
        service.get_working_graph()


def test_set_synthetic_graph_preserves_multidigraph() -> None:
    service = GraphService()
    g = create_synthetic_road_graph()
    service.set_graph(g)

    assert service.is_loaded
    working = service.get_working_graph()
    base = service.get_base_graph()

    assert isinstance(working, nx.MultiDiGraph)
    assert isinstance(base, nx.MultiDiGraph)
    assert working.number_of_nodes() == 4
    assert working.number_of_edges() == 5

    # Verify parallel edges between node 1 and node 2 are preserved
    assert working.has_edge(1, 2, key=0)
    assert working.has_edge(1, 2, key=1)
    assert working[1][2][0]["name"] == "Main Ave"
    assert working[1][2][1]["name"] == "Express Bypass"


def test_working_graph_isolation_and_reset() -> None:
    service = GraphService()
    g = create_synthetic_road_graph()
    service.set_graph(g)

    working = service.get_working_graph()
    base = service.get_base_graph()

    # Modifying working graph (e.g., removing an edge) does not affect base graph
    working.remove_edge(1, 2, key=0)
    assert not working.has_edge(1, 2, key=0)
    assert base.has_edge(1, 2, key=0)
    assert working.number_of_edges() == 4
    assert base.number_of_edges() == 5

    # Resetting working graph restores it from base
    service.reset_working_graph()
    restored = service.get_working_graph()
    assert restored.has_edge(1, 2, key=0)
    assert restored.number_of_edges() == 5


def test_base_graph_safe_access_protects_internal_state() -> None:
    """Correction 1: Ensure get_base_graph returns a deep copy protecting internal nodes, edges, and attributes."""
    service = GraphService()
    g = create_synthetic_road_graph()
    # Add nested mutable structure to verify deep copy protection
    g.nodes[1]["tags"] = {"lanes": 2, "hazards": ["debris"]}
    g[1][2][0]["nested_info"] = {"lane_ids": [10, 20]}
    service.set_graph(g)

    # Caller gets base graph and attempts to mutate top-level and nested attributes
    external_base = service.get_base_graph()
    external_base.remove_node(3)
    external_base.nodes[1]["tags"]["lanes"] = 99
    external_base.nodes[1]["tags"]["hazards"].append("fire")
    external_base[1][2][0]["nested_info"]["lane_ids"].append(30)

    # Internal base graph must remain completely untouched
    fresh_base = service.get_base_graph()
    assert fresh_base.has_node(3)
    assert fresh_base.number_of_nodes() == 4
    assert fresh_base.nodes[1]["tags"]["lanes"] == 2
    assert fresh_base.nodes[1]["tags"]["hazards"] == ["debris"]
    assert fresh_base[1][2][0]["nested_info"]["lane_ids"] == [10, 20]

    # Resetting working graph still restores all 4 nodes from intact base
    service.reset_working_graph()
    assert service.get_working_graph().has_node(3)


def test_edge_attribute_enrichment() -> None:
    service = GraphService()
    g = nx.MultiDiGraph()
    g.add_node(1, y=27.70, x=85.30)
    g.add_node(2, y=27.71, x=85.31)
    # Edge with length and speed provided, travel_time omitted
    g.add_edge(1, 2, key=0, length=1000.0, speed_kph=36.0)

    service.set_graph(g)
    edge_data = service.get_working_graph()[1][2][0]

    assert edge_data["length"] == 1000.0
    assert edge_data["speed_kph"] == 36.0
    # 36 km/h = 10 m/s; 1000m / 10 m/s = 100 seconds
    assert pytest.approx(edge_data["travel_time"], rel=1e-2) == 100.0


def test_find_nearest_node_with_synthetic_graph() -> None:
    service = GraphService()
    g = create_synthetic_road_graph()
    service.set_graph(g)

    # Point very close to Node 1 (27.7172, 85.3240)
    nearest = service.find_nearest_node(latitude=27.71725, longitude=85.32405)
    assert nearest == 1

    # Point very close to Node 4 (27.7145, 85.3275)
    nearest_to_4 = service.find_nearest_node(latitude=27.7144, longitude=85.3276)
    assert nearest_to_4 == 4


def test_find_nearest_node_no_valid_coordinates_raises_value_error() -> None:
    """Correction 2: Raise clear ValueError if graph has nodes but none have coordinate attributes."""
    service = GraphService()
    g = nx.MultiDiGraph()
    # Add nodes without 'x'/'y' or 'lat'/'lon'
    g.add_node("A", description="No coords")
    g.add_node("B", description="Also no coords")
    g.add_edge("A", "B", key=0, length=100.0)

    service.set_graph(g)

    with pytest.raises(ValueError, match="valid coordinate attributes"):
        service.find_nearest_node(latitude=27.71, longitude=85.32)


def test_maxspeed_parsing_numeric_and_units() -> None:
    """Correction 3: Handle numeric values, mph conversions, and km/h variants."""
    # Numeric
    assert GraphService._parse_speed_kph(50) == 50.0
    assert GraphService._parse_speed_kph(65.5) == 65.5

    # String with mph (1 mph = 1.609344 km/h)
    assert pytest.approx(GraphService._parse_speed_kph("30 mph"), rel=1e-3) == 30 * MPH_TO_KPH
    assert pytest.approx(GraphService._parse_speed_kph("45mph"), rel=1e-3) == 45 * MPH_TO_KPH

    # String with km/h
    assert GraphService._parse_speed_kph("50 km/h") == 50.0
    assert GraphService._parse_speed_kph("80 kph") == 80.0
    assert GraphService._parse_speed_kph("60kmh") == 60.0


def test_maxspeed_parsing_semicolon_and_lists() -> None:
    """Correction 2 & 3: Semicolon-separated values use conservative minimum speed for disaster rescue safety."""
    # Semicolon separated values -> conservative minimum (min speed)
    assert GraphService._parse_speed_kph("30; 50") == 30.0
    assert GraphService._parse_speed_kph("40; 60; 25") == 25.0

    # Semicolon separated with mph
    expected_min_mph = 30 * MPH_TO_KPH
    assert pytest.approx(GraphService._parse_speed_kph("30 mph; 50 mph"), rel=1e-3) == expected_min_mph

    # List inputs -> first valid speed
    assert pytest.approx(GraphService._parse_speed_kph(["30 mph", "50"]), rel=1e-3) == 30 * MPH_TO_KPH
    assert GraphService._parse_speed_kph([50, 60]) == 50.0


def test_maxspeed_parsing_invalid_fallbacks() -> None:
    """Correction 3: Fall back to configured default for invalid or non-numeric values."""
    default = 42.0
    assert GraphService._parse_speed_kph("signals", default_speed=default) == default
    assert GraphService._parse_speed_kph("none", default_speed=default) == default
    assert GraphService._parse_speed_kph("", default_speed=default) == default
    assert GraphService._parse_speed_kph(None, default_speed=default) == default
    assert GraphService._parse_speed_kph(-10, default_speed=default) == default
    assert GraphService._parse_speed_kph(float("nan"), default_speed=default) == default


def test_save_and_load_cache_roundtrip(tmp_path: Path) -> None:
    service = GraphService()
    g = create_synthetic_road_graph()
    service.set_graph(g)

    cache_file = tmp_path / "test_network.graphml"
    saved_path = service.save_to_cache(filepath=cache_file)
    assert saved_path.exists()
    assert saved_path == cache_file

    # Load into a fresh GraphService instance
    new_service = GraphService()
    loaded_graph = new_service.load_from_cache(filepath=cache_file)

    assert new_service.is_loaded
    assert isinstance(loaded_graph, nx.MultiDiGraph)
    assert loaded_graph.number_of_nodes() == 4
    assert loaded_graph.number_of_edges() == 5

    # Verify parallel edges and attributes survived roundtrip
    assert loaded_graph.has_edge(1, 2, key=0) or loaded_graph.has_edge("1", "2", key="0")
    assert loaded_graph.number_of_edges() == 5


def test_graph_config_preservation_on_cache_roundtrip(tmp_path: Path) -> None:
    """Correction 4: Preserve GraphConfig and default speed settings across cache save/load."""
    service = GraphService()
    g = create_synthetic_road_graph()
    custom_config = GraphConfig(
        center_lat=27.7172,
        center_lon=85.3240,
        dist_meters=2500.0,
        place_name="Kathmandu Central",
        default_speed_kmh=55.0,
    )
    service.set_graph(g, config=custom_config)

    cache_file = tmp_path / "configured_network.graphml"
    service.save_to_cache(filepath=cache_file)

    # Fresh service loads without passing explicit config
    new_service = GraphService()
    new_service.load_from_cache(filepath=cache_file)

    loaded_cfg = new_service.get_config()
    assert loaded_cfg is not None
    assert loaded_cfg.default_speed_kmh == 55.0
    assert loaded_cfg.dist_meters == 2500.0
    assert loaded_cfg.place_name == "Kathmandu Central"
    assert loaded_cfg.center_lat == 27.7172
    assert loaded_cfg.center_lon == 85.3240


def test_pure_networkx_cache_save_load(tmp_path: Path) -> None:
    """Correction 5: NetworkX GraphML read/write handles synthetic graphs without OSMnx."""
    service = GraphService()
    g = nx.MultiDiGraph()
    g.add_node("hub_a", y=37.77, x=-122.41)
    g.add_node("hub_b", y=37.78, x=-122.42)
    g.add_edge("hub_a", "hub_b", key=0, length=500.0, maxspeed="35 mph")
    g.add_edge("hub_a", "hub_b", key=1, length=600.0, maxspeed="45 mph")

    service.set_graph(g)

    cache_file = tmp_path / "pure_nx_cache.graphml"
    saved = service.save_to_cache(filepath=cache_file, use_osmnx=False)
    assert saved.exists()

    new_service = GraphService()
    loaded = new_service.load_from_cache(filepath=cache_file, use_osmnx=False)

    assert loaded.has_node("hub_a")
    assert loaded.has_node("hub_b")
    assert loaded.number_of_edges() == 2
    # Verify speed was converted from mph
    edge_0 = loaded["hub_a"]["hub_b"][0] if 0 in loaded["hub_a"]["hub_b"] else loaded["hub_a"]["hub_b"]["0"]
    assert pytest.approx(edge_0["speed_kph"], rel=1e-2) == 35 * MPH_TO_KPH


def test_load_cache_non_existent_file() -> None:
    service = GraphService()
    with pytest.raises(FileNotFoundError):
        service.load_from_cache("non_existent_path.graphml")


def test_find_nearest_node_empty_graph() -> None:
    service = GraphService()
    empty_graph = nx.MultiDiGraph()
    service.set_graph(empty_graph)

    with pytest.raises(ValueError, match="contains no nodes"):
        service.find_nearest_node(0.0, 0.0)


def test_set_graph_type_validation() -> None:
    service = GraphService()
    with pytest.raises(TypeError, match="Expected a NetworkX graph"):
        service.set_graph("not_a_graph")  # type: ignore[arg-type]


def test_graph_config_defaults() -> None:
    config = GraphConfig(center_lat=27.7172, center_lon=85.3240)
    assert config.dist_meters == 1000.0
    assert config.network_type == "drive"
    assert config.default_speed_kmh == 40.0
    assert config.bbox is None
    assert config.place_name is None


def test_osmnx_and_pure_networkx_cache_paths(tmp_path: Path) -> None:
    """Correction 4 & 5: Verify both OSMnx and pure NetworkX cache paths preserve structure, parallel edges, and GraphConfig."""
    service = GraphService()
    g = create_synthetic_road_graph()
    custom_cfg = GraphConfig(
        center_lat=27.7172,
        center_lon=85.3240,
        dist_meters=1800.0,
        default_speed_kmh=48.0,
    )
    service.set_graph(g, config=custom_cfg)

    # 1. Pure NetworkX cache path (use_osmnx=False)
    p_nx = tmp_path / "cache_pure_nx.graphml"
    service.save_to_cache(filepath=p_nx, use_osmnx=False)
    assert p_nx.exists()

    s_nx = GraphService()
    g_nx = s_nx.load_from_cache(filepath=p_nx, use_osmnx=False)
    assert isinstance(g_nx, nx.MultiDiGraph)
    assert g_nx.number_of_edges() == 5
    assert g_nx.has_edge(1, 2, key=0)
    assert g_nx.has_edge(1, 2, key=1)
    assert s_nx.get_config() is not None
    assert s_nx.get_config().default_speed_kmh == 48.0
    assert s_nx.get_config().dist_meters == 1800.0

    # 2. OSMnx cache path (use_osmnx=True)
    p_ox = tmp_path / "cache_osmnx.graphml"
    service.save_to_cache(filepath=p_ox, use_osmnx=True)
    assert p_ox.exists()

    s_ox = GraphService()
    g_ox = s_ox.load_from_cache(filepath=p_ox, use_osmnx=True)
    assert isinstance(g_ox, nx.MultiDiGraph)
    assert g_ox.number_of_edges() == 5
    assert g_ox.has_edge(1, 2, key=0)
    assert g_ox.has_edge(1, 2, key=1)
    assert s_ox.get_config() is not None
    assert s_ox.get_config().default_speed_kmh == 48.0

