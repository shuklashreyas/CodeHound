from fastapi import FastAPI, Request

from codehound.api.github import router as github_router
from codehound.api.health import router as health_router

app = FastAPI(
    title="CodeHound",
    description="An independent verification layer for AI-generated code.",
    version="0.1.0",
)
app.include_router(health_router, prefix="/api")

app.include_router(github_router, prefix="/api")


@app.middleware("http")
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/github/")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response
