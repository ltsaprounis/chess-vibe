"""Single game loop for SPRT testing.

Plays a complete game between two UCI engine clients, alternating moves,
detecting termination conditions, delegating to adjudication, and recording
all moves with per-move evaluations.

Time is measured with ``time.monotonic_ns()`` around each ``go``. Clock games
track both sides' remaining time and send it on every ``go``, and every search
of any time control gets an explicit limit: overrunning it loses on time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

import chess
import chess.syzygy
from shared.storage.models import GameResult, Move
from shared.time_control import (
    FixedTimeControl,
    IncrementTimeControl,
    TimeControl,
)
from shared.uci_client import UCIClient, UCIEngineError, UCIInfo, UCITimeoutError

from sprt_runner.adjudication import (
    AdjudicationConfig,
    AdjudicationType,
    check_adjudication,
)

logger = logging.getLogger(__name__)

DEFAULT_TIME_MARGIN_MS: int = 100
DEFAULT_MOVE_TIMEOUT_MS: int = 30_000

_NS_PER_MS: int = 1_000_000
_NS_PER_S: int = 1_000_000_000


class TerminationReason(Enum):
    """How a game ended."""

    CHECKMATE = "checkmate"
    STALEMATE = "stalemate"
    DRAW_RULE = "draw_rule"
    ADJUDICATION = "adjudication"
    TIMEOUT = "timeout"
    ENGINE_CRASH = "engine_crash"
    MAX_MOVES = "max_moves"


@dataclass(frozen=True)
class GameOutcome:
    """Result of playing a single game.

    Attributes:
        result: The game result.
        termination: How the game ended.
        moves: List of moves played with evaluations.
        start_fen: Starting FEN, or None for standard start.
    """

    result: GameResult
    termination: TerminationReason
    moves: list[Move]
    start_fen: str | None = None


@dataclass
class GameConfig:
    """Configuration for playing a single game.

    Attributes:
        time_control: Time control for the game.
        adjudication: Adjudication thresholds.
        start_fen: Custom starting FEN, or None for standard start.
        max_moves: Maximum number of full moves before declaring a draw.
        time_margin_ms: Grace added to the limit of time-bounded searches, to
            absorb harness latency. A ``wtime/btime`` search may take the
            mover's remaining time plus this margin, and a ``movetime`` search
            may take ``movetime_ms`` plus this margin. On clock games an
            overrun within the margin stays owed, so the margin can be used up
            only once per game. 0 means no grace.
        move_timeout_ms: Limit on a ``depth`` or ``nodes`` search, which has no
            time bound of its own. No margin is added.
    """

    time_control: TimeControl
    adjudication: AdjudicationConfig = field(default_factory=AdjudicationConfig)
    start_fen: str | None = None
    max_moves: int = 500
    time_margin_ms: int = DEFAULT_TIME_MARGIN_MS
    move_timeout_ms: int = DEFAULT_MOVE_TIMEOUT_MS


def _visible_clock_ms(remaining_ns: int) -> int:
    """Return remaining time as engines and records see it: whole ms, never negative."""
    return max(remaining_ns // _NS_PER_MS, 0)


@dataclass
class _Clocks:
    """Both sides' remaining time in a clock game, in integer nanoseconds.

    Remaining time can go as low as minus the margin. An overrun within the
    margin is carried rather than forgiven, so a side's total time is bounded
    by its start time, its increments and one margin per game.

    Attributes:
        time_control: The game's starting time control.
        white_ns: White's remaining time.
        black_ns: Black's remaining time.
    """

    time_control: IncrementTimeControl
    white_ns: int
    black_ns: int

    @classmethod
    def start(cls, time_control: IncrementTimeControl) -> _Clocks:
        """Create the clocks for the start of a game."""
        return cls(
            time_control=time_control,
            white_ns=time_control.wtime_ms * _NS_PER_MS,
            black_ns=time_control.btime_ms * _NS_PER_MS,
        )

    @property
    def white_ms(self) -> int:
        """White's visible clock."""
        return _visible_clock_ms(self.white_ns)

    @property
    def black_ms(self) -> int:
        """Black's visible clock."""
        return _visible_clock_ms(self.black_ns)

    def remaining_ns(self, *, is_white: bool) -> int:
        """Return the given side's remaining time, which may be negative."""
        return self.white_ns if is_white else self.black_ns

    def charge(self, elapsed_ns: int, *, is_white: bool) -> None:
        """Deduct a completed move from the mover and add the mover's increment."""
        if is_white:
            self.white_ns += self.time_control.winc_ms * _NS_PER_MS - elapsed_ns
        else:
            self.black_ns += self.time_control.binc_ms * _NS_PER_MS - elapsed_ns

    def current_time_control(self) -> IncrementTimeControl:
        """Return the time control to send on the next ``go``."""
        return replace(self.time_control, wtime_ms=self.white_ms, btime_ms=self.black_ms)


