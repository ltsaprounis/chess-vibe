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
            resp = client.get("/engines")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["id"] == "test-engine"
            assert data[0]["name"] == "Test Engine"

    def test_list_engines_registry_error(self, client: TestClient) -> None:
        with patch(
            "backend.routes.engines.load_registry",
            side_effect=EngineRegistryError("bad file"),
        ):
            resp = client.get("/engines")
            assert resp.status_code == 500
