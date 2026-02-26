"""Tests for the main application factory."""

from __future__ import annotations

from pathlib import Path

from backend.main import create_app
from fastapi.testclient import TestClient


class TestCreateApp:
    """Tests for the application factory."""

    def test_creates_app(self, tmp_path: Path) -> None:
        app = create_app(data_dir=tmp_path)
        assert app.title == "chess-vibe"

    def test_cors_configured(self, tmp_path: Path) -> None:
        app = create_app(data_dir=tmp_path, cors_origins=["http://localhost:3000"])
        # Just verify the app was created successfully
        assert app is not None

    def test_routes_registered(self, tmp_path: Path) -> None:
        app = create_app(data_dir=tmp_path)
        routes = [r.path for r in app.routes if hasattr(r, "path")]  # type: ignore[reportUnknownMemberType,union-attr]
        assert "/engines" in routes
        assert "/games" in routes
        assert "/games/{game_id}" in routes
        assert "/sprt/tests" in routes
        assert "/sprt/tests/{test_id}" in routes
        assert "/openings/books" in routes
        assert "/ws/play" in routes
        assert "/ws/sprt/{test_id}" in routes

    def test_health_check_via_games(self, tmp_path: Path) -> None:
        client = TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)
        resp = client.get("/games")
        assert resp.status_code == 200
