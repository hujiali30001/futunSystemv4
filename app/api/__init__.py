import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield


app = FastAPI(title="FuRun API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import auth, opportunities, positions, settings, strategies, tasks  # noqa: E402,F401
from app.api import admin  # noqa: E402

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(opportunities.router, prefix="/api/opportunities", tags=["opportunities"])
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(admin.router, prefix="/api")

_web_dir = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
if _web_dir.exists() and _web_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="frontend")
