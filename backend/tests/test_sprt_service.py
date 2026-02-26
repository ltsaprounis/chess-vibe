"""Tests for the SPRT service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from backend.services.sprt_service import SPRTProgress, SPRTService
from shared.storage.models import SPRTStatus, SPRTTest
from shared.time_control import FixedTimeControl


class TestSPRTServiceRecovery:
    """Tests for SPRT recovery on startup."""

    @pytest.fixture
    def sprt_repo(self) -> MagicMock:
        repo = MagicMock()
        repo.save_sprt_test = MagicMock()
        repo.get_sprt_test = MagicMock()
        repo.list_sprt_tests = MagicMock()
        repo.update_sprt_results = MagicMock()
        return repo

    @pytest.fixture
    def service(self, sprt_repo: MagicMock) -> SPRTService:
        return SPRTService(sprt_repo)

    @pytest.mark.asyncio
    async def test_recover_marks_running_as_cancelled(
        self, service: SPRTService, sprt_repo: MagicMock
    ) -> None:
        stale_test = SPRTTest(
            id="stale-1",
            engine_a="a",
            engine_b="b",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            alpha=0.05,
            beta=0.05,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            status=SPRTStatus.RUNNING,
        )
        sprt_repo.list_sprt_tests.return_value = [stale_test]

        count = await service.recover_on_startup()
        assert count == 1
        sprt_repo.update_sprt_results.assert_called_once()

        # Verify the updated test has CANCELLED status
        updated = sprt_repo.update_sprt_results.call_args[0][0]
        assert updated.status == SPRTStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_recover_no_stale_tests(self, service: SPRTService, sprt_repo: MagicMock) -> None:
        sprt_repo.list_sprt_tests.return_value = []
        count = await service.recover_on_startup()
        assert count == 0


class TestSPRTProgress:
    """Tests for SPRTProgress dataclass."""

    def test_defaults(self) -> None:
        p = SPRTProgress()
        assert p.wins == 0
        assert p.losses == 0
        assert p.draws == 0
        assert p.llr == 0.0
        assert p.games_total == 0


class TestSPRTServiceProperties:
    """Tests for SPRTService properties."""

    def test_running_tests_empty(self) -> None:
        repo = MagicMock()
        service = SPRTService(repo)
        assert service.running_tests == []

    def test_get_progress_nonexistent(self) -> None:
        repo = MagicMock()
        service = SPRTService(repo)
        assert service.get_progress("nonexistent") is None

    def test_subscribe_nonexistent(self) -> None:
        repo = MagicMock()
        service = SPRTService(repo)
        assert service.subscribe("nonexistent") is None

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self) -> None:
        repo = MagicMock()
        service = SPRTService(repo)
        result = await service.cancel_test("nonexistent")
        assert result is False