def _move_limit_ns(config: GameConfig, clocks: _Clocks | None, *, is_white: bool) -> int:
    """Return how long the mover may take over this search, in nanoseconds.

    Args:
        config: Game configuration.
        clocks: The game's clocks, or None if it is not a clock game.
        is_white: Whether it is white's turn.

    Returns:
        The limit. It is never negative, because a move that would have taken
        remaining time below minus the margin already lost on time.
    """
    if clocks is not None:
        return clocks.remaining_ns(is_white=is_white) + config.time_margin_ms * _NS_PER_MS
    if isinstance(config.time_control, FixedTimeControl):
        return (config.time_control.movetime_ms + config.time_margin_ms) * _NS_PER_MS
    return config.move_timeout_ms * _NS_PER_MS


def _extract_score_cp(infos: list[UCIInfo]) -> int | None:
    """Extract the centipawn score from the last info line with a score."""
    for info in reversed(infos):
        if info.score is not None:
            if info.score.cp is not None:
                return info.score.cp
            if info.score.mate is not None:
                # Convert mate to large cp value for adjudication
                sign = 1 if info.score.mate > 0 else -1
                return sign * 30000
    return None


@dataclass
class _MoveData:
    """Extracted evaluation data from UCI info lines."""

    depth: int | None = None
    seldepth: int | None = None
    score_cp: int | None = None
    score_mate: int | None = None
    pv: list[str] = field(default_factory=list[str])
    nodes: int | None = None
    time_ms: int | None = None


def _extract_move_data(infos: list[UCIInfo]) -> _MoveData:
    """Extract evaluation data from info lines for move recording."""
    data = _MoveData()

    for info in reversed(infos):
        if info.depth is not None and data.depth is None:
            data.depth = info.depth
        if info.seldepth is not None and data.seldepth is None:
            data.seldepth = info.seldepth
        if info.score is not None and data.score_cp is None and data.score_mate is None:
            data.score_cp = info.score.cp
            data.score_mate = info.score.mate
        if info.pv and not data.pv:
            data.pv = info.pv
        if info.nodes is not None and data.nodes is None:
            data.nodes = info.nodes
        if info.time_ms is not None and data.time_ms is None:
            data.time_ms = info.time_ms

    return data


def _open_tablebase(syzygy_path: Path | None) -> chess.syzygy.Tablebase | None:
    """Open the Syzygy tablebase handle once, for reuse across a whole game.

    Args:
        syzygy_path: Path to the Syzygy tablebase directory, or None to
            disable tablebase adjudication.

    Returns:
        An open Tablebase handle, or None if disabled or the tablebase
        could not be opened (e.g. missing or unreadable directory —
        logged, not fatal, so a bad path doesn't kill the game).
    """
    if syzygy_path is None:
        return None
    try:
        return chess.syzygy.open_tablebase(str(syzygy_path))
    except OSError:
        logger.warning(
            "Failed to open Syzygy tablebase at %s; tablebase adjudication disabled for this game",
            syzygy_path,
            exc_info=True,
        )
        return None


