import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield


app = FastAPI(title="FuRun API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import auth, opportunities, positions, settings, strategies, tasks, ws  # noqa: E402,F401
from app.api import admin  # noqa: E402
from app.risk import api as risk_api  # noqa: E402

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(opportunities.router, prefix="/api/opportunities", tags=["opportunities"])
app.include_router(positions.router, prefix="/api", tags=["positions"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(ws.router, prefix="/api/ws", tags=["ws"])
app.include_router(admin.router, prefix="/api")
app.include_router(risk_api.router, prefix="/api/risk", tags=["risk"])

_web_dir = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
_index = _web_dir / "index.html"
if _index.exists():
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        served = _web_dir / full_path
        if served.is_file():
            content_type, _ = mimetypes.guess_type(str(served))
            return FileResponse(served, media_type=content_type or "application/octet-stream")
        return FileResponse(str(_index))
