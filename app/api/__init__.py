import asyncio
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse


@asynccontextmanager
async def lifespan(application: FastAPI):
    task = asyncio.create_task(_startup_init())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _startup_init():
    await asyncio.sleep(3)
    try:
        loop = asyncio.get_running_loop()
        from app.api.deps import _session_factory
        await loop.run_in_executor(None, _ensure_column, _session_factory)
        from app.risk.scanner import init_scanner
        scanner = init_scanner(_session_factory)
        await scanner.run()
    except Exception:
        import logging
        logging.getLogger("uvicorn").exception("startup init failed")


def _ensure_column(session_factory):
    try:
        db = session_factory()
        with db.connection() as conn:
            conn.exec_driver_sql(
                "ALTER TABLE strategy_configs ADD COLUMN IF NOT EXISTS max_loss_usdt FLOAT"
            )
            db.commit()
        db.close()
    except Exception:
        pass


app = FastAPI(title="FuRun API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api import auth, opportunities, positions, settings, strategies, tasks, ws  # noqa: E402,F401
from app.api import admin, dashboard  # noqa: E402
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
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])

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
