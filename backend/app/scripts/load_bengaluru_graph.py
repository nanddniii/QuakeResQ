"""
One-time setup script to download and cache the Bengaluru road network graph.

Uses the existing GraphService and GraphConfig to download an OpenStreetMap
road network for Central Bengaluru using OSMnx and saves it as a GraphML
cache file for fast, offline startup in QuakeResQ.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure backend root is on sys.path if invoked directly
backend_root = Path(__file__).resolve().parents[2]
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from app.services.graph_service import GraphConfig, GraphService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Default Bengaluru Central demonstration parameters
BENGALURU_LAT = 12.9716
BENGALURU_LON = 77.5946
BENGALURU_RADIUS_METERS = 2500.0
NETWORK_TYPE = "drive"
DEFAULT_SPEED_KMH = 40.0
PLACE_NAME = "Bengaluru Central, Karnataka, India"


def load_bengaluru_graph(
    cache_dir: str | Path | None = None,
    lat: float = BENGALURU_LAT,
    lon: float = BENGALURU_LON,
    radius_meters: float = BENGALURU_RADIUS_METERS,
    network_type: str = NETWORK_TYPE,
    default_speed_kmh: float = DEFAULT_SPEED_KMH,
) -> Path:
    """
    Download road network for Central Bengaluru and save to GraphML cache.

    Reuses the existing GraphService and GraphConfig.
    """
    # Set place_name=None so download_from_osm() uses ox.graph_from_point()
    # with the configured coordinates and radius, avoiding place-polygon geocoding issues.
    download_config = GraphConfig(
        center_lat=lat,
        center_lon=lon,
        dist_meters=radius_meters,
        network_type=network_type,
        default_speed_kmh=default_speed_kmh,
        place_name=None,
    )

    print("=" * 60)
    print("QuakeResQ - Bengaluru Road Network Setup")
    print("=" * 60)
    print(f"Target Center:   Latitude {lat}, Longitude {lon}")
    print(f"Radius:          {radius_meters:.0f} metres ({radius_meters / 1000.0:.1f} km)")
    print(f"Network Type:    {network_type}")
    print(f"Default Speed:   {default_speed_kmh:.0f} km/h")
    print(f"Place Label:     {PLACE_NAME}")
    print("-" * 60)

    service = GraphService(cache_dir=cache_dir)
    target_cache_path = service.cache_dir / "road_network.graphml"
    print(f"Target Cache:    {target_cache_path}")
    print("\nDownloading road network from OpenStreetMap via OSMnx...")

    try:
        service.download_from_osm(download_config)
    except Exception as exc:
        print(f"\n[ERROR] Failed to download road network from OpenStreetMap: {exc}", file=sys.stderr)
        raise

    # Preserve display label in configuration and in base and working graphs
    service._config = GraphConfig(
        center_lat=lat,
        center_lon=lon,
        dist_meters=radius_meters,
        network_type=network_type,
        default_speed_kmh=default_speed_kmh,
        place_name=PLACE_NAME,
    )
    if getattr(service, "_base_graph", None) is not None:
        service._base_graph.graph["place_name"] = PLACE_NAME
    if getattr(service, "_working_graph", None) is not None:
        service._working_graph.graph["place_name"] = PLACE_NAME

    stats = service.get_stats()
    node_count = stats.get("node_count", 0)
    edge_count = stats.get("edge_count", 0)

    print("\n[SUCCESS] Download complete!")
    print(f"  Nodes:         {node_count:,}")
    print(f"  Edges:         {edge_count:,}")
    print(f"  Is Directed:   {stats.get('is_directed', True)}")
    print(f"  Is MultiGraph: {stats.get('is_multigraph', True)}")

    print(f"\nSaving graph to cache: {target_cache_path} ...")
    try:
        saved_path = service.save_to_cache(target_cache_path, use_osmnx=True)
    except Exception as exc:
        print(f"\n[ERROR] Failed to save graph to cache file: {exc}", file=sys.stderr)
        raise

    file_size_kb = saved_path.stat().st_size / 1024.0
    print(f"[SUCCESS] Graph cached successfully at:")
    print(f"  Path:          {saved_path.resolve()}")
    print(f"  File size:     {file_size_kb:.1f} KB ({file_size_kb / 1024.0:.2f} MB)")
    print("-" * 60)
    print("The road graph will now load automatically on FastAPI application startup.")
    print("=" * 60)

    return saved_path


def main() -> int:
    """CLI entry point for downloading the Bengaluru road graph."""
    parser = argparse.ArgumentParser(
        description="Download and cache OpenStreetMap road network for Bengaluru Central."
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=BENGALURU_LAT,
        help=f"Center latitude (default: {BENGALURU_LAT})",
    )
    parser.add_argument(
        "--lon",
        type=float,
        default=BENGALURU_LON,
        help=f"Center longitude (default: {BENGALURU_LON})",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=BENGALURU_RADIUS_METERS,
        help=f"Radius in meters (default: {BENGALURU_RADIUS_METERS})",
    )
    parser.add_argument(
        "--network-type",
        type=str,
        default=NETWORK_TYPE,
        help=f"OSM network type (default: {NETWORK_TYPE})",
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="Optional custom cache directory (default: backend/data/graph_cache)",
    )

    args = parser.parse_args()

    try:
        load_bengaluru_graph(
            cache_dir=args.cache_dir,
            lat=args.lat,
            lon=args.lon,
            radius_meters=args.radius,
            network_type=args.network_type,
        )
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
