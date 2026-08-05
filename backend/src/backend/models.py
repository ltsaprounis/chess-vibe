"""Pydantic request/response models for the backend API.

Maps between the domain models in ``shared.storage.models`` and the
JSON representations exchanged over HTTP and WebSocket.
"""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator
from shared.time_control import parse_time_control

# Upper bounds on request-supplied strings. These exist purely to keep
# malformed/adversarial input from reaching `SPRTService.start_test`
# (and from there, `argv` of the runner subprocess) — legitimate engine
# ids and time control strings are always far shorter than this.
_MAX_ENGINE_ID_LENGTH = 256
_MAX_TIME_CONTROL_LENGTH = 128

# Elo bounds are kept within a wide but finite range so an accepted
# value can never overflow `sprt_runner`'s logistic Elo model (which
# starts raising ``OverflowError`` around +/-123281).
_MIN_ELO = -1000.0
_MAX_ELO = 1000.0


def _has_non_printable(value: str) -> bool:
    """Return ``True`` if any character in ``value`` is non-printable.

    Catches control characters (e.g. NUL, newline) and Unicode format
    characters (e.g. zero-width space, byte-order mark) that would
    otherwise ride along into ``argv`` of the SPRT runner subprocess.

    Args:
        value: The string to check.

    Returns:
        Whether any character fails ``str.isprintable()``.
    """
    return any(not ch.isprintable() for ch in value)


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

    engine_a: str = Field(max_length=_MAX_ENGINE_ID_LENGTH, description="First engine identifier")
    engine_b: str = Field(max_length=_MAX_ENGINE_ID_LENGTH, description="Second engine identifier")
    time_control: str = Field(
        max_length=_MAX_TIME_CONTROL_LENGTH,
        description="Time control string, e.g. 'movetime=1000'",
    )
    elo0: float = Field(
        default=0.0,
        ge=_MIN_ELO,
        le=_MAX_ELO,
        allow_inf_nan=False,
        description="Null-hypothesis Elo bound",
    )
    elo1: float = Field(
        default=5.0,
        ge=_MIN_ELO,
        le=_MAX_ELO,
        allow_inf_nan=False,
        description="Alternative-hypothesis Elo bound",
    )
    alpha: float = Field(
        default=0.05, gt=0, lt=1, allow_inf_nan=False, description="Type-I error rate"
    )
    beta: float = Field(
        default=0.05, gt=0, lt=1, allow_inf_nan=False, description="Type-II error rate"
    )
    book_id: str | None = None
    concurrency: int = Field(default=1, ge=1, le=64, description="Number of concurrent workers")

    @field_validator("engine_a", "engine_b")
    @classmethod
    def _strip_and_require_nonempty(cls, value: str) -> str:
        """Reject blank, whitespace-only, or non-printable engine identifiers.

        Whitespace is stripped and re-checked here (bare ``min_length``
        would accept ``"   "``); the stripped value is what gets stored
        and forwarded to the runner. Non-printable characters (control
        characters, zero-width/format characters) are rejected outright
        so they cannot ride along into the runner subprocess's argv.

        Args:
            value: The raw engine identifier from the request body.

        Returns:
            The stripped engine identifier.

        Raises:
            ValueError: If the value is blank/whitespace-only after
                stripping, or contains a non-printable character.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty or whitespace-only")
        if _has_non_printable(stripped):
            raise ValueError("must not contain non-printable characters")
        return stripped

    @field_validator("time_control")
    @classmethod
    def _validate_time_control(cls, value: str) -> str:
        """Ensure the time control string is parseable.

        Delegates to :func:`shared.time_control.parse_time_control` so
        an unparseable string fails validation here (422) instead of
        surfacing deep inside :meth:`SPRTService.start_test` as a 500.
        The string is stripped first (matching ``engine_a``/``engine_b``)
        and checked for non-printable characters before being handed to
        the parser, since a bare parser failure can otherwise surface an
        internal Python error message (e.g. from the parser's own
        ``dict()`` construction) instead of a descriptive one.

        Args:
            value: The raw time control string from the request body.

        Returns:
            The stripped time control string.

        Raises:
            ValueError: If the value is blank/whitespace-only after
                stripping, contains a non-printable character, or is not
                a recognised time control format.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("time_control must not be empty or whitespace-only")
        if _has_non_printable(stripped):
            raise ValueError("time_control must not contain non-printable characters")
        try:
            parse_time_control(stripped)
        except ValueError as e:
            raise ValueError(
                f"invalid time_control {stripped!r}: expected one of "
                "'movetime=<ms>', 'depth=<n>', 'nodes=<n>', or "
                "'wtime=<ms>,btime=<ms>[,winc=<ms>,binc=<ms>]'"
            ) from e
        return stripped

    @model_validator(mode="after")
    def _validate_elo_bounds(self) -> Self:
        """Ensure elo0 is strictly less than elo1.

        ``elo0 >= elo1`` describes a statistically meaningless SPRT
        (the null and alternative hypotheses don't bracket a positive
        Elo gain), so it is rejected here rather than accepted and
        handed to the runner.

        Returns:
            ``self``, unchanged.

        Raises:
            ValueError: If ``elo0 >= elo1``.
        """
        if self.elo0 >= self.elo1:
            raise ValueError(f"elo0 ({self.elo0}) must be strictly less than elo1 ({self.elo1})")
        return self


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
