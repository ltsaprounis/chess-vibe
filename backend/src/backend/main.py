"""FastAPI application factory and lifespan management.

Creates the backend application with CORS middleware, mounts all
routes and WebSocket handlers, and manages startup/shutdown lifecycle
including SPRT recovery.
"""

from __future__ import annotations

import logging
import math
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from shared.storage.file_store import (
    FileGameRepository,
    FileOpeningBookRepository,
    FileSPRTTestRepository,
)
from shared.utils import get_repo_root

from backend.routes import engines, games, openings, sprt
from backend.services.engine_pool import EnginePool
from backend.services.game_manager import GameManager
from backend.services.sprt_service import SPRTService
from backend.ws import play as ws_play
from backend.ws import sprt as ws_sprt

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path("data")
_DEFAULT_RUNNER_PYTHON = "sprt-runner/.venv/bin/python"


def _json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats with their string form.

    Starlette's ``JSONResponse`` renders with ``allow_nan=False``
    (spec-compliant JSON). A request validation error whose rejected
    input was NaN/Infinity (e.g. an ``elo0`` guarded by
    ``allow_inf_nan=False``) embeds that raw float in the error body
    via ``jsonable_encoder``, which otherwise crashes serialisation of
    the 422 response itself. Stringifying it keeps the response a
    clean 422 instead of a 500.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        mapping = cast("dict[Any, Any]", value)
        return {key: _json_safe(item) for key, item in mapping.items()}
    if isinstance(value, list):
        items = cast("list[Any]", value)
        return [_json_safe(item) for item in items]
    return value


async def _validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render request validation errors as JSON, guarding non-finite floats.

    Args:
        request: The incoming request (required by FastAPI's handler
            signature, unused here).
        exc: The validation error raised by Pydantic/FastAPI. Typed as
            ``Exception`` to match Starlette's ``ExceptionHandler``
            signature; narrowed to :class:`RequestValidationError`
            below since that is the only exception type this handler
            is registered for.

    Returns:
        A 422 JSON response with sanitised error details.

    Raises:
        Exception: Re-raises ``exc`` unchanged if it is not a
            :class:`RequestValidationError` (this handler is only ever
            registered for that type, but ``assert`` would be a no-op
            under ``python -O``, so the guard is an explicit ``raise``).
    """
    if not isinstance(exc, RequestValidationError):
        raise exc
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )


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

    # Guard against non-finite floats (NaN/Infinity) crashing the 422
    # response itself — see `_validation_exception_handler`.
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)

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
    effective_data_dir = (data_dir or _DEFAULT_DATA_DIR).resolve()
    repo_root = get_repo_root()
    effective_runner_python = runner_python or str(repo_root / _DEFAULT_RUNNER_PYTHON)

    game_repo = FileGameRepository(effective_data_dir)
    sprt_repo = FileSPRTTestRepository(effective_data_dir)
    book_repo = FileOpeningBookRepository(effective_data_dir)
    engine_pool = EnginePool()
    game_manager = GameManager(engine_pool, game_repo)
    sprt_service = SPRTService(
        sprt_repo,
        runner_python=effective_runner_python,
        repo_root=repo_root,
        data_dir=effective_data_dir,
    )

    # Store on app.state for access in route handlers
    app.state.game_repo = game_repo
    app.state.sprt_repo = sprt_repo
    app.state.book_repo = book_repo
    app.state.engine_pool = engine_pool
    app.state.game_manager = game_manager
    app.state.sprt_service = sprt_service

    # Mount routes
    app.include_router(engines.router, prefix="/api")
    app.include_router(games.router, prefix="/api")
    app.include_router(sprt.router, prefix="/api")
    app.include_router(openings.router, prefix="/api")

    # Mount WebSocket handlers
    app.include_router(ws_play.router)
    app.include_router(ws_sprt.router)

    return app
