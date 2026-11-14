"""
USGS Earthquake API integration service for QuakeResQ.

Fetches real-time and historical earthquake event data from the official USGS
FDSN Event Web Service using GeoJSON format. Extracts and normalizes event fields
into standardized data structures independent of database persistence or web frameworks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USGS_EVENT_API_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


class EarthquakeServiceError(Exception):
    """Base exception for all EarthquakeService errors."""


class EarthquakeAPIError(EarthquakeServiceError):
    """Raised when the USGS API returns an HTTP error status or an error response payload."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class EarthquakeTimeoutError(EarthquakeServiceError):
    """Raised when a request to the USGS API times out."""


class EarthquakeNetworkError(EarthquakeServiceError):
    """Raised when a connection error or network failure occurs while reaching the USGS API."""


class EarthquakeParseError(EarthquakeServiceError):
    """Raised when the USGS API response body cannot be parsed as valid JSON or GeoJSON."""


@dataclass
class NormalizedEarthquake:
    """Normalized earthquake record extracted from USGS GeoJSON."""

    external_id: str
    magnitude: float
    location_name: str
    latitude: float
    longitude: float
    depth_km: float | None
    occurred_at: datetime
    url: str | None = None
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        """Convert normalized earthquake to a dictionary matching database and schema fields."""
        return {
            "external_id": self.external_id,
            "magnitude": self.magnitude,
            "location_name": self.location_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "depth_km": self.depth_km,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "url": self.url,
            "status": self.status,
        }


