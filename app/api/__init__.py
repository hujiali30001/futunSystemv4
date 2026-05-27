import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


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

from app.api import auth, opportunities, strategies, tasks  # noqa: E402,F401

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(opportunities.router, prefix="/api/opportunities", tags=["opportunities"])
app.include_router(strategies.router, prefix="/api/strategies", tags=["strategies"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
