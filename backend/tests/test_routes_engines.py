"""Tests for the engines route."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from backend.main import create_app
from fastapi.testclient import TestClient
from shared.engine_registry import EngineEntry, EngineRegistryError


class TestEnginesRoute:
    """Tests for GET /engines."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        return TestClient(create_app(data_dir=tmp_path), raise_server_exceptions=False)

    def test_list_engines_success(self, client: TestClient, tmp_path: Path) -> None:
        entries = [
            EngineEntry(
                id="test-engine",
                name="Test Engine",
                dir="engines/test",
                build=None,
                run=".venv/bin/python -m test_engine",
            )
        ]
        with patch("backend.routes.engines.load_registry", return_value=entries):
            resp = client.get("/api/engines")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["id"] == "test-engine"
            assert data[0]["name"] == "Test Engine"

    def test_list_engines_does_not_expose_dir_or_run(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verify the response does not leak filesystem paths or shell commands."""
        entries = [
            EngineEntry(
                id="test-engine",
                name="Test Engine",
                dir="engines/test",
                build=None,
                run=".venv/bin/python -m test_engine",
            )
        ]
        with patch("backend.routes.engines.load_registry", return_value=entries):
            resp = client.get("/api/engines")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert "dir" not in data[0]
            assert "run" not in data[0]

    def test_list_engines_registry_error(self, client: TestClient) -> None:
        with patch(
            "backend.routes.engines.load_registry",
            side_effect=EngineRegistryError("bad file"),
        ):
            resp = client.get("/api/engines")
            assert resp.status_code == 500

    def test_list_engines_uses_repo_root_for_registry(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Verify load_registry is called with get_repo_root() / 'engines.json'."""
        fake_root = Path("/fake/repo/root")
        entries = [
            EngineEntry(
                id="e1",
                name="E1",
                dir="engines/e1",
                build=None,
                run="./run",
            )
        ]
        with (
            patch("backend.routes.engines.get_repo_root", return_value=fake_root),
            patch("backend.routes.engines.load_registry", return_value=entries) as mock_load,
        ):
            resp = client.get("/api/engines")
            assert resp.status_code == 200
            mock_load.assert_called_once_with(fake_root / "engines.json")
