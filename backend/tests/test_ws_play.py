"""Tests for the play WebSocket module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import chess
import pytest
from backend.services.game_manager import GameSession
from backend.ws import play as ws_play
from backend.ws.play import _resolve_engine_path  # type: ignore[reportPrivateUsage]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shared.engine_registry import EngineEntry
from shared.storage.models import GameResult


class TestResolveEnginePath:
    """Tests for _resolve_engine_path."""

    def test_uses_repo_root_for_engine_dir(self) -> None:
        """Engine directory should be resolved relative to repo root."""
        fake_root = Path("/fake/repo/root")
        entry = EngineEntry(
            id="my-engine",
            name="My Engine",
            dir="engines/my-engine",
            build=None,
            run=".venv/bin/python -m my_engine",
        )
        registry_path = fake_root / "engines.json"

        with (
            patch("backend.ws.play.load_registry", return_value=[entry]),
            patch("backend.ws.play.get_repo_root", return_value=fake_root),
        ):
            result = _resolve_engine_path("my-engine", registry_path)

        expected = str(fake_root / "engines/my-engine" / ".venv/bin/python") + " -m my_engine"
        assert result == expected

    def test_engine_not_found_raises(self) -> None:
        """ValueError is raised when the engine ID is not in the registry."""
        with (
            patch("backend.ws.play.load_registry", return_value=[]),
            pytest.raises(ValueError, match="not found"),
        ):
            _resolve_engine_path("missing", Path("/some/engines.json"))

    def test_run_without_parts_returns_run(self) -> None:
        """When run is empty, return it as-is."""
        fake_root = Path("/fake/repo/root")
        entry = EngineEntry(
            id="empty-run",
            name="Empty",
            dir="engines/empty",
            build=None,
            run="",
        )
        with (
            patch("backend.ws.play.load_registry", return_value=[entry]),
            patch("backend.ws.play.get_repo_root", return_value=fake_root),
        ):
            result = _resolve_engine_path("empty-run", Path("/any/engines.json"))

        assert result == ""


class TestPlayWebSocket:
    """Tests for the /ws/play WebSocket endpoint."""

    @pytest.fixture
    def mock_game_manager(self) -> MagicMock:
        """Create a mock GameManager with async methods."""
        gm = MagicMock()
        gm.create_session = AsyncMock()
        gm.get_session = MagicMock(return_value=None)
        gm.apply_player_move = MagicMock()
        gm.check_game_over = MagicMock(return_value=None)
        gm.make_engine_move = AsyncMock()
        gm.end_session = AsyncMock()
        gm.cleanup_session = AsyncMock()
        return gm

    @pytest.fixture
    def ws_client(self, mock_game_manager: MagicMock) -> TestClient:
        """Create a TestClient with mocked GameManager."""
        app = FastAPI()
        app.state.game_manager = mock_game_manager
        app.include_router(ws_play.router)
        return TestClient(app, raise_server_exceptions=False)

    def _make_session(
        self,
        *,
        game_id: str = "test-game-id",
        engine_id: str = "test-engine",
        player_color: str = "white",
    ) -> GameSession:
        """Create a GameSession with a real chess board."""
        return GameSession(
            game_id=game_id,
            engine_id=engine_id,
            player_color=player_color,
            board=chess.Board(),
        )

    def _make_mock_game(self, game_id: str = "test-game-id") -> MagicMock:
        """Create a mock Game object with an id attribute."""
        game = MagicMock()
        game.id = game_id
        return game

    def test_connection_accepted(self, ws_client: TestClient) -> None:
        """WebSocket connection should be accepted."""
        with ws_client.websocket_connect("/ws/play"):
            pass

    def test_start_valid_engine_returns_started(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Start message with valid engine_id returns started response."""
        session = self._make_session()
        mock_game_manager.create_session = AsyncMock(return_value=session)

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            data = ws.receive_json()

        assert data["type"] == "started"
        assert data["game_id"] == "test-game-id"
        assert "fen" in data
        mock_game_manager.create_session.assert_called_once()

    def test_start_unknown_engine_returns_error(
        self,
        ws_client: TestClient,
    ) -> None:
        """Start message with unknown engine returns error response."""
        with (
            patch(
                "backend.ws.play._resolve_engine_path",
                side_effect=ValueError("Engine 'unknown' not found in registry"),
            ),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "unknown"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert "not found" in data["message"]

    def test_move_triggers_engine_response(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Valid move triggers engine response."""
        session = self._make_session()
        mock_game_manager.create_session = AsyncMock(return_value=session)
        mock_game_manager.get_session = MagicMock(return_value=session)
        mock_game_manager.make_engine_move = AsyncMock(
            return_value=("e7e5", "e5", "fen_after_e5", None)
        )

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            ws.receive_json()  # started

            ws.send_json({"type": "move", "move": "e2e4"})
            data = ws.receive_json()

        assert data["type"] == "engine_move"
        assert data["move"] == "e7e5"
        assert data["san"] == "e5"
        assert data["fen"] == "fen_after_e5"
        mock_game_manager.apply_player_move.assert_called_once_with(session, "e2e4")

    def test_illegal_move_returns_error(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Illegal move returns error response."""
        session = self._make_session()
        mock_game_manager.create_session = AsyncMock(return_value=session)
        mock_game_manager.get_session = MagicMock(return_value=session)
        mock_game_manager.apply_player_move = MagicMock(
            side_effect=ValueError("Illegal move: e1e8")
        )

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            ws.receive_json()  # started

            ws.send_json({"type": "move", "move": "e1e8"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert "Illegal move" in data["message"]

    def test_game_over_after_player_move(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Game over after player move sends game_over message."""
        session = self._make_session()
        mock_game = self._make_mock_game()
        mock_game_manager.create_session = AsyncMock(return_value=session)
        mock_game_manager.get_session = MagicMock(return_value=session)
        mock_game_manager.check_game_over = MagicMock(return_value=GameResult.BLACK_WIN)
        mock_game_manager.end_session = AsyncMock(return_value=mock_game)

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            ws.receive_json()  # started

            ws.send_json({"type": "move", "move": "d8h4"})
            data = ws.receive_json()

        assert data["type"] == "game_over"
        assert data["result"] == GameResult.BLACK_WIN.value
        assert data["game_id"] == "test-game-id"
        mock_game_manager.end_session.assert_called_once()

    def test_game_over_after_engine_move(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Game over after engine move sends engine_move then game_over."""
        session = self._make_session()
        mock_game = self._make_mock_game()
        mock_game_manager.create_session = AsyncMock(return_value=session)
        mock_game_manager.get_session = MagicMock(return_value=session)
        mock_game_manager.check_game_over = MagicMock(side_effect=[None, GameResult.WHITE_WIN])
        mock_game_manager.make_engine_move = AsyncMock(
            return_value=("e7e5", "e5", "fen_after", None)
        )
        mock_game_manager.end_session = AsyncMock(return_value=mock_game)

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            ws.receive_json()  # started

            ws.send_json({"type": "move", "move": "e2e4"})
            engine_data = ws.receive_json()
            game_over_data = ws.receive_json()

        assert engine_data["type"] == "engine_move"
        assert game_over_data["type"] == "game_over"
        assert game_over_data["result"] == GameResult.WHITE_WIN.value

    def test_invalid_message_type_returns_error(
        self,
        ws_client: TestClient,
    ) -> None:
        """Unknown message type returns error response."""
        with ws_client.websocket_connect("/ws/play") as ws:
            ws.send_json({"type": "invalid_type"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert "Unknown message type" in data["message"]

    def test_disconnect_mid_game_cleans_up_session(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Disconnecting mid-game calls cleanup_session."""
        session = self._make_session()
        mock_game_manager.create_session = AsyncMock(return_value=session)

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            ws.receive_json()  # started

        mock_game_manager.cleanup_session.assert_called_once_with("test-game-id")

    def test_resign_ends_game(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """Resign message ends game with engine as winner."""
        session = self._make_session(player_color="white")
        mock_game = self._make_mock_game()
        mock_game_manager.create_session = AsyncMock(return_value=session)
        mock_game_manager.get_session = MagicMock(return_value=session)
        mock_game_manager.end_session = AsyncMock(return_value=mock_game)

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json({"type": "start", "engine_id": "test-engine"})
            ws.receive_json()  # started

            ws.send_json({"type": "resign"})
            data = ws.receive_json()

        assert data["type"] == "game_over"
        assert data["result"] == GameResult.BLACK_WIN.value
        assert data["game_id"] == "test-game-id"

    def test_player_black_engine_moves_first(
        self,
        ws_client: TestClient,
        mock_game_manager: MagicMock,
    ) -> None:
        """When player is black, engine makes first move after start."""
        session = self._make_session(player_color="black")
        mock_game_manager.create_session = AsyncMock(return_value=session)
        mock_game_manager.make_engine_move = AsyncMock(
            return_value=("e2e4", "e4", "fen_after_e4", None)
        )

        with (
            patch("backend.ws.play._resolve_engine_path", return_value="/path/to/engine"),
            ws_client.websocket_connect("/ws/play") as ws,
        ):
            ws.send_json(
                {
                    "type": "start",
                    "engine_id": "test-engine",
                    "player_color": "black",
                }
            )
            started = ws.receive_json()
            engine_move = ws.receive_json()

        assert started["type"] == "started"
        assert engine_move["type"] == "engine_move"
        assert engine_move["move"] == "e2e4"
        assert engine_move["san"] == "e4"
