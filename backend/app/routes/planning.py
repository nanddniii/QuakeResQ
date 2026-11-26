"""Planning and rescue-team allocation endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_routing_service
from app.schemas.entities import (
    AllocationResponse,
    ReplanningResponse,
    RouteRecalculationDetail,
)
from app.services.allocation_service import (
    AllocationRoutingError,
    allocate_incidents,
)
from app.services.replanning_service import (
    recalculate_active_routes,
    replan_incidents,
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


@router.post("/replan", response_model=ReplanningResponse)
def replan(
    allow_preemption: bool = Query(
        default=True,
        description="Allow justified preemption of lower-priority non-active operations for critical incidents",
    ),
    db: Session = Depends(get_db),
    routing_service: RoutingService = Depends(get_routing_service),
) -> ReplanningResponse:
    try:
        return replan_incidents(db, routing_service, allow_preemption=allow_preemption)
    except AllocationRoutingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Road routing is unavailable: {exc}",
        ) from exc


@router.post("/evaluate-closures", response_model=list[RouteRecalculationDetail])
def evaluate_closures(
    db: Session = Depends(get_db),
    routing_service: RoutingService = Depends(get_routing_service),
) -> list[RouteRecalculationDetail]:
    try:
        return recalculate_active_routes(db, routing_service)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Closure route evaluation failed: {exc}",
        ) from exc
