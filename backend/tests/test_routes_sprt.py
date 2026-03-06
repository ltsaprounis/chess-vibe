"""Tests for the SPRT routes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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

    def test_get_sprt_test_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests/nonexistent")
        assert resp.status_code == 404

    def test_cancel_nonexistent_test(self, client: TestClient) -> None:
        resp = client.post("/api/sprt/tests/nonexistent/cancel")
        assert resp.status_code == 404

    def test_list_sprt_tests_empty(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sprt_tests_returns_all(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        for i, status in enumerate(
            [SPRTStatus.RUNNING, SPRTStatus.COMPLETED, SPRTStatus.CANCELLED]
        ):
            sprt_repo.save_sprt_test(
                SPRTTest(
                    id=f"test-{i}",
                    engine_a="engine-a",
                    engine_b="engine-b",
                    time_control=FixedTimeControl(movetime_ms=100),
                    elo0=0.0,
                    elo1=5.0,
                    alpha=0.05,
                    beta=0.05,
                    created_at=datetime(2024, 1, 1, tzinfo=UTC),
                    status=status,
                    wins=0,
                    losses=0,
                    draws=0,
                    llr=0.0,
                )
            )

        resp = client.get("/api/sprt/tests")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        returned_ids = {t["id"] for t in data}
        assert returned_ids == {"test-0", "test-1", "test-2"}

    def test_list_sprt_tests_filter_by_status(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        for i, status in enumerate([SPRTStatus.RUNNING, SPRTStatus.COMPLETED]):
            sprt_repo.save_sprt_test(
                SPRTTest(
                    id=f"test-{i}",
                    engine_a="engine-a",
                    engine_b="engine-b",
                    time_control=FixedTimeControl(movetime_ms=100),
                    elo0=0.0,
                    elo1=5.0,
                    alpha=0.05,
                    beta=0.05,
                    created_at=datetime(2024, 1, 1, tzinfo=UTC),
                    status=status,
                    wins=0,
                    losses=0,
                    draws=0,
                    llr=0.0,
                )
            )

        resp = client.get("/api/sprt/tests", params={"status": "running"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-0"
        assert data[0]["status"] == "running"

    def test_list_sprt_tests_filter_by_engine_id(
        self,
        client: TestClient,
        sprt_repo: FileSPRTTestRepository,
    ) -> None:
        sprt_repo.save_sprt_test(
            SPRTTest(
                id="test-ab",
                engine_a="alpha",
                engine_b="beta",
                time_control=FixedTimeControl(movetime_ms=100),
                elo0=0.0,
                elo1=5.0,
                alpha=0.05,
                beta=0.05,
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                status=SPRTStatus.RUNNING,
                wins=0,
                losses=0,
                draws=0,
                llr=0.0,
            )
        )
        sprt_repo.save_sprt_test(
            SPRTTest(
                id="test-cd",
                engine_a="gamma",
                engine_b="delta",
                time_control=FixedTimeControl(movetime_ms=100),
                elo0=0.0,
                elo1=5.0,
                alpha=0.05,
                beta=0.05,
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                status=SPRTStatus.RUNNING,
                wins=0,
                losses=0,
                draws=0,
                llr=0.0,
            )
        )

        resp = client.get("/api/sprt/tests", params={"engine_id": "alpha"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-ab"

    def test_list_sprt_tests_invalid_status(self, client: TestClient) -> None:
        resp = client.get("/api/sprt/tests", params={"status": "bogus"})
        assert resp.status_code == 400
