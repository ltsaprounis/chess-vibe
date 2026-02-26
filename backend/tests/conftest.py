"""Pytest configuration for backend tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.main import create_app
from fastapi.testclient import TestClient
from shared.storage.file_store import FileGameRepository, FileSPRTTestRepository


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Provide a temporary data directory."""
    return tmp_path


@pytest.fixture
def game_repo(data_dir: Path) -> FileGameRepository:
    """Provide a game repository backed by the temp directory."""
    return FileGameRepository(data_dir)


@pytest.fixture
def sprt_repo(data_dir: Path) -> FileSPRTTestRepository:
    """Provide an SPRT test repository backed by the temp directory."""
    return FileSPRTTestRepository(data_dir)


@pytest.fixture
def client(data_dir: Path) -> TestClient:
    """Provide a FastAPI TestClient with a temporary data directory."""
    app = create_app(data_dir=data_dir)
    return TestClient(app, raise_server_exceptions=False)
