"""Domain models for the chess-vibe persistence layer.

Defines the core data structures used throughout the application:
game records with per-move evaluations, SPRT test metadata, engine
and opening-book descriptors, and structured query filters.

All models are frozen dataclasses — immutable value objects that cross
component boundaries unchanged. Filters map cleanly to SQL WHERE
clauses when the storage backend is swapped later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from shared.time_control import TimeControl


class GameResult(Enum):
    """Possible outcomes for a chess game."""

    WHITE_WIN = "1-0"
    BLACK_WIN = "0-1"
    DRAW = "1/2-1/2"
    UNFINISHED = "*"


class SPRTStatus(Enum):
    """Lifecycle status of an SPRT test."""

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SPRTOutcome(Enum):
    """Final statistical outcome of an SPRT test."""

    H0 = "H0"
    H1 = "H1"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """A single move in a chess game with optional engine evaluation data.

    Attributes:
        uci: Move in UCI notation (e.g. ``"e2e4"``).
        san: Move in Standard Algebraic Notation (e.g. ``"e4"``).
        fen_after: FEN string of the position after the move.
        score_cp: Engine score in centipawns, or ``None``.
        score_mate: Mate-in-N score, or ``None``.
        depth: Search depth in plies.
        seldepth: Selective search depth.
        pv: Principal variation as a list of UCI moves.
        nodes: Number of nodes searched.
        time_ms: Time spent on this move in milliseconds.
        clock_white_ms: White clock remaining in milliseconds.
        clock_black_ms: Black clock remaining in milliseconds.
    """

    uci: str
    san: str
    fen_after: str
    score_cp: int | None = None
    score_mate: int | None = None
    depth: int | None = None
    seldepth: int | None = None
    pv: list[str] = field(default_factory=list[str])
    nodes: int | None = None
    time_ms: int | None = None
    clock_white_ms: int | None = None
    clock_black_ms: int | None = None


@dataclass(frozen=True)
class Game:
    """A completed (or in-progress) chess game record.

    Attributes:
        id: Unique identifier (UUID).
        white_engine: Identifier of the engine playing white.
        black_engine: Identifier of the engine playing black.
        result: Outcome of the game.
        moves: Ordered list of moves played.
        created_at: Timestamp when the game was created.
        opening_name: ECO or human-readable opening name, if known.
        sprt_test_id: ID of the SPRT test this game belongs to, if any.
        start_fen: Custom starting FEN, or ``None`` for standard start.
        time_control: Time control used for the game, if any.
    """

    id: str
    white_engine: str
    black_engine: str
    result: GameResult
    moves: list[Move]
    created_at: datetime
    opening_name: str | None = None
    sprt_test_id: str | None = None
    start_fen: str | None = None
    time_control: TimeControl | None = None


@dataclass(frozen=True)
class SPRTTest:
    """Metadata and running tallies for a Sequential Probability Ratio Test.

    Attributes:
        id: Unique identifier (UUID).
        engine_a: Identifier of the first engine.
        engine_b: Identifier of the second engine.
        time_control: Time control used for games in this test.
        elo0: Null-hypothesis Elo difference.
        elo1: Alternative-hypothesis Elo difference.
        alpha: Type-I error rate.
        beta: Type-II error rate.
        created_at: Timestamp when the test was created.
        status: Current lifecycle status.
        wins: Number of wins for engine_a.
        losses: Number of losses for engine_a.
        draws: Number of drawn games.
        llr: Current log-likelihood ratio.
        result: Statistical outcome, or ``None`` while running.
        completed_at: Timestamp when the test finished, or ``None``.
    """

    id: str
    engine_a: str
    engine_b: str
    time_control: TimeControl
    elo0: float
    elo1: float
    alpha: float
    beta: float
    created_at: datetime
    status: SPRTStatus = SPRTStatus.RUNNING
    wins: int = 0
    losses: int = 0
    draws: int = 0
    llr: float = 0.0
    result: SPRTOutcome | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class Engine:
    """Descriptor for a chess engine binary.

    Attributes:
        id: Unique identifier.
        name: Human-readable display name.
        path: Filesystem path to the engine executable.
        description: Optional description of the engine.
    """

    id: str
    name: str
    path: str
    description: str | None = None


@dataclass(frozen=True)
class OpeningBook:
    """Descriptor for an opening book file.

    Attributes:
        id: Unique identifier.
        name: Human-readable display name.
        path: Filesystem path to the book file.
        format: Book format (e.g. ``"pgn"``, ``"polyglot"``).
    """

    id: str
    name: str
    path: str
    format: str


# ---------------------------------------------------------------------------
# Structured filters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GameFilter:
    """Filter criteria for listing games.

    All fields default to ``None`` (no filtering).  When multiple fields are
    set, they are combined with AND semantics.  ``engine_id`` matches if the
    engine appears as either white or black.

    Attributes:
        sprt_test_id: Restrict to games belonging to a specific SPRT test.
        result: Restrict to games with a specific result.
        engine_id: Restrict to games where this engine played (either side).
        opening_name: Restrict to games with this opening name.
    """

    sprt_test_id: str | None = None
    result: GameResult | None = None
    engine_id: str | None = None
    opening_name: str | None = None


@dataclass(frozen=True)
class SPRTTestFilter:
    """Filter criteria for listing SPRT tests.

    All fields default to ``None`` (no filtering).  When multiple fields are
    set, they are combined with AND semantics.  ``engine_id`` matches if the
    engine is either ``engine_a`` or ``engine_b``.

    Attributes:
        status: Restrict to tests with this status.
        engine_id: Restrict to tests involving this engine (either side).
    """

    status: SPRTStatus | None = None
    engine_id: str | None = None
