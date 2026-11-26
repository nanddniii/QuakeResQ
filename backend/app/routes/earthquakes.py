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
        int, Query(ge=1, le=1000, description="Maximum number of events to return")
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
    feed: Annotated[
        str | None,
        Query(
            description="Optional USGS summary feed ('all_day', 'all_week', or full URL)",
        ),
    ] = None,
    region: Annotated[
        str | None,
        Query(description="Geographic region filter ('worldwide' or 'india')"),
    ] = None,
    sync_db: Annotated[
        bool,
        Query(description="If true, upsert fetched earthquakes into the SQLite database"),
    ] = False,
    db: Session = Depends(get_db),
    earthquake_service: EarthquakeService = Depends(get_earthquake_service),
) -> EarthquakeFeatureCollection:
    """
    Query real-time or historical earthquake events from the USGS FDSN Web Service
    or official summary GeoJSON feeds (all_day, all_week).
    Returns GeoJSON RFC 7946 compliant FeatureCollection with [longitude, latitude, depth].
    Optionally synchronizes events into SQLite, and falls back gracefully to cached SQLite records on network errors.
    """
    events: list[NormalizedEarthquake] = []
    cached_fallback = False

    try:
        if feed is not None and feed.strip() and hasattr(earthquake_service, "fetch_feed"):
            raw_events = earthquake_service.fetch_feed(feed=feed.strip())
            events = raw_events
        else:
            # Calculate start time from hours window if not explicitly provided
            resolved_start: datetime | str
            if start_time is not None and start_time.strip():
                resolved_start = start_time.strip()
            else:
                resolved_start = datetime.now(timezone.utc) - timedelta(hours=hours)

            events = earthquake_service.fetch_earthquakes(
                start_time=resolved_start,
                end_time=end_time.strip() if end_time and end_time.strip() else None,
                min_magnitude=min_magnitude if min_magnitude > 0 else None,
                limit=limit if (not region or region.lower() == "worldwide") else None,
            )
    except (
        EarthquakeTimeoutError,
        EarthquakeNetworkError,
        EarthquakeAPIError,
        EarthquakeParseError,
    ) as exc:
        # Fallback gracefully to cached SQLite records
        cached_stmt = select(Earthquake).order_by(Earthquake.occurred_at.desc())
        if min_magnitude > 0:
            cached_stmt = cached_stmt.where(Earthquake.magnitude >= min_magnitude)
        if region and region.lower() == "india":
            cached_stmt = cached_stmt.where(
                Earthquake.latitude >= 6.0,
                Earthquake.latitude <= 38.0,
                Earthquake.longitude >= 68.0,
                Earthquake.longitude <= 98.0,
            )
        cached_records = list(db.scalars(cached_stmt.limit(limit)).all())
        if cached_records:
            cached_fallback = True
            logger.warning(
                f"USGS Earthquake API unreachable ({exc}); returning {len(cached_records)} cached records from SQLite."
            )
            events = [
                NormalizedEarthquake(
                    external_id=q.external_id or f"cached_{q.id}",
                    magnitude=q.magnitude,
                    location_name=q.location_name,
                    latitude=q.latitude,
                    longitude=q.longitude,
                    depth_km=q.depth_km,
                    occurred_at=q.occurred_at
                    if q.occurred_at.tzinfo
                    else q.occurred_at.replace(tzinfo=timezone.utc),
                    url=None,
                    status=q.status,
                )
                for q in cached_records
            ]
        else:
            if isinstance(exc, EarthquakeTimeoutError):
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"USGS Earthquake API timed out: {exc}",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"USGS Earthquake API returned error or invalid data: {exc}",
            ) from exc

    # Filter by minimum magnitude if not already done
    if min_magnitude > 0 and not cached_fallback:
        events = [ev for ev in events if ev.magnitude >= min_magnitude]

    # Filter by geographic region if requested
    if region and region.lower() == "india" and not cached_fallback:
        events = [
            ev
            for ev in events
            if 6.0 <= ev.latitude <= 38.0 and 68.0 <= ev.longitude <= 98.0
        ]

    # Apply limit
    events = events[:limit]

    inserted_count = 0
    if sync_db and events and not cached_fallback:
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
        cached_fallback=cached_fallback,
        feed=feed,
        region=region,
    )

    return EarthquakeFeatureCollection(
        type="FeatureCollection",
        metadata=metadata,
        features=features,
    )