class EarthquakeService:
    """Service for querying and normalizing earthquake events from the USGS FDSN API."""

    def __init__(
        self,
        base_url: str = USGS_EVENT_API_URL,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._client = client

    def fetch_earthquakes(
        self,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        min_magnitude: float | None = 2.5,
        max_magnitude: float | None = None,
        limit: int | None = 100,
        min_latitude: float | None = None,
        max_latitude: float | None = None,
        min_longitude: float | None = None,
        max_longitude: float | None = None,
        order_by: str = "time",
        timeout: float | None = None,
    ) -> list[NormalizedEarthquake]:
        """
        Fetch earthquake events from the USGS FDSN Event API and return normalized records.

        Args:
            start_time: Start time (datetime or ISO 8601 string).
            end_time: End time (datetime or ISO 8601 string).
            min_magnitude: Minimum earthquake magnitude filter.
            max_magnitude: Maximum earthquake magnitude filter.
            limit: Maximum number of events to return.
            min_latitude: Southern boundary latitude (-90 to 90).
            max_latitude: Northern boundary latitude (-90 to 90).
            min_longitude: Western boundary longitude (-180 to 180).
            max_longitude: Eastern boundary longitude (-180 to 180).
            order_by: Result ordering ("time", "time-asc", "magnitude", "magnitude-asc").
            timeout: Optional per-request timeout override in seconds.

        Returns:
            List of NormalizedEarthquake objects.

        Raises:
            EarthquakeTimeoutError: If the HTTP request times out.
            EarthquakeNetworkError: If a connection or network failure occurs.
            EarthquakeAPIError: If the API returns a non-200 HTTP status code.
            EarthquakeParseError: If the response cannot be parsed as valid JSON/GeoJSON.
        """
        params = self._build_query_params(
            start_time=start_time,
            end_time=end_time,
            min_magnitude=min_magnitude,
            max_magnitude=max_magnitude,
            limit=limit,
            min_latitude=min_latitude,
            max_latitude=max_latitude,
            min_longitude=min_longitude,
            max_longitude=max_longitude,
            order_by=order_by,
        )

        req_timeout = timeout if timeout is not None else self.timeout

        try:
            if self._client is not None:
                response = self._client.get(self.base_url, params=params, timeout=req_timeout)
            else:
                with httpx.Client(timeout=req_timeout) as client:
                    response = client.get(self.base_url, params=params)

            if response.status_code != 200:
                raise EarthquakeAPIError(
                    f"USGS API error HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )

            try:
                data = response.json()
            except Exception as e:
                raise EarthquakeParseError(f"Failed to parse USGS response as JSON: {e}") from e

        except httpx.TimeoutException as e:
            raise EarthquakeTimeoutError(
                f"USGS API request timed out after {req_timeout}s: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            raise EarthquakeAPIError(
                f"USGS API HTTP status error {status}: {e}",
                status_code=status,
            ) from e
        except httpx.RequestError as e:
            raise EarthquakeNetworkError(f"USGS API network/connection error: {e}") from e

        return self.normalize_geojson(data)

    def get_earthquake_by_id(
        self, event_id: str, timeout: float | None = None
    ) -> NormalizedEarthquake | None:
        """
        Fetch a single earthquake by its USGS event ID.

        Returns None if the event is not found (HTTP 404).
        """
        if not event_id or not str(event_id).strip():
            raise ValueError("Event ID must be a non-empty string.")

        params = {"eventid": str(event_id).strip(), "format": "geojson"}
        req_timeout = timeout if timeout is not None else self.timeout

        try:
            if self._client is not None:
                response = self._client.get(self.base_url, params=params, timeout=req_timeout)
            else:
                with httpx.Client(timeout=req_timeout) as client:
                    response = client.get(self.base_url, params=params)

            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise EarthquakeAPIError(
                    f"USGS API error HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )

            try:
                data = response.json()
            except Exception as e:
                raise EarthquakeParseError(f"Failed to parse USGS response as JSON: {e}") from e

        except httpx.TimeoutException as e:
            raise EarthquakeTimeoutError(f"USGS API request timed out: {e}") from e
        except httpx.RequestError as e:
            raise EarthquakeNetworkError(f"USGS API connection error: {e}") from e

        if not isinstance(data, dict):
            raise EarthquakeParseError("Expected GeoJSON feature dictionary.")

        return self.normalize_feature(data)

    def normalize_geojson(self, data: Any) -> list[NormalizedEarthquake]:
        """
        Parse a GeoJSON dictionary and return a list of normalized earthquake records.
        """
        if not isinstance(data, dict):
            raise EarthquakeParseError(
                f"Expected GeoJSON to be a dictionary, got {type(data).__name__}"
            )

        if "error" in data:
            raise EarthquakeAPIError(f"USGS API returned error payload: {data.get('message', data)}")

        features = data.get("features")
        if features is None:
            if data.get("type") == "FeatureCollection":
                return []
            if data.get("type") == "Feature":
                single = self.normalize_feature(data)
                return [single] if single else []
            raise EarthquakeParseError("GeoJSON missing 'features' list.")

        if not isinstance(features, list):
            raise EarthquakeParseError(
                f"Expected 'features' to be a list, got {type(features).__name__}"
            )

        normalized_list: list[NormalizedEarthquake] = []
        for feature in features:
            parsed = self.normalize_feature(feature)
            if parsed is not None:
                normalized_list.append(parsed)

        return normalized_list

    def normalize_feature(self, feature: Any) -> NormalizedEarthquake | None:
        """
        Extract and normalize fields from a single GeoJSON Feature object.

        Returns None if required geometric coordinates cannot be extracted or are invalid.
        Safely handles missing or irregular attributes.
        """
        if not isinstance(feature, dict):
            return None

        # 1. Geometry and coordinates: [longitude, latitude, depth]
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            return None

        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
            return None

        try:
            longitude = float(coordinates[0])
            latitude = float(coordinates[1])
        except (ValueError, TypeError):
            return None

        if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
            return None

        # Depth in km (GeoJSON coordinates[2] or properties["depth"])
        depth_km: float | None = None
        if len(coordinates) >= 3 and coordinates[2] is not None:
            try:
                depth_val = float(coordinates[2])
                depth_km = max(0.0, depth_val)
            except (ValueError, TypeError):
                depth_km = None

        props = feature.get("properties")
        if not isinstance(props, dict):
            props = {}

        if depth_km is None and props.get("depth") is not None:
            try:
                depth_km = max(0.0, float(props["depth"]))
            except (ValueError, TypeError):
                depth_km = None

        # 2. External USGS event ID
        ext_id = feature.get("id") or props.get("code")
        if not ext_id and props.get("ids"):
            ext_id = props["ids"].strip(",").split(",")[0]
        if not ext_id:
            ext_id = f"usgs_{latitude:.4f}_{longitude:.4f}"
        external_id = str(ext_id).strip()[:100]

        # 3. Magnitude (safe float >= 0)
        mag_raw = props.get("mag")
        if mag_raw is not None:
            try:
                mag_val = float(mag_raw)
                magnitude = max(0.0, mag_val)
            except (ValueError, TypeError):
                magnitude = 0.0
        else:
            magnitude = 0.0

        # 4. Location name
        place_raw = props.get("place") or props.get("title")
        if place_raw is not None and str(place_raw).strip():
            location_name = str(place_raw).strip()[:200]
        else:
            location_name = "Unknown location"

        # 5. Event time (epoch milliseconds to UTC datetime)
        occurred_at: datetime
        time_raw = props.get("time")
        if time_raw is not None:
            try:
                time_num = float(time_raw)
                occurred_at = datetime.fromtimestamp(time_num / 1000.0, tz=timezone.utc)
            except (ValueError, TypeError, OverflowError, OSError):
                try:
                    occurred_at = datetime.fromisoformat(str(time_raw))
                    if occurred_at.tzinfo is None:
                        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                except Exception:
                    occurred_at = datetime.now(timezone.utc)
        else:
            occurred_at = datetime.now(timezone.utc)

        # 6. USGS Event URL
        url_raw = props.get("url")
        url = str(url_raw).strip() if url_raw else None

        return NormalizedEarthquake(
            external_id=external_id,
            magnitude=magnitude,
            location_name=location_name,
            latitude=latitude,
            longitude=longitude,
            depth_km=depth_km,
            occurred_at=occurred_at,
            url=url,
            status="active",
        )

    @classmethod
    def _build_query_params(
        cls,
        start_time: datetime | str | None,
        end_time: datetime | str | None,
        min_magnitude: float | None,
        max_magnitude: float | None,
        limit: int | None,
        min_latitude: float | None,
        max_latitude: float | None,
        min_longitude: float | None,
        max_longitude: float | None,
        order_by: str,
    ) -> dict[str, str]:
        """Construct dictionary of query parameters for the USGS API request."""
        params: dict[str, str] = {"format": "geojson"}

        if start_time is not None:
            params["starttime"] = cls._format_timestamp(start_time)
        if end_time is not None:
            params["endtime"] = cls._format_timestamp(end_time)
        if min_magnitude is not None:
            params["minmagnitude"] = str(float(min_magnitude))
        if max_magnitude is not None:
            params["maxmagnitude"] = str(float(max_magnitude))
        if limit is not None:
            if limit <= 0:
                raise ValueError(f"Limit must be positive, got {limit}")
            params["limit"] = str(int(limit))
        if min_latitude is not None:
            params["minlatitude"] = str(float(min_latitude))
        if max_latitude is not None:
            params["maxlatitude"] = str(float(max_latitude))
        if min_longitude is not None:
            params["minlongitude"] = str(float(min_longitude))
        if max_longitude is not None:
            params["maxlongitude"] = str(float(max_longitude))
        if order_by:
            params["orderby"] = str(order_by)

        return params

    @staticmethod
    def _format_timestamp(val: datetime | str) -> str:
        """Format datetime as UTC ISO-8601 string, or validate non-empty string."""
        if isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
            return val.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        if isinstance(val, str):
            clean = val.strip()
            if not clean:
                raise ValueError("Timestamp string cannot be empty.")
            return clean
        raise TypeError(f"Expected datetime or str for timestamp, got {type(val).__name__}")
