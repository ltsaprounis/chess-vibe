"""Game browsing endpoints.

Lists and retrieves completed games from the game repository.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from shared.storage.models import GameFilter, GameResult

from backend.converters import game_to_response, game_to_summary
from backend.models import GameResponse, GameSummaryResponse

router = APIRouter(prefix="/games", tags=["games"])


@router.get("", response_model=list[GameSummaryResponse])
def list_games(
    request: Request,
    sprt_test_id: str | None = None,
    engine_id: str | None = None,
    result: str | None = None,
    opening_name: str | None = None,
) -> list[GameSummaryResponse]:
    """List games with optional filters.

    Args:
        request: FastAPI request (for accessing app state).
        sprt_test_id: Filter by SPRT test ID.
        engine_id: Filter by engine (either side).
        result: Filter by result string.
        opening_name: Filter by opening name.

    Returns:
        List of game summaries.
    """
    game_filter: GameFilter | None = None

    result_enum: GameResult | None = None
    if result is not None:
        try:
            result_enum = GameResult(result)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid result: {result}") from e

    if any(v is not None for v in (sprt_test_id, engine_id, result_enum, opening_name)):
        game_filter = GameFilter(
            sprt_test_id=sprt_test_id,
            engine_id=engine_id,
            result=result_enum,
            opening_name=opening_name,
        )

    games = request.app.state.game_repo.list_games(game_filter)
    return [game_to_summary(g) for g in games]


@router.get("/{game_id}", response_model=GameResponse)
def get_game(game_id: str, request: Request) -> GameResponse:
    """Retrieve a completed game by ID.

    Args:
        game_id: Unique game identifier.
        request: FastAPI request.

    Returns:
        Full game record with per-move evaluations.
    """
    game = request.app.state.game_repo.get_game(game_id)
    if game is None:
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
    return game_to_response(game)
