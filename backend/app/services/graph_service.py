"""
Road network graph management service.

Maintains base and working NetworkX MultiDiGraph instances, supports downloading
OpenStreetMap networks using OSMnx, saving and loading GraphML caches, and querying
nearest road nodes. Independent of FastAPI and database models.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import networkx as nx

MPH_TO_KPH = 1.609344


@dataclass
class GraphConfig:
    """Configuration for road network area and download parameters."""

    # Point-and-radius specification (default)
    center_lat: float | None = None
    center_lon: float | None = None
    dist_meters: float = 1000.0  # Configurable radius in meters

    # Optional bounding box specification: (north, south, east, west)
    bbox: tuple[float, float, float, float] | None = None

    # Optional place name query (e.g. "Palo Alto, California, USA")
    place_name: str | None = None

    # Network type for OSMnx (e.g., 'drive', 'all', 'walk')
    network_type: str = "drive"

    # Default travel speed in km/h if road maxspeed is absent or invalid
    default_speed_kmh: float = 40.0


class GraphService:
    """Manages road network graphs, OSMnx downloads, GraphML caches, and node lookups."""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._base_graph: nx.MultiDiGraph | None = None
        self._working_graph: nx.MultiDiGraph | None = None
        self._config: GraphConfig | None = None
        self._reset_callbacks: list[Callable[[], None]] = []

        if cache_dir is not None:
            self._cache_dir = Path(cache_dir)
        else:
            self._cache_dir = Path(__file__).resolve().parents[2] / "data" / "graph_cache"

    def register_reset_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be invoked whenever the working graph is reset or replaced."""
        if callback not in self._reset_callbacks:
            self._reset_callbacks.append(callback)

    def unregister_reset_callback(self, callback: Callable[[], None]) -> None:
        """Unregister a previously registered reset callback."""
        if callback in self._reset_callbacks:
            self._reset_callbacks.remove(callback)

    @property
    def is_loaded(self) -> bool:
        """Return True if a graph is loaded into memory."""
        return self._base_graph is not None and self._working_graph is not None

    @property
    def cache_dir(self) -> Path:
        """Directory path used for GraphML cache files."""
        return self._cache_dir

    def get_config(self) -> GraphConfig | None:
        """Return the currently active GraphConfig, if any."""
        return self._config

    def get_base_graph(self) -> nx.MultiDiGraph:
        """
        Get a safe deep copy of the base graph.

        Returns a deep copy to protect the internal base graph (including mutable
        node/edge attribute dictionaries and lists) from accidental external mutation.
        Raises RuntimeError if no graph has been loaded.
        """
        if self._base_graph is None:
            raise RuntimeError("Graph has not been loaded.")
        return copy.deepcopy(self._base_graph)

    def get_working_graph(self) -> nx.MultiDiGraph:
        """Get the active working graph. Raises RuntimeError if not loaded."""
        if self._working_graph is None:
            raise RuntimeError("Graph has not been loaded.")
        return self._working_graph

    def set_graph(
        self, graph: nx.MultiDiGraph | nx.DiGraph | nx.Graph, config: GraphConfig | None = None
    ) -> None:
        """Set base and working graphs from an existing NetworkX graph instance."""
        if isinstance(graph, nx.MultiDiGraph):
            base = copy.deepcopy(graph)
        elif isinstance(graph, (nx.DiGraph, nx.Graph)):
            base = nx.MultiDiGraph(copy.deepcopy(graph))
        else:
            raise TypeError(f"Expected a NetworkX graph, got {type(graph).__name__}")

        default_speed = config.default_speed_kmh if config else 40.0
        self._ensure_edge_attributes(base, default_speed=default_speed)

        self._base_graph = copy.deepcopy(base)
        self._working_graph = copy.deepcopy(base)
        self._config = config
        self._notify_reset_callbacks()

    def reset_working_graph(self) -> None:
        """Restore working graph to a fresh deep copy of the base graph."""
        if self._base_graph is None:
            raise RuntimeError("Cannot reset working graph: base graph is not loaded.")
        self._working_graph = copy.deepcopy(self._base_graph)
        self._notify_reset_callbacks()

    def _notify_reset_callbacks(self) -> None:
        """Invoke all registered reset callbacks safely."""
        if hasattr(self, "_reset_callbacks"):
            for callback in list(self._reset_callbacks):
                try:
                    callback()
                except Exception:
                    pass

    def download_from_osm(self, config: GraphConfig) -> nx.MultiDiGraph:
        """Download an OpenStreetMap road network using OSMnx and build a MultiDiGraph."""
        import osmnx as ox

        if config.place_name:
            graph = ox.graph_from_place(config.place_name, network_type=config.network_type)
        elif config.bbox:
            graph = ox.graph_from_bbox(bbox=config.bbox, network_type=config.network_type)
        elif config.center_lat is not None and config.center_lon is not None:
            graph = ox.graph_from_point(
                (config.center_lat, config.center_lon),
                dist=config.dist_meters,
                network_type=config.network_type,
            )
        else:
            raise ValueError(
                "Invalid GraphConfig: provide center_lat/center_lon, bbox, or place_name."
            )

        # Populate speeds and travel times
        try:
            graph = ox.routing.add_edge_speeds(graph, fallback=config.default_speed_kmh)
            graph = ox.routing.add_edge_travel_times(graph)
        except Exception:
            self._ensure_edge_attributes(graph, default_speed=config.default_speed_kmh)

        self.set_graph(graph, config)
        return self.get_working_graph()

    def save_to_cache(
        self, filepath: str | Path | None = None, use_osmnx: bool = False
    ) -> Path:
        """
        Save the base graph to a GraphML cache file.

        Uses NetworkX write_graphml by default for synthetic graphs, with an option
        to use OSMnx save_graphml when saving downloaded OSM graphs with complex attributes.
        """
        if self._base_graph is None:
            raise RuntimeError("Cannot save: graph is not loaded.")

        if filepath is None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            filename = "road_network.graphml"
            target_path = self._cache_dir / filename
        else:
            target_path = Path(filepath)
            target_path.parent.mkdir(parents=True, exist_ok=True)

        # Preserve GraphConfig in graph-level attributes
        if self._config is not None:
            self._base_graph.graph["default_speed_kmh"] = float(self._config.default_speed_kmh)
            self._base_graph.graph["network_type"] = str(self._config.network_type)
            self._base_graph.graph["dist_meters"] = float(self._config.dist_meters)
            if self._config.center_lat is not None:
                self._base_graph.graph["center_lat"] = float(self._config.center_lat)
            if self._config.center_lon is not None:
                self._base_graph.graph["center_lon"] = float(self._config.center_lon)
            if self._config.place_name is not None:
                self._base_graph.graph["place_name"] = str(self._config.place_name)

        if use_osmnx:
            try:
                import osmnx as ox

                ox.save_graphml(self._base_graph, filepath=target_path)
                return target_path
            except Exception:
                pass

        # NetworkX GraphML writer: serialize clean MultiDiGraph
        graph_to_write = self._prepare_for_graphml(self._base_graph)
        nx.write_graphml(graph_to_write, target_path)
        return target_path

    def load_from_cache(
        self,
        filepath: str | Path,
        config: GraphConfig | None = None,
        use_osmnx: bool = False,
    ) -> nx.MultiDiGraph:
        """
        Load a GraphML file into base and working graphs.

        Preserves GraphConfig/default speed settings from graph attributes or arguments.
        Uses pure NetworkX reading by default for fast, dependency-free synthetic graph loading.
        """
        target_path = Path(filepath)
        if not target_path.exists():
            raise FileNotFoundError(f"GraphML cache file not found: {target_path}")

        loaded_graph: nx.MultiDiGraph | None = None

        if use_osmnx:
            try:
                import osmnx as ox

                loaded_graph = ox.load_graphml(filepath=target_path)
            except Exception:
                loaded_graph = None

        if loaded_graph is None:
            raw = nx.read_graphml(target_path, force_multigraph=True)
            if isinstance(raw, nx.MultiDiGraph):
                loaded_graph = raw
            else:
                loaded_graph = nx.MultiDiGraph(raw)

            # Ensure numeric conversions for coordinates and edge metrics
            self._restore_graphml_types(loaded_graph)

        # Resolve effective config: passed config > graph metadata > default
        effective_config = self._resolve_config(loaded_graph, config)

        self._ensure_edge_attributes(loaded_graph, default_speed=effective_config.default_speed_kmh)
        self.set_graph(loaded_graph, config=effective_config)
        return self.get_working_graph()

    def find_nearest_node(self, latitude: float, longitude: float) -> Any:
        """
        Find the nearest road network node for a given latitude and longitude.

        Uses OSMnx's spatial index (KDTree) for real road graphs, falling back to
        an offline geometric distance lookup for synthetic test graphs.
        Raises ValueError if the graph has no nodes or none have coordinate attributes.
        """
        if self._working_graph is None:
            raise RuntimeError("Graph is not loaded.")

        if self._working_graph.number_of_nodes() == 0:
            raise ValueError("Graph contains no nodes.")

        # Primary method: OSMnx nearest_nodes
        try:
            import osmnx as ox

            nearest = ox.nearest_nodes(self._working_graph, X=longitude, Y=latitude)
            return nearest
        except Exception:
            pass

        # Offline fallback: Haversine distance over node coordinates
        return self._find_nearest_node_fallback(latitude, longitude)

    def _find_nearest_node_fallback(self, latitude: float, longitude: float) -> Any:
        """Offline calculation of closest node based on (y, x) or (lat, lon) attributes."""
        best_node = None
        min_dist = float("inf")

        for node_id, data in self._working_graph.nodes(data=True):
            node_lat = data.get("y", data.get("lat", data.get("latitude")))
            node_lon = data.get("x", data.get("lon", data.get("longitude")))

            if node_lat is None or node_lon is None:
                continue

            try:
                n_lat = float(node_lat)
                n_lon = float(node_lon)
            except (ValueError, TypeError):
                continue

            dist = self._haversine_distance(latitude, longitude, n_lat, n_lon)
            if dist < min_dist:
                min_dist = dist
                best_node = node_id

        if best_node is None:
            raise ValueError(
                "No nodes in the graph have valid coordinate attributes (latitude/longitude or y/x)."
            )

        return best_node

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Great-circle distance between two points on Earth in meters."""
        r = 6371000.0  # Earth radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(delta_phi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
        )
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return r * c

    @classmethod
    def _parse_speed_kph(cls, speed: Any, default_speed: float = 40.0) -> float:
        """
        Parse road speed to float km/h.

        Handles:
          - Numeric values: 50, 50.0
          - Strings with units: '30 mph', '30mph', '50 km/h', '50 kph'
          - Semicolon-separated values: '30; 50', '30 mph; 50 mph' (computes average)
          - Lists / tuples: ['30 mph', '50'], [40, 50]
          - Invalid / non-numeric text ('signals', 'none', '', None): falls back to default_speed.
        """
        if speed is None:
            return default_speed

        if isinstance(speed, (list, tuple)):
            for item in speed:
                parsed = cls._parse_single_speed(item)
                if parsed is not None:
                    return parsed
            return default_speed

        if isinstance(speed, (int, float)):
            if math.isnan(speed) or speed <= 0:
                return default_speed
            return float(speed)

        speed_str = str(speed).strip()
        if not speed_str:
            return default_speed

        if ";" in speed_str:
            parts = [p.strip() for p in speed_str.split(";") if p.strip()]
            valid_speeds: list[float] = []
            for part in parts:
                parsed = cls._parse_single_speed(part)
                if parsed is not None:
                    valid_speeds.append(parsed)
            if valid_speeds:
                # Use conservative minimum speed for disaster rescue routing safety
                return min(valid_speeds)
            return default_speed

        parsed = cls._parse_single_speed(speed_str)
        if parsed is not None:
            return parsed

        return default_speed

    @staticmethod
    def _parse_single_speed(val: Any) -> float | None:
        """Parse a single speed token to float km/h, converting mph if needed."""
        if val is None:
            return None
        if isinstance(val, (int, float)):
            if math.isnan(val) or val <= 0:
                return None
            return float(val)

        s = str(val).strip().lower()
        if not s:
            return None

        is_mph = "mph" in s

        match = re.search(r"[-+]?(?:\d*\.\d+|\d+)", s)
        if not match:
            return None

        try:
            num = float(match.group(0))
            if num <= 0:
                return None
            if is_mph:
                return num * MPH_TO_KPH
            return num
        except (ValueError, TypeError):
            return None

    @classmethod
    def _ensure_edge_attributes(cls, graph: nx.MultiDiGraph, default_speed: float = 40.0) -> None:
        """Ensure every edge has numeric length, speed_kph, and travel_time attributes."""
        for _u, _v, _k, data in graph.edges(keys=True, data=True):
            # 1. Length (meters)
            length = data.get("length")
            if length is None:
                length = 100.0
            else:
                try:
                    length = float(length)
                except (ValueError, TypeError):
                    length = 100.0
            data["length"] = length

            # 2. Speed (km/h)
            raw_speed = data.get("speed_kph", data.get("maxspeed"))
            speed_kph = cls._parse_speed_kph(raw_speed, default_speed=default_speed)
            data["speed_kph"] = speed_kph

            # 3. Travel time (seconds): distance_m / speed_m_s
            travel_time = data.get("travel_time")
            if travel_time is None:
                speed_m_per_s = max(speed_kph * (1000.0 / 3600.0), 0.1)
                travel_time = length / speed_m_per_s
            else:
                try:
                    travel_time = float(travel_time)
                except (ValueError, TypeError):
                    speed_m_per_s = max(speed_kph * (1000.0 / 3600.0), 0.1)
                    travel_time = length / speed_m_per_s
            data["travel_time"] = travel_time

    @staticmethod
    def _prepare_for_graphml(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
        """Return a copy with non-primitive attributes converted to strings for GraphML."""
        clean = copy.deepcopy(graph)
        for k, v in list(clean.graph.items()):
            if isinstance(v, (list, tuple, dict)):
                clean.graph[k] = str(v)

        for _n, data in clean.nodes(data=True):
            for k, v in list(data.items()):
                if isinstance(v, (list, tuple, dict)):
                    data[k] = str(v)

        for _u, _v, _k, data in clean.edges(keys=True, data=True):
            for k, v in list(data.items()):
                if isinstance(v, (list, tuple, dict)):
                    data[k] = str(v)
                elif hasattr(v, "__geo_interface__"):  # Shapely geometries
                    data[k] = str(v)

        return clean

    @staticmethod
    def _restore_graphml_types(graph: nx.MultiDiGraph) -> None:
        """Convert string coordinates and edge metrics back to floats after GraphML load."""
        if graph.nodes and all(isinstance(n, str) and n.isdigit() for n in graph.nodes):
            nx.relabel_nodes(graph, int, copy=False)

        for _n, data in graph.nodes(data=True):
            for coord in ("x", "y", "lat", "lon", "latitude", "longitude"):
                if coord in data and isinstance(data[coord], str):
                    try:
                        data[coord] = float(data[coord])
                    except ValueError:
                        pass

        for _u, _v, _k, data in graph.edges(keys=True, data=True):
            for attr in ("length", "speed_kph", "travel_time"):
                if attr in data and isinstance(data[attr], str):
                    try:
                        data[attr] = float(data[attr])
                    except ValueError:
                        pass

    @staticmethod
    def _resolve_config(graph: nx.MultiDiGraph, explicit_config: GraphConfig | None) -> GraphConfig:
        """Determine effective GraphConfig from explicit argument or graph metadata."""
        if explicit_config is not None:
            return explicit_config

        attrs = graph.graph
        if "default_speed_kmh" in attrs or "dist_meters" in attrs or "place_name" in attrs:
            try:
                center_lat = (
                    float(attrs["center_lat"]) if attrs.get("center_lat") is not None else None
                )
            except (ValueError, TypeError):
                center_lat = None

            try:
                center_lon = (
                    float(attrs["center_lon"]) if attrs.get("center_lon") is not None else None
                )
            except (ValueError, TypeError):
                center_lon = None

            try:
                dist_meters = float(attrs.get("dist_meters", 1000.0))
            except (ValueError, TypeError):
                dist_meters = 1000.0

            try:
                default_speed_kmh = float(attrs.get("default_speed_kmh", 40.0))
            except (ValueError, TypeError):
                default_speed_kmh = 40.0

            return GraphConfig(
                center_lat=center_lat,
                center_lon=center_lon,
                dist_meters=dist_meters,
                place_name=attrs.get("place_name"),
                network_type=attrs.get("network_type", "drive"),
                default_speed_kmh=default_speed_kmh,
            )

        return GraphConfig()

    def get_stats(self) -> dict[str, Any]:
        """Return summary statistics of the currently loaded working graph."""
        if self._working_graph is None:
            return {"loaded": False}

        return {
            "loaded": True,
            "node_count": self._working_graph.number_of_nodes(),
            "edge_count": self._working_graph.number_of_edges(),
            "is_directed": self._working_graph.is_directed(),
            "is_multigraph": self._working_graph.is_multigraph(),
        }
