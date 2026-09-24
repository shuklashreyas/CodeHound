from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from codehound.api.executions import router as executions_router
from codehound.api.github import router as github_router
from codehound.api.health import router as health_router
from codehound.api.verifications import router as verifications_router
from codehound.core.limits import SubmissionBodyLimit
from codehound.db.database import Database


@asynccontextmanager
async def lifespan(app):
    database = Database()
    try:
        await run_in_threadpool(database.migrate)
        app.state.database = database
        yield
    finally:
        database.close()


app = FastAPI(
    lifespan=lifespan,
    title="CodeHound",
    description="An independent verification layer for AI-generated code.",
    version="0.1.0",
)
app.add_middleware(SubmissionBodyLimit)
app.include_router(health_router, prefix="/api")

app.include_router(github_router, prefix="/api")
app.include_router(verifications_router, prefix="/api")
app.include_router(executions_router, prefix="/api")


@app.middleware("http")
async def private_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(
        ("/api/auth/", "/api/github/", "/api/verifications", "/api/executions")
    ):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response
