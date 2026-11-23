"""
Unit tests for EarthquakeService in QuakeResQ.

Verifies USGS GeoJSON response parsing, coordinate extraction, field normalization,
error handling (timeouts, HTTP errors, network failures, invalid JSON), and parameter
formatting using mock HTTP transports without live internet access.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.services.earthquake_service import (
    EarthquakeAPIError,
    EarthquakeNetworkError,
    EarthquakeParseError,
    EarthquakeService,
    EarthquakeTimeoutError,
    NormalizedEarthquake,
)


@pytest.fixture
def sample_geojson() -> dict[str, Any]:
    """Sample GeoJSON FeatureCollection matching the official USGS FDSN format."""
    return {
        "type": "FeatureCollection",
        "metadata": {
            "generated": 1671539000000,
            "url": "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson",
            "title": "USGS Earthquakes",
            "status": 200,
            "api": "1.14.0",
            "count": 2,
        },
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "mag": 5.4,
                    "place": "12 km SW of Eureka, CA",
                    "time": 1671534800000,
                    "updated": 1671539000000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/nc73821036",
                    "status": "reviewed",
                    "code": "73821036",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-124.288, 40.732, 27.8],  # [lon, lat, depth]
                },
                "id": "nc73821036",
            },
            {
                "type": "Feature",
                "properties": {
                    "mag": 3.2,
                    "place": "5 km NE of San Jose, CA",
                    "time": 1671540000000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/nc73821037",
                    "status": "automatic",
                    "code": "73821037",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-121.848, 37.386, 8.5],
                },
                "id": "nc73821037",
            },
        ],
    }


def test_fetch_earthquakes_success(sample_geojson: dict[str, Any]) -> None:
    """Fetch earthquakes parses USGS GeoJSON features into normalized records."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "geojson"
        return httpx.Response(200, json=sample_geojson)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = EarthquakeService(client=client)

    events = service.fetch_earthquakes(min_magnitude=3.0, limit=10)
    assert len(events) == 2

    e1 = events[0]
    assert isinstance(e1, NormalizedEarthquake)
    assert e1.external_id == "nc73821036"
    assert e1.magnitude == 5.4
    assert e1.location_name == "12 km SW of Eureka, CA"
    # Note coordinate order: GeoJSON has [lon, lat, depth]
    assert e1.latitude == pytest.approx(40.732)
    assert e1.longitude == pytest.approx(-124.288)
    assert e1.depth_km == pytest.approx(27.8)
    assert e1.occurred_at == datetime.fromtimestamp(1671534800000 / 1000.0, tz=timezone.utc)
    assert e1.url == "https://earthquake.usgs.gov/earthquakes/eventpage/nc73821036"
    assert e1.status == "active"

    # Test dictionary export
    d = e1.to_dict()
    assert d["external_id"] == "nc73821036"
    assert d["magnitude"] == 5.4
    assert d["latitude"] == pytest.approx(40.732)
    assert d["longitude"] == pytest.approx(-124.288)
    assert d["depth_km"] == pytest.approx(27.8)
    assert d["status"] == "active"


def test_fetch_earthquakes_configurable_query_parameters() -> None:
    """Configurable start time, end time, minimum magnitude, bounding box and limit."""
    recorded_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal recorded_params
        recorded_params = dict(request.url.params)
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = EarthquakeService(client=client)

    t_start = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_end = datetime(2026, 11, 10, 18, 30, 0, tzinfo=timezone.utc)

    events = service.fetch_earthquakes(
        start_time=t_start,
        end_time=t_end,
        min_magnitude=4.5,
        max_magnitude=7.0,
        limit=50,
        min_latitude=32.0,
        max_latitude=42.0,
        min_longitude=-125.0,
        max_longitude=-115.0,
        order_by="magnitude",
    )

    assert events == []
    assert recorded_params["format"] == "geojson"
    assert recorded_params["starttime"] == "2026-11-01T12:00:00"
    assert recorded_params["endtime"] == "2026-11-10T18:30:00"
    assert recorded_params["minmagnitude"] == "4.5"
    assert recorded_params["maxmagnitude"] == "7.0"
    assert recorded_params["limit"] == "50"
    assert recorded_params["minlatitude"] == "32.0"
    assert recorded_params["maxlatitude"] == "42.0"
    assert recorded_params["minlongitude"] == "-125.0"
    assert recorded_params["maxlongitude"] == "-115.0"
    assert recorded_params["orderby"] == "magnitude"


