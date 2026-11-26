"""Tests for the Bengaluru road graph setup script."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx
import pytest

from app.scripts.load_bengaluru_graph import (
    BENGALURU_LAT,
    BENGALURU_LON,
    BENGALURU_RADIUS_METERS,
    PLACE_NAME,
    load_bengaluru_graph,
    main,
)
from app.services.graph_service import GraphConfig, GraphService


def test_load_bengaluru_graph_uses_point_download_and_sets_metadata(tmp_path: Path) -> None:
    """Test that download_from_osm receives place_name=None so it uses graph_from_point."""
    mock_service = MagicMock(spec=GraphService)
    mock_service.cache_dir = tmp_path
    mock_service.get_stats.return_value = {
        "node_count": 120,
        "edge_count": 250,
        "is_directed": True,
        "is_multigraph": True,
    }
    expected_file = tmp_path / "road_network.graphml"
    expected_file.write_text("<graphml></graphml>", encoding="utf-8")
    mock_service.save_to_cache.return_value = expected_file

    mock_base = nx.MultiDiGraph()
    mock_working = nx.MultiDiGraph()
    mock_service._base_graph = mock_base
    mock_service._working_graph = mock_working

    with patch("app.scripts.load_bengaluru_graph.GraphService", return_value=mock_service):
        result_path = load_bengaluru_graph(cache_dir=tmp_path)

    assert result_path == expected_file
    mock_service.download_from_osm.assert_called_once()
    called_config: GraphConfig = mock_service.download_from_osm.call_args[0][0]

    # Verify place_name is None during download call to prevent graph_from_place execution
    assert called_config.place_name is None
    assert called_config.center_lat == BENGALURU_LAT
    assert called_config.center_lon == BENGALURU_LON
    assert called_config.dist_meters == BENGALURU_RADIUS_METERS
    assert called_config.network_type == "drive"
    assert called_config.default_speed_kmh == 40.0

    # Verify display metadata is attached after download and before saving
    assert mock_base.graph.get("place_name") == PLACE_NAME
    assert mock_working.graph.get("place_name") == PLACE_NAME
    assert mock_service._config.place_name == PLACE_NAME
    mock_service.save_to_cache.assert_called_once_with(expected_file, use_osmnx=True)


def test_regression_coordinate_based_download_not_graph_from_place(tmp_path: Path) -> None:
    """
    Regression test: verify integration through real GraphService calls
    ox.graph_from_point and strictly DOES NOT call ox.graph_from_place.
    """
    mock_ox = MagicMock()
    synthetic_graph = nx.MultiDiGraph()
    synthetic_graph.add_node(1, x=77.5946, y=12.9716)
    synthetic_graph.add_node(2, x=77.5950, y=12.9720)
    synthetic_graph.add_edge(1, 2, key=0, length=120.0, maxspeed="40")

    mock_ox.graph_from_point.return_value = synthetic_graph
    mock_ox.routing.add_edge_speeds.return_value = synthetic_graph
    mock_ox.routing.add_edge_travel_times.return_value = synthetic_graph

    def fake_save_graphml(graph, filepath):
        Path(filepath).write_text("<graphml></graphml>", encoding="utf-8")

    mock_ox.save_graphml.side_effect = fake_save_graphml

    with patch.dict("sys.modules", {"osmnx": mock_ox}):
        result_path = load_bengaluru_graph(cache_dir=tmp_path)

    # Assert ox.graph_from_point was invoked with exact point-and-radius arguments
    mock_ox.graph_from_point.assert_called_once_with(
        (BENGALURU_LAT, BENGALURU_LON),
        dist=BENGALURU_RADIUS_METERS,
        network_type="drive",
    )
    # Assert ox.graph_from_place was NEVER called (preventing polygon geocoding failure)
    assert not mock_ox.graph_from_place.called
    assert result_path.exists()
    assert result_path.name == "road_network.graphml"


def test_load_bengaluru_graph_handles_download_failure(tmp_path: Path) -> None:
    """Test load_bengaluru_graph handles and raises download failure gracefully."""
    mock_service = MagicMock(spec=GraphService)
    mock_service.cache_dir = tmp_path
    mock_service.download_from_osm.side_effect = ConnectionError("Overpass API timed out")

    with patch("app.scripts.load_bengaluru_graph.GraphService", return_value=mock_service):
        with pytest.raises(ConnectionError, match="Overpass API timed out"):
            load_bengaluru_graph(cache_dir=tmp_path)


def test_main_cli_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test main() CLI wrapper returns 0 on successful download."""
    mock_file = tmp_path / "road_network.graphml"
    mock_file.write_text("<graphml></graphml>", encoding="utf-8")

    with patch("app.scripts.load_bengaluru_graph.load_bengaluru_graph", return_value=mock_file):
        monkeypatch.setattr("sys.argv", ["load_bengaluru_graph.py", "--radius", "1500"])
        exit_code = main()

    assert exit_code == 0


def test_main_cli_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test main() CLI wrapper returns 1 on exception."""
    with patch(
        "app.scripts.load_bengaluru_graph.load_bengaluru_graph",
        side_effect=RuntimeError("Download failed"),
    ):
        monkeypatch.setattr("sys.argv", ["load_bengaluru_graph.py"])
        exit_code = main()

    assert exit_code == 1
