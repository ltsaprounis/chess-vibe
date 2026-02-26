"""Game adjudication for SPRT testing.

Determines whether a game can be adjudicated early based on engine
evaluations. Supports win adjudication (both engines agree one side
is decisively ahead) and draw adjudication (both engines agree the
position is drawn). All thresholds are configurable per test.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
    """

    win_threshold_cp: int = 1000
    win_consecutive_moves: int = 5
    draw_threshold_cp: int = 10
    draw_consecutive_moves: int = 10
    draw_min_move: int = 40


def check_adjudication(
    white_scores: list[int],
    black_scores: list[int],
    *,
    move_number: int,
    config: AdjudicationConfig,
) -> AdjudicationResult | None:
    """Check whether a game should be adjudicated.

    Scores are from each engine's perspective: positive means the engine
    thinks it is ahead. The lists contain recent scores (one per move pair).

    Args:
        white_scores: Recent centipawn scores from white's engine (positive = white ahead).
        black_scores: Recent centipawn scores from black's engine (positive = black ahead).
        move_number: Current move number in the game.
        config: Adjudication thresholds.

    Returns:
        An AdjudicationResult if the game should be adjudicated, else None.
    """
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