def test_fetch_earthquakes_empty_results() -> None:
    """Empty features list in GeoJSON returns an empty list cleanly."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = EarthquakeService(client=client)

    events = service.fetch_earthquakes()
    assert events == []


def test_get_earthquake_by_id_found() -> None:
    """get_earthquake_by_id fetches and normalizes a single event."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["eventid"] == "us7000test"
        return httpx.Response(
            200,
            json={
                "type": "Feature",
                "id": "us7000test",
                "properties": {
                    "mag": 6.1,
                    "place": "Off coast of Oregon",
                    "time": 1672000000000,
                    "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000test",
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-126.5, 44.2, 10.0],
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = EarthquakeService(client=client)

    event = service.get_earthquake_by_id("us7000test")
    assert event is not None
    assert event.external_id == "us7000test"
    assert event.magnitude == 6.1
    assert event.latitude == pytest.approx(44.2)
    assert event.longitude == pytest.approx(-126.5)


def test_get_earthquake_by_id_not_found_returns_none() -> None:
    """get_earthquake_by_id returns None when USGS API returns HTTP 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Event not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = EarthquakeService(client=client)

    result = service.get_earthquake_by_id("nonexistent_id")
    assert result is None


def test_missing_and_irregular_fields_handled_safely() -> None:
    """Safely normalizes records when optional/non-standard fields are missing or irregular."""
    irregular_geojson = {
        "type": "FeatureCollection",
        "features": [
            # Feature 1: missing properties dict, 2D coordinates (no depth)
            {
                "type": "Feature",
                "id": "sparse_1",
                "geometry": {
                    "type": "Point",
                    "coordinates": [-122.0, 37.0],
                },
            },
            # Feature 2: string magnitude, place is null, missing id (falls back to code)
            {
                "type": "Feature",
                "properties": {
                    "code": "code_only_2",
                    "mag": "4.8",
                    "place": None,
                    "time": "2026-11-14T10:00:00+00:00",  # ISO string instead of epoch ms
                    "depth": 15.5,  # Depth in properties instead of coordinates
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-121.5, 36.5],
                },
            },
            # Feature 3: missing id and code (falls back to coordinate-based id)
            {
                "type": "Feature",
                "properties": {
                    "mag": None,  # Magnitude is None (uncalculated)
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [-120.0, 35.0, 5.0],
                },
            },
        ],
    }

    service = EarthquakeService()
    events = service.normalize_geojson(irregular_geojson)

    assert len(events) == 3

    # Feature 1
    assert events[0].external_id == "sparse_1"
    assert events[0].magnitude == 0.0
    assert events[0].location_name == "Unknown location"
    assert events[0].depth_km is None
    assert events[0].url is None

    # Feature 2
    assert events[1].external_id == "code_only_2"
    assert events[1].magnitude == pytest.approx(4.8)
    assert events[1].location_name == "Unknown location"
    assert events[1].depth_km == pytest.approx(15.5)

    # Feature 3
    assert events[2].external_id.startswith("usgs_")
    assert events[2].magnitude == 0.0
    assert events[2].depth_km == pytest.approx(5.0)


def test_invalid_coordinates_skipped_safely() -> None:
    """Features with corrupt, incomplete, or out-of-range coordinates are skipped without error."""
    corrupt_geojson = {
        "type": "FeatureCollection",
        "features": [
            # Only 1 coordinate value
            {"id": "bad1", "geometry": {"type": "Point", "coordinates": [-122.0]}},
            # Non-numeric coordinate
            {"id": "bad2", "geometry": {"type": "Point", "coordinates": ["invalid", 37.0]}},
            # Out-of-bounds latitude (> 90)
            {"id": "bad3", "geometry": {"type": "Point", "coordinates": [-122.0, 95.0]}},
            # Out-of-bounds longitude (< -180)
            {"id": "bad4", "geometry": {"type": "Point", "coordinates": [-195.0, 37.0]}},
            # Missing geometry
            {"id": "bad5", "geometry": None},
            # Non-dictionary feature
            "not a dict",
            # One valid feature
            {
                "id": "valid",
                "properties": {"mag": 3.0, "place": "Test"},
                "geometry": {"type": "Point", "coordinates": [-122.0, 37.0]},
            },
        ],
    }

    service = EarthquakeService()
    events = service.normalize_geojson(corrupt_geojson)

    assert len(events) == 1
    assert events[0].external_id == "valid"


def test_http_api_status_errors() -> None:
    """Non-200 HTTP response codes raise EarthquakeAPIError with the status code."""

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    service = EarthquakeService(client=httpx.Client(transport=httpx.MockTransport(handler_500)))
    with pytest.raises(EarthquakeAPIError) as exc_info:
        service.fetch_earthquakes()

    assert exc_info.value.status_code == 500
    assert "500" in str(exc_info.value)


def test_api_error_payload_in_geojson() -> None:
    """When USGS returns an error payload with 200/400, EarthquakeAPIError is raised."""

    def handler_error_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": 400, "message": "Invalid limit parameter"})

    service = EarthquakeService(client=httpx.Client(transport=httpx.MockTransport(handler_error_json)))
    with pytest.raises(EarthquakeAPIError, match="Invalid limit parameter"):
        service.fetch_earthquakes()


def test_timeout_error_handling() -> None:
    """Request timeout raises EarthquakeTimeoutError."""

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out", request=request)

    service = EarthquakeService(client=httpx.Client(transport=httpx.MockTransport(timeout_handler)))
    with pytest.raises(EarthquakeTimeoutError, match="timed out"):
        service.fetch_earthquakes()


def test_network_connection_error_handling() -> None:
    """Connection failure raises EarthquakeNetworkError."""

    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by peer", request=request)

    service = EarthquakeService(client=httpx.Client(transport=httpx.MockTransport(network_handler)))
    with pytest.raises(EarthquakeNetworkError, match="network/connection error"):
        service.fetch_earthquakes()


def test_invalid_json_response_handling() -> None:
    """Malformed or non-JSON response body raises EarthquakeParseError."""

    def html_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body>Gateway Timeout</body></html>")

    service = EarthquakeService(client=httpx.Client(transport=httpx.MockTransport(html_handler)))
    with pytest.raises(EarthquakeParseError, match="Failed to parse USGS response as JSON"):
        service.fetch_earthquakes()


def test_invalid_query_parameters_validation() -> None:
    """Invalid parameter values raise appropriate ValueError or TypeError before network call."""
    service = EarthquakeService()

    with pytest.raises(ValueError, match="Limit must be positive"):
        service.fetch_earthquakes(limit=0)

    with pytest.raises(ValueError, match="Timestamp string cannot be empty"):
        service.fetch_earthquakes(start_time="   ")

    with pytest.raises(TypeError, match="Expected datetime or str"):
        service.fetch_earthquakes(start_time=12345)  # type: ignore

    with pytest.raises(ValueError, match="Event ID must be a non-empty string"):
        service.get_earthquake_by_id("")
