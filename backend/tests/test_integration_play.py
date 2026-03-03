"""End-to-end integration test for the backend play-vs-engine workflow.

Exercises the complete flow: HTTP engine listing → WebSocket game session
→ real engine subprocess → game persistence. Uses the real random-engine
subprocess and a temporary data directory.

Marked with ``@pytest.mark.integration`` so unit-test runs can skip it.
Skips automatically if the ``random-engine`` venv is not built.
"""

from __future__ import annotations

import random
from pathlib import Path

import chess
import pytest
from backend.main import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_MAX_GAME_MOVES = 300


def _pick_random_move(board: chess.Board) -> str:
    """Pick a random legal move from the current board position."""
    return random.choice(list(board.legal_moves)).uci()


@pytest.fixture
def integration_client(
    tmp_path: Path,
    random_engine_command: str,  # triggers skip if venv absent
) -> TestClient:
    """Create a TestClient with real dependencies and a temp data dir.

    Using ``random_engine_command`` ensures the test is skipped when
    the random-engine virtualenv has not been built.
    """
    app = create_app(data_dir=tmp_path)
    return TestClient(app, raise_server_exceptions=False)


class TestPlayVsEngineIntegration:
    """End-to-end: HTTP + WebSocket + real engine + persistence."""

    def test_engines_list_includes_random_engine(
        self,
        integration_client: TestClient,
    ) -> None:
        """GET /api/engines should include random-engine."""
        response = integration_client.get("/api/engines")

        assert response.status_code == 200
        engine_ids = [e["id"] for e in response.json()]
        assert "random-engine" in engine_ids

    def test_full_play_and_persistence(
        self,
        integration_client: TestClient,
    ) -> None:
        """Play a full game via WebSocket and verify it is persisted."""
        board = chess.Board()
        game_id: str | None = None
        game_result: str | None = None
        move_count = 0

        with integration_client.websocket_connect("/ws/play") as ws:
            # --- Start the game ------------------------------------------------
            ws.send_json(
                {
                    "type": "start",
                    "engine_id": "random-engine",
                    "player_color": "white",
                }
            )
            started = ws.receive_json()

            assert started["type"] == "started"
            game_id = started["game_id"]
            assert game_id is not None
            assert "fen" in started

            # --- Play moves until game over or safety limit --------------------
            while move_count < _MAX_GAME_MOVES:
                if board.is_game_over():
                    break

                # Player (white) picks a random legal move
                player_move = _pick_random_move(board)
                board.push(chess.Move.from_uci(player_move))
                move_count += 1

                ws.send_json({"type": "move", "move": player_move})
                data = ws.receive_json()

                # Player move might have ended the game
                if data["type"] == "game_over":
                    game_result = data["result"]
                    break

                # Otherwise, it must be the engine's reply
                assert data["type"] == "engine_move"
                assert "move" in data
                assert "fen" in data
                assert "san" in data

                engine_move_uci = data["move"]
                board.push(chess.Move.from_uci(engine_move_uci))
                move_count += 1

                # If engine move ended the game, a game_over follows
                if board.is_game_over():
                    over = ws.receive_json()
                    assert over["type"] == "game_over"
                    game_result = over["result"]
                    break

        # --- Verify the game was actually played --------------------------------
        assert game_id is not None
        assert move_count > 0

        # --- Verify persistence via HTTP ----------------------------------------
        if game_result is not None:
            response = integration_client.get(f"/api/games/{game_id}")
            assert response.status_code == 200

            game_data = response.json()
            assert game_data["id"] == game_id
            assert game_data["result"] == game_result
            assert len(game_data["moves"]) == move_count
            assert game_data["white_engine"] == "player"
            assert game_data["black_engine"] == "random-engine"
