"""Single game loop for SPRT testing.

Plays a complete game between two UCI engine clients, alternating moves,
enforcing time control via ``time.monotonic_ns()`` deadlines, detecting
termination conditions, delegating to adjudication, and recording all
moves with per-move evaluations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

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
        move_overhead_ms: Per-move watchdog timeout overhead in milliseconds.
            The total per-move deadline is the time control's expected
            duration plus this overhead.  For ``movetime`` the expected
            duration equals ``movetime_ms``; for ``wtime/btime`` it equals
            the current side's remaining time; for unbounded controls
            (``depth``/``nodes``) only the overhead applies.  Set to 0 to
            disable the watchdog.  Uses ``time.monotonic_ns()`` for deadline
            tracking.
    """

    time_control: TimeControl
    adjudication: AdjudicationConfig = field(default_factory=AdjudicationConfig)
    start_fen: str | None = None
    max_moves: int = 500
    move_overhead_ms: int = 5000


def watchdog_timeout_ms(tc: TimeControl, *, is_white: bool) -> int:
    """Return the expected move duration in milliseconds for watchdog calculation.

    For time-bounded controls (movetime, wtime/btime) returns the relevant
    time limit so the watchdog fires only *after* the engine's legitimate
    search window.  For unbounded controls (depth/nodes) returns 0 so the
    caller falls back to ``move_overhead_ms`` alone.

    Args:
        tc: The active time control.
        is_white: Whether it is white's turn (selects wtime vs btime).

    Returns:
        Expected duration in milliseconds (0 for unbounded TCs).
    """
    if isinstance(tc, FixedTimeControl):
        return tc.movetime_ms
    if isinstance(tc, IncrementTimeControl):
        return tc.wtime_ms if is_white else tc.btime_ms
    # DepthTimeControl / NodesTimeControl have no time bound
    return 0


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


async def play_game(
    *,
    white: UCIClient,
    black: UCIClient,
    config: GameConfig,
) -> GameOutcome:
    """Play a complete game between two UCI engines.

    Args:
        white: UCI client for the white engine.
        black: UCI client for the black engine.
        config: Game configuration.

    Returns:
        GameOutcome with the result, termination reason, and moves.
    """
    # Open Syzygy tablebase once for the entire game
    tablebase: chess.syzygy.Tablebase | None = None
    if config.adjudication.syzygy_path is not None:
        try:
            tablebase = chess.syzygy.open_tablebase(str(config.adjudication.syzygy_path))
        except Exception:
            logger.warning("Failed to open Syzygy tablebase", exc_info=True)

    try:
        return await _play_game_loop(white=white, black=black, config=config, tablebase=tablebase)
    finally:
        if tablebase is not None:
            tablebase.close()


async def _play_game_loop(
    *,
    white: UCIClient,
    black: UCIClient,
    config: GameConfig,
    tablebase: chess.syzygy.Tablebase | None,
) -> GameOutcome:
    """Run the main game loop.

    This is an internal helper for ``play_game``. The caller is responsible
    for opening and closing the *tablebase* handle.
    """
    # Initialize board
    board = chess.Board(config.start_fen) if config.start_fen else chess.Board()

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

        # Set position and search with deadline
        try:
            if config.start_fen:
                await engine.position(fen=config.start_fen, moves=uci_moves if uci_moves else None)
            else:
                await engine.position(moves=uci_moves if uci_moves else None)

            # Search with per-move watchdog deadline using time.monotonic_ns()
            if config.move_overhead_ms > 0:
                start_ns = time.monotonic_ns()
                tc_duration_ms = watchdog_timeout_ms(config.time_control, is_white=is_white)
                timeout_s = (tc_duration_ms + config.move_overhead_ms) / 1000.0
                try:
                    bestmove, infos = await asyncio.wait_for(
                        engine.go(config.time_control),
                        timeout=timeout_s,
                    )
                except TimeoutError:
                    elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
                    logger.warning(
                        "Move watchdog timeout after %.0f ms (limit %d ms)",
                        elapsed_ms,
                        config.move_overhead_ms,
                    )
                    result = GameResult.BLACK_WIN if is_white else GameResult.WHITE_WIN
                    return GameOutcome(
                        result=result,
                        termination=TerminationReason.TIMEOUT,
                        moves=moves_played,
                        start_fen=config.start_fen,
                    )
            else:
                bestmove, infos = await engine.go(config.time_control)
        except UCITimeoutError:
            logger.warning("Engine timed out")
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
