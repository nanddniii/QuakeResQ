"""
FastAPI route handlers for USGS Earthquake events.
Mounted under /api/v1/earthquakes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_earthquake_service
from app.models.earthquake import Earthquake
from app.schemas.api_models import (
    EarthquakeEventProperties,
    EarthquakeFeatureCollection,
    EarthquakeGeoJsonFeature,
    EarthquakeGeoJsonGeometry,
    EarthquakeGeoJsonMetadata,
)
from app.services.earthquake_service import (
    EarthquakeAPIError,
    EarthquakeNetworkError,
    EarthquakeParseError,
    EarthquakeService,
    EarthquakeTimeoutError,
    NormalizedEarthquake,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/earthquakes", tags=["Earthquakes"])


@router.get("/events", response_model=EarthquakeFeatureCollection)
def get_earthquake_events(
    min_magnitude: Annotated[
        float, Query(ge=0.0, le=10.0, description="Minimum earthquake magnitude filter")
    ] = 2.5,
    limit: Annotated[
        int, Query(ge=1, le=500, description="Maximum number of events to return")
    ] = 50,
    hours: Annotated[
        float, Query(ge=0.1, le=720.0, description="Lookback window in hours from now")
    ] = 24.0,
    start_time: Annotated[
        str | None, Query(description="Optional ISO 8601 start timestamp override")
    ] = None,
    end_time: Annotated[
        str | None, Query(description="Optional ISO 8601 end timestamp override")
    ] = None,
    sync_db: Annotated[
        bool,
        Query(description="If true, upsert fetched earthquakes into the SQLite database"),
    ] = False,
    db: Session = Depends(get_db),
    earthquake_service: EarthquakeService = Depends(get_earthquake_service),
) -> EarthquakeFeatureCollection:
    """
    Query real-time or historical earthquake events from the USGS FDSN Web Service.
    Returns GeoJSON RFC 7946 compliant FeatureCollection with [longitude, latitude, depth].
    Optionally synchronizes events into SQLite.
    """
    # Calculate start time from hours window if not explicitly provided
    resolved_start: datetime | str
    if start_time is not None and start_time.strip():
        resolved_start = start_time.strip()
    else:
        resolved_start = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        events: list[NormalizedEarthquake] = earthquake_service.fetch_earthquakes(
            start_time=resolved_start,
            end_time=end_time.strip() if end_time and end_time.strip() else None,
            min_magnitude=min_magnitude,
            limit=limit,
        )
    except EarthquakeTimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"USGS Earthquake API timed out: {e}",
        ) from e
    except EarthquakeNetworkError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"USGS Earthquake API network error: {e}",
        ) from e
    except (EarthquakeAPIError, EarthquakeParseError) as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"USGS Earthquake API returned error or invalid data: {e}",
        ) from e

    inserted_count = 0
    if sync_db and events:
        for ev in events:
            # Check if external_id exists in SQLite
            stmt = select(Earthquake).where(Earthquake.external_id == ev.external_id)
            existing = db.scalars(stmt).first()
            if existing is None:
                db_event = Earthquake(
                    external_id=ev.external_id,
                    magnitude=ev.magnitude,
                    location_name=ev.location_name,
                    latitude=ev.latitude,
                    longitude=ev.longitude,
                    depth_km=ev.depth_km,
                    occurred_at=ev.occurred_at,
                    status=ev.status,
                )
                db.add(db_event)
                inserted_count += 1
        db.commit()

    features: list[EarthquakeGeoJsonFeature] = []
    for ev in events:
        coords = [ev.longitude, ev.latitude]
        if ev.depth_km is not None:
            coords.append(ev.depth_km)

        features.append(
            EarthquakeGeoJsonFeature(
                id=ev.external_id,
                geometry=EarthquakeGeoJsonGeometry(
                    type="Point",
                    coordinates=coords,  # [longitude, latitude, depth_km]
                ),
                properties=EarthquakeEventProperties(
                    external_id=ev.external_id,
                    magnitude=ev.magnitude,
                    location_name=ev.location_name,
                    occurred_at=ev.occurred_at,
                    url=ev.url,
                    status=ev.status,
                    depth_km=ev.depth_km,
                ),
            )
        )

    metadata = EarthquakeGeoJsonMetadata(
        count=len(features),
        min_magnitude=min_magnitude,
        queried_at=datetime.now(timezone.utc),
        synced_to_db=sync_db,
        inserted_count=inserted_count if sync_db else None,
    )

    return EarthquakeFeatureCollection(
        type="FeatureCollection",
        metadata=metadata,
        features=features,
    )
