"""Pydantic request/response models for the backend API.

Maps between the domain models in ``shared.storage.models`` and the
JSON representations exchanged over HTTP and WebSocket.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Time control
# ---------------------------------------------------------------------------


class TimeControlResponse(BaseModel):
    """Time control in API responses."""

    type: str
    movetime_ms: int | None = None
    wtime_ms: int | None = None
    btime_ms: int | None = None
    winc_ms: int | None = None
    binc_ms: int | None = None
    moves_to_go: int | None = None
    depth: int | None = None
    nodes: int | None = None


# ---------------------------------------------------------------------------
# Move
# ---------------------------------------------------------------------------


class MoveResponse(BaseModel):
    """A single move with evaluation data."""

    uci: str
    san: str
    fen_after: str
    score_cp: int | None = None
    score_mate: int | None = None
    depth: int | None = None
    seldepth: int | None = None
    pv: list[str] = Field(default_factory=list)
    nodes: int | None = None
    time_ms: int | None = None
    clock_white_ms: int | None = None
    clock_black_ms: int | None = None


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------


class GameResponse(BaseModel):
    """Full game record returned by GET /games/{id}."""

    id: str
    white_engine: str
    black_engine: str
    result: str
    moves: list[MoveResponse]
    created_at: datetime
    opening_name: str | None = None
    sprt_test_id: str | None = None
    start_fen: str | None = None
    time_control: TimeControlResponse | None = None


class GameSummaryResponse(BaseModel):
    """Abbreviated game record for list endpoints."""

    id: str
    white_engine: str
    black_engine: str
    result: str
    move_count: int
    created_at: datetime
    opening_name: str | None = None
    sprt_test_id: str | None = None


# ---------------------------------------------------------------------------
# SPRT
# ---------------------------------------------------------------------------


class SPRTTestCreateRequest(BaseModel):
    """Request body for POST /sprt/tests."""

    engine_a: str
    engine_b: str
    time_control: str = Field(description="Time control string, e.g. 'movetime=1000'")
    elo0: float = 0.0
    elo1: float = 5.0
    alpha: float = 0.05
    beta: float = 0.05
    book_id: str | None = None
    concurrency: int = 1


class SPRTTestResponse(BaseModel):
    """SPRT test status returned by GET /sprt/tests/{id}."""

    id: str
    engine_a: str
    engine_b: str
    time_control: TimeControlResponse
    elo0: float
    elo1: float
    alpha: float
    beta: float
    created_at: datetime
    status: str
    wins: int
    losses: int
    draws: int
    llr: float
    result: str | None = None
    completed_at: datetime | None = None


class SPRTTestCreatedResponse(BaseModel):
    """Response body for POST /sprt/tests."""

    id: str
    status: str


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class EngineResponse(BaseModel):
    """Engine descriptor from the registry."""

    id: str
    name: str


# ---------------------------------------------------------------------------
# Opening book
# ---------------------------------------------------------------------------


class OpeningBookResponse(BaseModel):
    """Opening book descriptor."""

    id: str
    name: str
    format: str


class OpeningBookUploadResponse(BaseModel):
    """Response after uploading an opening book."""

    id: str
    name: str
    format: str


# ---------------------------------------------------------------------------
# WebSocket messages
# ---------------------------------------------------------------------------


class PlayMoveMessage(BaseModel):
    """Player move sent over WebSocket."""

    type: str = "move"
    move: str = Field(description="Move in UCI notation")


class EngineMoveMessage(BaseModel):
    """Engine response sent over WebSocket."""

    type: str = "engine_move"
    move: str
    san: str
    fen: str
    score_cp: int | None = None
    score_mate: int | None = None
    depth: int | None = None
    pv: list[str] = Field(default_factory=list)


class GameOverMessage(BaseModel):
    """Game-over notification sent over WebSocket."""

    type: str = "game_over"
    result: str
    game_id: str


class ErrorMessage(BaseModel):
    """Error notification sent over WebSocket."""

    type: str = "error"
    message: str


class SPRTProgressMessage(BaseModel):
    """Live SPRT progress update over WebSocket."""

    type: str = "progress"
    wins: int
    losses: int
    draws: int
    llr: float
    lower_bound: float | None = None
    upper_bound: float | None = None
    games_total: int


class SPRTCompleteMessage(BaseModel):
    """SPRT completion notification over WebSocket."""

    type: str = "complete"
    result: str
    total_games: int
    llr: float
