"""API router collection."""

from fastapi import APIRouter

from app.routes.earthquakes import router as earthquakes_router
from app.routes.health import router as health_router
from app.routes.roads import router as roads_router

# Versioned /api/v1 router containing Member 1 endpoints
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(roads_router)
api_v1_router.include_router(earthquakes_router)

# Top-level API router
api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(api_v1_router)

__all__ = ["api_router", "api_v1_router"]
