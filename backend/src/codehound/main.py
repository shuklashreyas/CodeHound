from fastapi import FastAPI

from codehound.api.health import router as health_router

app = FastAPI(
    title="CodeHound",
    description="An independent verification layer for AI-generated code.",
    version="0.1.0",
)
app.include_router(health_router, prefix="/api")
