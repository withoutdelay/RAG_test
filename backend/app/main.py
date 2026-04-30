from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.documents import recover_document_parse_jobs_on_startup
from app.api.router import api_router
from app.config import get_settings
from app.db import get_engine
from app.services.parsing.docling_runtime import configure_docling_runtime, prewarm_docling_models


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_docling_runtime(settings)
    async with get_engine().begin() as connection:
        await connection.execute(text("SELECT 1"))
    if settings.docling_prewarm_models_on_startup:
        await asyncio.to_thread(prewarm_docling_models, settings)
    recovery = await recover_document_parse_jobs_on_startup()
    if recovery.get("recovered"):
        logger.info("Queued %s recovered document parse job(s)", recovery["recovered"])
    yield


app = FastAPI(title="Presale Copilot Backend", version="0.1.0", lifespan=lifespan)
settings = get_settings()
cors_origins = [
    origin.strip()
    for origin in str(settings.cors_allow_origins or "").split(",")
    if origin.strip()
]
if not cors_origins and settings.app_env.lower() == "development":
    cors_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
