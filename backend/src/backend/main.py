"""FastAPI application factory and lifespan management.

Creates the backend application with CORS middleware, mounts all
routes and WebSocket handlers, and manages startup/shutdown lifecycle
including SPRT recovery.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from shared.storage.file_store import FileGameRepository, FileSPRTTestRepository

from backend.routes import engines, games, openings, sprt
from backend.services.engine_pool import EnginePool
from backend.services.game_manager import GameManager
from backend.services.sprt_service import SPRTService
from backend.ws import play as ws_play
from backend.ws import sprt as ws_sprt

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path("data")
_DEFAULT_RUNNER_PYTHON = "sprt-runner/.venv/bin/python"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan: startup and shutdown hooks.

    Startup:
        - Marks any ``RUNNING`` SPRT tests as ``CANCELLED`` (recovery).

    Shutdown:
        - Terminates all running SPRT subprocesses.
        - Shuts down the engine pool.
    """
    # Startup
    sprt_service: SPRTService = app.state.sprt_service
    recovered = await sprt_service.recover_on_startup()
    if recovered:
        logger.info("Recovered %d stale SPRT test(s) on startup", recovered)

    yield

    # Shutdown
    await sprt_service.shutdown()
    engine_pool: EnginePool = app.state.engine_pool
    await engine_pool.shutdown()
    logger.info("Backend shut down")


def create_app(
    *,
    data_dir: Path | None = None,
    runner_python: str | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        data_dir: Root data directory for storage.
        runner_python: Path to the Python interpreter in the
            sprt-runner virtualenv.
        cors_origins: Allowed CORS origins. Defaults to Vite dev
            server origin.

    Returns:
        Configured :class:`FastAPI` application.
    """
    app = FastAPI(
        title="chess-vibe",
        description="Chess engine development suite backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    origins = cors_origins or ["http://localhost:5173"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Dependencies
    effective_data_dir = data_dir or _DEFAULT_DATA_DIR
    effective_runner_python = runner_python or _DEFAULT_RUNNER_PYTHON

    game_repo = FileGameRepository(effective_data_dir)
    sprt_repo = FileSPRTTestRepository(effective_data_dir)
    engine_pool = EnginePool()
    game_manager = GameManager(engine_pool, game_repo)
    sprt_service = SPRTService(
        sprt_repo,
        runner_python=effective_runner_python,
    )

    # Store on app.state for access in route handlers
    app.state.data_dir = effective_data_dir
    app.state.game_repo = game_repo
    app.state.sprt_repo = sprt_repo
    app.state.engine_pool = engine_pool
    app.state.game_manager = game_manager
    app.state.sprt_service = sprt_service

    # Mount routes
    app.include_router(engines.router)
    app.include_router(games.router)
    app.include_router(sprt.router)
    app.include_router(openings.router)

    # Mount WebSocket handlers
    app.include_router(ws_play.router)
    app.include_router(ws_sprt.router)

    return app
