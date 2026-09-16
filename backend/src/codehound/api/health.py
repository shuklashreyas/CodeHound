from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Report process liveness; this does not check database readiness."""
    return HealthResponse(status="ok", service="codehound")
