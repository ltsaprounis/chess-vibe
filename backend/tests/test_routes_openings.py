"""Tests for the openings routes."""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.main import create_app
from fastapi.testclient import TestClient


class TestOpeningsRoutes:
    """Tests for GET /openings/books and POST /openings/books."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def client(self, data_dir: Path) -> TestClient:
        return TestClient(create_app(data_dir=data_dir), raise_server_exceptions=False)

    def test_list_books_empty(self, client: TestClient) -> None:
        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_books_with_files(self, client: TestClient, data_dir: Path) -> None:
        books_dir = data_dir / "openings"
        books_dir.mkdir(parents=True)
        (books_dir / "test.pgn").write_text("1. e4 e5 *")
        (books_dir / "positions.epd").write_text("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")

        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {b["name"] for b in data}
        assert "test" in names
        assert "positions" in names

    def test_list_books_does_not_expose_path(self, client: TestClient, data_dir: Path) -> None:
        """Verify the response does not leak filesystem paths."""
        books_dir = data_dir / "openings"
        books_dir.mkdir(parents=True)
        (books_dir / "test.pgn").write_text("1. e4 e5 *")

        resp = client.get("/api/openings/books")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert "path" not in data[0]

    def test_upload_book_pgn(self, client: TestClient, data_dir: Path) -> None:
        resp = client.post(
            "/api/openings/books",
            files={"file": ("test.pgn", b"1. e4 e5 *", "application/octet-stream")},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "test.pgn"
        assert data["format"] == "pgn"
        assert "path" not in data

        # Verify file was saved via repository (in openings dir)
        books_dir = data_dir / "openings"
        assert books_dir.is_dir()
        saved_files = list(books_dir.glob("*.pgn"))
        assert len(saved_files) == 1

    def test_upload_book_epd(self, client: TestClient) -> None:
        resp = client.post(
            "/api/openings/books",
            files={
                "file": (
                    "positions.epd",
                    b"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
                    "application/octet-stream",
                )
            },
        )
        assert resp.status_code == 201
        assert resp.json()["format"] == "epd"

    def test_upload_invalid_format(self, client: TestClient) -> None:
        resp = client.post(
            "/api/openings/books",
            files={"file": ("test.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400
