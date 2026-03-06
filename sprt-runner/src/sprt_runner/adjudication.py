"""Game adjudication for SPRT testing.

Determines whether a game can be adjudicated early based on engine
evaluations. Supports win adjudication (both engines agree one side
is decisively ahead), draw adjudication (both evals near zero for N
moves), and Syzygy tablebase adjudication (position resolved by
endgame tablebases). All thresholds are configurable per test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import chess
import chess.syzygy

logger = logging.getLogger(__name__)


class AdjudicationType(Enum):
    """Type of adjudication that ended a game."""

    WIN_WHITE = "win_white"
    WIN_BLACK = "win_black"
    DRAW = "draw"


@dataclass(frozen=True)
class AdjudicationResult:
    """Result of an adjudication check.

    Attributes:
        adjudication_type: The type of adjudication.
        reason: Human-readable explanation.
    """

    adjudication_type: AdjudicationType
    reason: str


@dataclass(frozen=True)
class AdjudicationConfig:
    """Configuration for game adjudication thresholds.

    Attributes:
        win_threshold_cp: Centipawn threshold for win adjudication.
        win_consecutive_moves: Number of consecutive moves both engines
            must agree the eval exceeds the threshold. Set to 0 to disable.
        draw_threshold_cp: Centipawn threshold for draw adjudication.
        draw_consecutive_moves: Number of consecutive moves both engines
            must agree the eval is below the threshold. Set to 0 to disable.
        draw_min_move: Minimum move number before draw adjudication can trigger.
        syzygy_path: Path to Syzygy tablebase directory, or None to disable.
    """

    win_threshold_cp: int = 1000
    win_consecutive_moves: int = 5
    draw_threshold_cp: int = 10
    draw_consecutive_moves: int = 10
    draw_min_move: int = 40
    syzygy_path: Path | None = None


def check_adjudication(
    white_scores: list[int],
    black_scores: list[int],
    *,
    move_number: int,
    config: AdjudicationConfig,
    board: chess.Board | None = None,
    tablebase: chess.syzygy.Tablebase | None = None,
) -> AdjudicationResult | None:
    """Check whether a game should be adjudicated.

    Scores are from each engine's perspective: positive means the engine
    thinks it is ahead. The lists contain recent scores (one per move pair).

    Args:
        white_scores: Recent centipawn scores from white's engine (positive = white ahead).
        black_scores: Recent centipawn scores from black's engine (positive = black ahead).
        move_number: Current move number in the game.
        config: Adjudication thresholds.
        board: Current board position for tablebase probing (optional).
        tablebase: Pre-opened Syzygy tablebase handle for reuse across calls
            (optional). When ``None`` but ``config.syzygy_path`` is set, a
            temporary tablebase is opened per call as a fallback.

    Returns:
        An AdjudicationResult if the game should be adjudicated, else None.
    """
    # Syzygy tablebase adjudication (highest priority)
    if board is not None:
        if tablebase is not None:
            tb_result = _check_syzygy(board, tablebase)
            if tb_result is not None:
                return tb_result
        elif config.syzygy_path is not None:
            # Fallback: open a temporary tablebase when no pre-opened handle
            # is provided. Prefer passing a pre-opened handle for performance.
            try:
                with chess.syzygy.open_tablebase(str(config.syzygy_path)) as tb:
                    tb_result = _check_syzygy(board, tb)
                    if tb_result is not None:
                        return tb_result
            except Exception:
                logger.debug(
                    "Failed to open Syzygy tablebase at %s",
                    config.syzygy_path,
                    exc_info=True,
                )

    # Win adjudication: both engines agree one side is winning
    win_result = _check_win(white_scores, black_scores, config)
    if win_result is not None:
        return win_result

    # Draw adjudication: both engines agree position is drawn
    draw_result = _check_draw(white_scores, black_scores, move_number, config)
    if draw_result is not None:
        return draw_result

    return None


def _check_win(
    white_scores: list[int],
    black_scores: list[int],
    config: AdjudicationConfig,
) -> AdjudicationResult | None:
    """Check for win adjudication.

    Both engines must agree that one side is winning for N consecutive
    score pairs. White scores positive = white ahead; black scores
    positive = black ahead. Agreement on white winning means white has
    score >= threshold AND black has score <= -threshold.
    """
    n = config.win_consecutive_moves
    if n <= 0 or len(white_scores) < n or len(black_scores) < n:
        return None

    threshold = config.win_threshold_cp

    # Check last N scores for white winning
    recent_white = white_scores[-n:]
    recent_black = black_scores[-n:]

    # White wins: white engine sees >= threshold, black engine sees <= -threshold
    if all(w >= threshold for w in recent_white) and all(b <= -threshold for b in recent_black):
        return AdjudicationResult(
            adjudication_type=AdjudicationType.WIN_WHITE,
            reason=f"Win adjudication: both engines agree white is winning "
            f"(threshold={threshold}cp, moves={n})",
        )

    # Black wins: white engine sees <= -threshold, black engine sees >= threshold
    if all(w <= -threshold for w in recent_white) and all(b >= threshold for b in recent_black):
        return AdjudicationResult(
            adjudication_type=AdjudicationType.WIN_BLACK,
            reason=f"Win adjudication: both engines agree black is winning "
            f"(threshold={threshold}cp, moves={n})",
        )

    return None


def _check_draw(
    white_scores: list[int],
    black_scores: list[int],
    move_number: int,
    config: AdjudicationConfig,
) -> AdjudicationResult | None:
    """Check for draw adjudication.

    Both engines must agree the position is drawn (abs(score) <= threshold)
    for N consecutive score pairs, and the game must be past the minimum move.
    """
    n = config.draw_consecutive_moves
    if n <= 0 or len(white_scores) < n or len(black_scores) < n:
        return None

    if move_number < config.draw_min_move:
        return None

    threshold = config.draw_threshold_cp

    recent_white = white_scores[-n:]
    recent_black = black_scores[-n:]

    if all(abs(w) <= threshold for w in recent_white) and all(
        abs(b) <= threshold for b in recent_black
    ):
        return AdjudicationResult(
            adjudication_type=AdjudicationType.DRAW,
            reason=f"Draw adjudication: both engines agree position is drawn "
            f"(threshold={threshold}cp, moves={n})",
        )

    return None


def _check_syzygy(
    board: chess.Board,
    tablebase: chess.syzygy.Tablebase,
) -> AdjudicationResult | None:
    """Check for Syzygy tablebase adjudication.

    Probes the Syzygy tablebases to determine if the position has a
    known outcome. Only probes when the number of pieces on the board
    is within tablebase range.

    Args:
        board: Current board position.
        tablebase: Pre-opened Syzygy tablebase handle.

    Returns:
        An AdjudicationResult if the position is resolved, else None.
    """
    # Only probe when piece count is low enough for tablebases
    piece_count = len(board.piece_map())
    if piece_count > 7:
        return None

    try:
        wdl = tablebase.probe_wdl(board)
    except KeyError:
        # Position not in tablebases
        return None
    except Exception:
        logger.debug("Syzygy probe failed for position", exc_info=True)
        return None

    if wdl > 0:
        # Side to move wins
        if board.turn == chess.WHITE:
            return AdjudicationResult(
                adjudication_type=AdjudicationType.WIN_WHITE,
                reason="Syzygy tablebase: white wins",
            )
        return AdjudicationResult(
            adjudication_type=AdjudicationType.WIN_BLACK,
            reason="Syzygy tablebase: black wins",
        )

    if wdl < 0:
        # Side to move loses
        if board.turn == chess.WHITE:
            return AdjudicationResult(
                adjudication_type=AdjudicationType.WIN_BLACK,
                reason="Syzygy tablebase: black wins",
            )
        return AdjudicationResult(
            adjudication_type=AdjudicationType.WIN_WHITE,
            reason="Syzygy tablebase: white wins",
        )

    # WDL == 0: draw
    return AdjudicationResult(
        adjudication_type=AdjudicationType.DRAW,
        reason="Syzygy tablebase: draw",
    )
