"""FastAPI application entrypoint."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.agent.aiops.heartbeat import heartbeat_manager
from app.api import aiops, chat, file, health
from app.api.agent import router as agent_router
from app.config import config
from app.core.milvus_client import milvus_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle."""
    logger.info("=" * 60)
    logger.info(f"{config.app_name} v{config.app_version} starting")
    logger.info(f"Environment: {'debug' if config.debug else 'production'}")
    logger.info(f"Listening on http://{config.host}:{config.port}")
    logger.info(f"Docs: http://{config.host}:{config.port}/docs")

    logger.info("Connecting to Milvus...")
    milvus_manager.connect()
    logger.info("Milvus connected")
    heartbeat_manager.start()
    logger.info("=" * 60)

    yield

    logger.info("Stopping heartbeat manager...")
    heartbeat_manager.stop()
    logger.info("Closing Milvus connection...")
    milvus_manager.close()
    logger.info(f"{config.app_name} stopped")


app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    description="AgentAssistant with RAG chat and AIOps investigation runtimes",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(file.router, prefix="/api", tags=["file"])
app.include_router(aiops.router, prefix="/api", tags=["aiops"])
app.include_router(agent_router, tags=["agent"])

STATIC_DIR = "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    """Serve the frontend entrypoint."""
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": f"Welcome to {config.app_name} API",
        "version": config.app_version,
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level="info",
    )
