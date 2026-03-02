"""WebSocket endpoint for interactive play-vs-engine games.

Spawns an engine as a UCI subprocess, relays player moves to the
engine, responds with engine moves and evaluations, and stores the
completed game.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from shared.engine_registry import load_registry
from shared.storage.models import GameResult
from shared.utils import get_repo_root

from backend.models import EngineMoveMessage, ErrorMessage, GameOverMessage
from backend.services.game_manager import GameManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_engine_path(engine_id: str, registry_path: Path) -> str:
    """Resolve an engine ID to its executable path.

    Args:
        engine_id: Engine identifier from the registry.
        registry_path: Path to ``engines.json``.

    Returns:
        Resolved executable command.

    Raises:
        ValueError: If the engine is not found.
    """
    entries = load_registry(registry_path)
    for entry in entries:
        if entry.id == engine_id:
            engine_dir = get_repo_root() / entry.dir
            parts = entry.run.split()
            if parts:
                executable = engine_dir / parts[0]
                return " ".join([str(executable), *parts[1:]])
            return entry.run
    raise ValueError(f"Engine '{engine_id}' not found in registry")


@router.websocket("/ws/play")
async def play_websocket(websocket: WebSocket) -> None:
    """Interactive play-vs-engine WebSocket endpoint.

    Protocol:
        1. Client sends ``{"type": "start", "engine_id": "...",
           "player_color": "white"}``
        2. Server acknowledges with ``{"type": "started", "game_id": "..."}``
        3. Client sends ``{"type": "move", "move": "e2e4"}``
        4. Server responds with engine move + eval
        5. On game over: server sends ``{"type": "game_over", ...}``
    """
    await websocket.accept()

    game_manager: GameManager = websocket.app.state.game_manager
    game_id: str | None = None

    try:
        while True:
            data: Any = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "start":
                engine_id = data.get("engine_id", "")
                player_color = data.get("player_color", "white")

                try:
                    engine_path = _resolve_engine_path(engine_id, get_repo_root() / "engines.json")
                except ValueError as e:
                    await websocket.send_json(ErrorMessage(message=str(e)).model_dump())
                    continue

                session = await game_manager.create_session(
                    engine_id=engine_id,
                    engine_path=engine_path,
                    player_color=player_color,
                )
                game_id = session.game_id

                await websocket.send_json(
                    {"type": "started", "game_id": game_id, "fen": session.board.fen()}
                )

                # If engine plays first (player is black)
                if player_color == "black":
                    uci_move, san, fen, info = await game_manager.make_engine_move(session)
                    response = EngineMoveMessage(
                        move=uci_move,
                        san=san,
                        fen=fen,
                        score_cp=info.score.cp if info and info.score else None,
                        score_mate=info.score.mate if info and info.score else None,
                        depth=info.depth if info else None,
                        pv=info.pv if info else [],
                    )
                    await websocket.send_json(response.model_dump())

                    result = game_manager.check_game_over(session)
                    if result is not None:
                        game = await game_manager.end_session(game_id, result)
                        await websocket.send_json(
                            GameOverMessage(result=result.value, game_id=game.id).model_dump()
                        )
                        game_id = None

            elif msg_type == "move" and game_id is not None:
                session = game_manager.get_session(game_id)
                if session is None:
                    await websocket.send_json(
                        ErrorMessage(message="No active session").model_dump()
                    )
                    continue

                move_uci = data.get("move", "")
                try:
                    game_manager.apply_player_move(session, move_uci)
                except ValueError as e:
                    await websocket.send_json(ErrorMessage(message=str(e)).model_dump())
                    continue

                # Check if game ended after player move
                result = game_manager.check_game_over(session)
                if result is not None:
                    game = await game_manager.end_session(game_id, result)
                    await websocket.send_json(
                        GameOverMessage(result=result.value, game_id=game.id).model_dump()
                    )
                    game_id = None
                    continue

                # Engine responds
                uci_move, san, fen, info = await game_manager.make_engine_move(session)
                response = EngineMoveMessage(
                    move=uci_move,
                    san=san,
                    fen=fen,
                    score_cp=info.score.cp if info and info.score else None,
                    score_mate=info.score.mate if info and info.score else None,
                    depth=info.depth if info else None,
                    pv=info.pv if info else [],
                )
                await websocket.send_json(response.model_dump())

                result = game_manager.check_game_over(session)
                if result is not None:
                    game = await game_manager.end_session(game_id, result)
                    await websocket.send_json(
                        GameOverMessage(result=result.value, game_id=game.id).model_dump()
                    )
                    game_id = None

            elif msg_type == "resign" and game_id is not None:
                session = game_manager.get_session(game_id)
                if session is None:
                    continue
                # Player resigns — engine wins
                if session.player_color == "white":
                    result = GameResult.BLACK_WIN
                else:
                    result = GameResult.WHITE_WIN
                game = await game_manager.end_session(game_id, result)
                await websocket.send_json(
                    GameOverMessage(result=result.value, game_id=game.id).model_dump()
                )
                game_id = None

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except json.JSONDecodeError:
        logger.warning("Invalid JSON received on WebSocket")
    except Exception:
        logger.exception("Error in play WebSocket")
    finally:
        if game_id is not None:
            await game_manager.cleanup_session(game_id)
