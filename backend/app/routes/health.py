"""Application health endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
def get_health() -> dict[str, str]:
    return {"status": "ok"}
