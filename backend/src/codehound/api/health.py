from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str


@router.get("/health", response_model=HealthResponse, tags=["health"])
def health() -> HealthResponse:
    """Report process liveness; this does not check database readiness."""
    return HealthResponse(status="ok", service="codehound")


@router.get("/health/ready", response_model=HealthResponse, tags=["health"])
def readiness(request: Request, response: Response) -> HealthResponse:
    """Check database connectivity without exposing connection details."""
    try:
        with request.app.state.database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = 503
        return HealthResponse(status="unavailable", service="codehound")
    return HealthResponse(status="ready", service="codehound")
