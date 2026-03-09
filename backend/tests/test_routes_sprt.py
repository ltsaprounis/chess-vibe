"""Tests for the SPRT routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from backend.main import create_app
from fastapi.testclient import TestClient
from shared.storage.file_store import FileSPRTTestRepository
from shared.storage.models import SPRTStatus, SPRTTest
from shared.time_control import FixedTimeControl


class TestSPRTRoutes:
    """Tests for SPRT test endpoints."""

    @pytest.fixture
    def data_dir(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture
    def client(self, data_dir: Path) -> TestClient:
        return TestClient(create_app(data_dir=data_dir), raise_server_exceptions=False)

    @pytest.fixture
    def sprt_repo(self, data_dir: Path) -> FileSPRTTestRepository:
        return FileSPRTTestRepository(data_dir)

    def test_get_sprt_test_found(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        test = SPRTTest(
            id="test-1",
            engine_a="engine-a",
            engine_b="engine-b",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            status=SPRTStatus.RUNNING,
            wins=5,
            losses=3,
            draws=2,
            llr=0.5,
        )
        sprt_repo.save_sprt_test(test)

        resp = client.get("/api/sprt/tests/test-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-1"
        assert data["status"] == "running"
        assert data["wins"] == 5
        assert data["losses"] == 3

    def test_list_sprt_tests_empty(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sprt_tests_returns_saved_tests(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        test = SPRTTest(
            id="test-list-1",
            engine_a="engine-a",
            engine_b="engine-b",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            status=SPRTStatus.RUNNING,
            wins=5,
            losses=3,
            draws=2,
            llr=0.5,
        )
        sprt_repo.save_sprt_test(test)

        resp = client.get("/api/sprt/tests")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-list-1"
        assert data[0]["status"] == "running"

    def test_get_sprt_test_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests/nonexistent")
        assert resp.status_code == 404

    def test_cancel_nonexistent_test(self, client: TestClient) -> None:
        resp = client.post("/api/sprt/tests/nonexistent/cancel")
        assert resp.status_code == 404

    def test_create_sprt_test_invalid_book_id_returns_400(self, client: TestClient) -> None:
        """POST /sprt/tests with a non-existent book_id returns 400."""
        resp = client.post(
            "/api/sprt/tests",
            json={
                "engine_a": "engine-a",
                "engine_b": "engine-b",
                "time_control": "movetime=100",
                "book_id": "nonexistent-book",
            },
        )
        assert resp.status_code == 400
        assert "nonexistent-book" in resp.json()["detail"]

    def test_create_sprt_test_valid_book_id_resolves_path(
        self, client: TestClient, data_dir: Path
    ) -> None:
        """POST /sprt/tests with a valid book_id resolves to a filesystem path."""
        books_dir = data_dir / "openings"
        books_dir.mkdir(parents=True)
        book_file = books_dir / "my-book.pgn"
        book_file.write_text("1. e4 e5 *")

        # The book repo uses the stem as the ID
        book_id = "my-book"

        with patch(
            "backend.services.sprt_service.SPRTService.start_test",
            new_callable=AsyncMock,
            return_value="test-123",
        ) as mock_start:
            resp = client.post(
                "/api/sprt/tests",
                json={
                    "engine_a": "engine-a",
                    "engine_b": "engine-b",
                    "time_control": "movetime=100",
                    "book_id": book_id,
                },
            )
            assert resp.status_code == 201
            # Verify the resolved filesystem path was passed to start_test
            mock_start.assert_called_once()
            call_kwargs = mock_start.call_args
            assert call_kwargs.kwargs["book_path"] == str(book_file)
