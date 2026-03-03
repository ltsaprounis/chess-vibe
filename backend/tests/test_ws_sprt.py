"""Tests for the SPRT WebSocket endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.ws import sprt as ws_sprt
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestSPRTWebSocket:
    """Tests for the /ws/sprt/{test_id} WebSocket endpoint."""

    @pytest.fixture
    def mock_sprt_service(self) -> MagicMock:
        """Create a mock SPRTService."""
        service = MagicMock()
        service.subscribe = MagicMock(return_value=None)
        service.unsubscribe = MagicMock()
        return service

    @pytest.fixture
    def ws_client(self, mock_sprt_service: MagicMock) -> TestClient:
        """Create a TestClient with mocked SPRTService."""
        app = FastAPI()
        app.state.sprt_service = mock_sprt_service
        app.include_router(ws_sprt.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_nonexistent_test_returns_error(
        self,
        ws_client: TestClient,
        mock_sprt_service: MagicMock,
    ) -> None:
        """Connecting to non-existent test_id returns error and closes."""
        mock_sprt_service.subscribe = MagicMock(return_value=None)

        with ws_client.websocket_connect("/ws/sprt/nonexistent-id") as ws:
            data = ws.receive_json()

        assert data["type"] == "error"
        assert "not running" in data["message"]

    def test_subscribe_receives_progress(
        self,
        ws_client: TestClient,
        mock_sprt_service: MagicMock,
    ) -> None:
        """Subscriber receives progress and completion messages."""
        mock_queue = AsyncMock()
        mock_queue.get = AsyncMock(
            side_effect=[
                {"type": "progress", "wins": 5, "losses": 3, "draws": 2},
                {"type": "complete", "result": "H1", "total_games": 10, "llr": 3.0},
            ]
        )
        mock_sprt_service.subscribe = MagicMock(return_value=mock_queue)

        with ws_client.websocket_connect("/ws/sprt/test-123") as ws:
            progress = ws.receive_json()
            complete = ws.receive_json()

        assert progress["type"] == "progress"
        assert progress["wins"] == 5
        assert progress["losses"] == 3
        assert progress["draws"] == 2
        assert complete["type"] == "complete"
        assert complete["result"] == "H1"

    def test_completion_triggers_unsubscribe(
        self,
        ws_client: TestClient,
        mock_sprt_service: MagicMock,
    ) -> None:
        """Complete message triggers unsubscribe in finally block."""
        mock_queue = AsyncMock()
        mock_queue.get = AsyncMock(
            return_value={"type": "complete", "result": "H0", "total_games": 50, "llr": -3.0}
        )
        mock_sprt_service.subscribe = MagicMock(return_value=mock_queue)

        with ws_client.websocket_connect("/ws/sprt/test-456") as ws:
            ws.receive_json()

        mock_sprt_service.unsubscribe.assert_called_once()

    def test_multiple_progress_messages_relayed(
        self,
        ws_client: TestClient,
        mock_sprt_service: MagicMock,
    ) -> None:
        """Multiple progress messages are relayed correctly."""
        mock_queue = AsyncMock()
        mock_queue.get = AsyncMock(
            side_effect=[
                {"type": "progress", "wins": 1, "losses": 0, "draws": 0},
                {"type": "progress", "wins": 2, "losses": 1, "draws": 0},
                {"type": "progress", "wins": 3, "losses": 1, "draws": 1},
                {"type": "complete", "result": "H1", "total_games": 5, "llr": 2.5},
            ]
        )
        mock_sprt_service.subscribe = MagicMock(return_value=mock_queue)

        with ws_client.websocket_connect("/ws/sprt/test-789") as ws:
            msg1 = ws.receive_json()
            msg2 = ws.receive_json()
            msg3 = ws.receive_json()
            complete = ws.receive_json()

        assert msg1["wins"] == 1
        assert msg2["wins"] == 2
        assert msg3["wins"] == 3
        assert complete["type"] == "complete"
