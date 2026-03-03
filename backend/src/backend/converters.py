"""Converters between domain models and API/serialisation formats.

Centralises all format conversions so that routes and services stay
thin.
"""

from __future__ import annotations

from shared.storage.models import Game, GameResult, Move, SPRTTest
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    TimeControl,
    parse_time_control,
)

from backend.models import (
    GameResponse,
    GameSummaryResponse,
    MoveResponse,
    SPRTTestResponse,
    TimeControlResponse,
)


def time_control_from_string(tc_str: str) -> TimeControl:
    """Parse a time control string into a domain :class:`TimeControl`.

    Thin wrapper around :func:`shared.time_control.parse_time_control` kept
    for backward-compatibility with existing call-sites.

    Args:
        tc_str: Time control string.

    Returns:
        Parsed :class:`TimeControl`.

    Raises:
        ValueError: If the format is unrecognised.
    """
    return parse_time_control(tc_str)


def time_control_to_response(tc: TimeControl) -> TimeControlResponse:
    """Convert a domain :class:`TimeControl` to an API response model."""
    if isinstance(tc, FixedTimeControl):
        return TimeControlResponse(type="fixed_time", movetime_ms=tc.movetime_ms)
    if isinstance(tc, IncrementTimeControl):
        return TimeControlResponse(
            type="increment",
            wtime_ms=tc.wtime_ms,
            btime_ms=tc.btime_ms,
            winc_ms=tc.winc_ms,
            binc_ms=tc.binc_ms,
            moves_to_go=tc.moves_to_go,
        )
    if isinstance(tc, DepthTimeControl):
        return TimeControlResponse(type="depth", depth=tc.depth)
    # NodesTimeControl
    return TimeControlResponse(type="nodes", nodes=tc.nodes)


def move_to_response(move: Move) -> MoveResponse:
    """Convert a domain :class:`Move` to an API response model."""
    return MoveResponse(
        uci=move.uci,
        san=move.san,
        fen_after=move.fen_after,
        score_cp=move.score_cp,
        score_mate=move.score_mate,
        depth=move.depth,
        seldepth=move.seldepth,
        pv=move.pv,
        nodes=move.nodes,
        time_ms=move.time_ms,
        clock_white_ms=move.clock_white_ms,
        clock_black_ms=move.clock_black_ms,
    )


def game_to_response(game: Game) -> GameResponse:
    """Convert a domain :class:`Game` to a full API response model."""
    return GameResponse(
        id=game.id,
        white_engine=game.white_engine,
        black_engine=game.black_engine,
        result=game.result.value,
        moves=[move_to_response(m) for m in game.moves],
        created_at=game.created_at,
        opening_name=game.opening_name,
        sprt_test_id=game.sprt_test_id,
        start_fen=game.start_fen,
        time_control=(time_control_to_response(game.time_control) if game.time_control else None),
    )


def game_to_summary(game: Game) -> GameSummaryResponse:
    """Convert a domain :class:`Game` to a summary API response model."""
    return GameSummaryResponse(
        id=game.id,
        white_engine=game.white_engine,
        black_engine=game.black_engine,
        result=game.result.value,
        move_count=len(game.moves),
        created_at=game.created_at,
        opening_name=game.opening_name,
        sprt_test_id=game.sprt_test_id,
    )


def sprt_test_to_response(test: SPRTTest) -> SPRTTestResponse:
    """Convert a domain :class:`SPRTTest` to an API response model."""
    return SPRTTestResponse(
        id=test.id,
        engine_a=test.engine_a,
        engine_b=test.engine_b,
        time_control=time_control_to_response(test.time_control),
        elo0=test.elo0,
        elo1=test.elo1,
        alpha=test.alpha,
        beta=test.beta,
        created_at=test.created_at,
        status=test.status.value,
        wins=test.wins,
        losses=test.losses,
        draws=test.draws,
        llr=test.llr,
        result=test.result.value if test.result else None,
        completed_at=test.completed_at,
    )


def game_result_from_string(result_str: str) -> GameResult:
    """Parse a game result string into a :class:`GameResult`.

    Args:
        result_str: One of ``"1-0"``, ``"0-1"``, ``"1/2-1/2"``, ``"*"``.

    Returns:
        The matching :class:`GameResult`.

    Raises:
        ValueError: If the string is not a valid result.
    """
    return GameResult(result_str)
