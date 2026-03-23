from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.router import api_router
from app.db import get_engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with get_engine().begin() as connection:
        await connection.execute(text("SELECT 1"))
    yield


app = FastAPI(title="Presale Copilot Backend", version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
