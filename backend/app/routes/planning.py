"""Planning and rescue-team allocation endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_routing_service
from app.schemas.entities import AllocationResponse
from app.services.allocation_service import (
    AllocationRoutingError,
    allocate_incidents,
)
from app.services.routing_service import RoutingService

router = APIRouter(prefix="/planning", tags=["Planning"])


@router.post("/allocate", response_model=AllocationResponse)
def allocate(
    db: Session = Depends(get_db),
    routing_service: RoutingService = Depends(get_routing_service),
) -> AllocationResponse:
    try:
        return allocate_incidents(db, routing_service)
    except AllocationRoutingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Road routing is unavailable: {exc}",
        ) from exc