async def play_game(
    *,
    white: UCIClient,
    black: UCIClient,
    config: GameConfig,
) -> GameOutcome:
    """Play a complete game between two UCI engines.

    Opens the Syzygy tablebase handle (if configured) once for the whole
    game and guarantees it is closed on every exit path, including
    exceptions, before delegating to the game loop.

    Args:
        white: UCI client for the white engine.
        black: UCI client for the black engine.
        config: Game configuration.

    Returns:
        GameOutcome with the result, termination reason, and moves.
    """
    tablebase = _open_tablebase(config.adjudication.syzygy_path)
    try:
        return await _play_game(white=white, black=black, config=config, tablebase=tablebase)
    finally:
        if tablebase is not None:
            try:
                tablebase.close()
            except Exception:
                # A close failure must not destroy an already-completed
                # GameOutcome (or mask an exception already propagating
                # through this finally block) — log and move on.
                logger.warning("Failed to close Syzygy tablebase", exc_info=True)


async def _play_game(
    *,
    white: UCIClient,
    black: UCIClient,
    config: GameConfig,
    tablebase: chess.syzygy.Tablebase | None,
) -> GameOutcome:
    """Run the game loop for a single game between two UCI engines.

    Only the ``go`` itself is timed, not ``position``. Every search gets an
    explicit timeout, so none can fall back to ``UCIClient.default_timeout``.
    The mover loses on time if the search takes longer than its limit, whether
    the client's deadline fires or the move arrives late. A flagged move is not
    recorded. A move exactly at the limit stands.

    On clock games, every ``go`` carries both sides' current clocks and every
    recorded move carries both clocks after it, increment included.

    Args:
        white: UCI client for the white engine.
        black: UCI client for the black engine.
        config: Game configuration.
        tablebase: An already-open Syzygy tablebase handle, or None.
            Owned and closed by ``play_game``; this function only probes it.

    Returns:
        GameOutcome with the result, termination reason, and moves.
    """
    # Initialize board
    board = chess.Board(config.start_fen) if config.start_fen else chess.Board()

    time_control = config.time_control
    clocks = _Clocks.start(time_control) if isinstance(time_control, IncrementTimeControl) else None

    moves_played: list[Move] = []
    uci_moves: list[str] = []
    white_scores: list[int] = []
    black_scores: list[int] = []

    while True:
        # Check move limit
        full_moves = (len(moves_played) // 2) + 1
        if full_moves > config.max_moves:
            logger.info("Max moves (%d) reached, declaring draw", config.max_moves)
            return GameOutcome(
                result=GameResult.DRAW,
                termination=TerminationReason.MAX_MOVES,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        # Determine which engine moves
        is_white = board.turn == chess.WHITE
        engine = white if is_white else black

        limit_ns = _move_limit_ns(config, clocks, is_white=is_white)
        search_tc = clocks.current_time_control() if clocks is not None else time_control

        # Set position and search within the limit
        try:
            if config.start_fen:
                await engine.position(fen=config.start_fen, moves=uci_moves if uci_moves else None)
            else:
                await engine.position(moves=uci_moves if uci_moves else None)

            start_ns = time.monotonic_ns()
            bestmove, infos = await engine.go(search_tc, timeout=limit_ns / _NS_PER_S)
            elapsed_ns = time.monotonic_ns() - start_ns
        except UCITimeoutError:
            logger.warning("Engine timed out (limit %.1f ms)", limit_ns / _NS_PER_MS)
            result = GameResult.BLACK_WIN if is_white else GameResult.WHITE_WIN
            return GameOutcome(
                result=result,
                termination=TerminationReason.TIMEOUT,
                moves=moves_played,
                start_fen=config.start_fen,
            )
        except UCIEngineError:
            logger.warning("Engine crashed")
            result = GameResult.BLACK_WIN if is_white else GameResult.WHITE_WIN
            return GameOutcome(
                result=result,
                termination=TerminationReason.ENGINE_CRASH,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        # The client's deadline only starts after it sends ``go``, so a move can
        # arrive just past the limit without the client raising.
        if elapsed_ns > limit_ns:
            logger.warning(
                "Move took %.1f ms (limit %.1f ms)",
                elapsed_ns / _NS_PER_MS,
                limit_ns / _NS_PER_MS,
            )
            result = GameResult.BLACK_WIN if is_white else GameResult.WHITE_WIN
            return GameOutcome(
                result=result,
                termination=TerminationReason.TIMEOUT,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        # Validate and apply move
        try:
            chess_move = chess.Move.from_uci(bestmove.move)
            if chess_move not in board.legal_moves:
                logger.warning("Engine played illegal move: %s", bestmove.move)
                result = GameResult.BLACK_WIN if is_white else GameResult.WHITE_WIN
                return GameOutcome(
                    result=result,
                    termination=TerminationReason.ENGINE_CRASH,
                    moves=moves_played,
                    start_fen=config.start_fen,
                )
        except ValueError:
            logger.warning("Invalid UCI move string: %s", bestmove.move)
            result = GameResult.BLACK_WIN if is_white else GameResult.WHITE_WIN
            return GameOutcome(
                result=result,
                termination=TerminationReason.ENGINE_CRASH,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        if clocks is not None:
            clocks.charge(elapsed_ns, is_white=is_white)

        # Record move data
        san = board.san(chess_move)
        board.push(chess_move)
        fen_after = board.fen()

        move_data = _extract_move_data(infos)

        move = Move(
            uci=bestmove.move,
            san=san,
            fen_after=fen_after,
            score_cp=move_data.score_cp,
            score_mate=move_data.score_mate,
            depth=move_data.depth,
            seldepth=move_data.seldepth,
            pv=move_data.pv,
            nodes=move_data.nodes,
            time_ms=move_data.time_ms,
            clock_white_ms=clocks.white_ms if clocks is not None else None,
            clock_black_ms=clocks.black_ms if clocks is not None else None,
        )
        moves_played.append(move)
        uci_moves.append(bestmove.move)

        # Track scores for adjudication
        # Scores are from each engine's perspective (positive = engine thinks it's ahead)
        score_cp = _extract_score_cp(infos)
        if score_cp is not None:
            if is_white:
                white_scores.append(score_cp)
            else:
                black_scores.append(score_cp)

        # Check game-ending conditions
        if board.is_checkmate():
            result = GameResult.WHITE_WIN if board.turn == chess.BLACK else GameResult.BLACK_WIN
            return GameOutcome(
                result=result,
                termination=TerminationReason.CHECKMATE,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        if board.is_stalemate():
            return GameOutcome(
                result=GameResult.DRAW,
                termination=TerminationReason.STALEMATE,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        if board.is_insufficient_material() or board.is_fifty_moves() or board.is_repetition(3):
            return GameOutcome(
                result=GameResult.DRAW,
                termination=TerminationReason.DRAW_RULE,
                moves=moves_played,
                start_fen=config.start_fen,
            )

        # Check adjudication
        adj_result = check_adjudication(
            white_scores,
            black_scores,
            move_number=full_moves,
            config=config.adjudication,
            board=board,
            tablebase=tablebase,
        )
        if adj_result is not None:
            logger.info("Adjudication: %s", adj_result.reason)
            if adj_result.adjudication_type == AdjudicationType.WIN_WHITE:
                game_result = GameResult.WHITE_WIN
            elif adj_result.adjudication_type == AdjudicationType.WIN_BLACK:
                game_result = GameResult.BLACK_WIN
            else:
                game_result = GameResult.DRAW
            return GameOutcome(
                result=game_result,
                termination=TerminationReason.ADJUDICATION,
                moves=moves_played,
                start_fen=config.start_fen,
            )
