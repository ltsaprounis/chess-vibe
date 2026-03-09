"""Tests for the SPRT service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import backend.services.sprt_service as _sprt_mod
import pytest
from backend.services.sprt_service import SPRTProgress, SPRTService
from shared.storage.models import SPRTStatus, SPRTTest
from shared.time_control import FixedTimeControl

_RunningTest = _sprt_mod._RunningTest  # pyright: ignore[reportPrivateUsage]


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


class TestSPRTServiceStderr:
    """Tests for SPRT service stderr draining."""

    @pytest.mark.asyncio
    async def test_drain_stderr_logs_output(self) -> None:
        """_drain_stderr reads and logs stderr lines without hanging."""
        stderr_lines = [b"warning: something\n", b"debug info\n", b""]
        stderr_mock = MagicMock()
        stderr_mock.readline = AsyncMock(side_effect=stderr_lines)

        process = MagicMock()
        process.stderr = stderr_mock

        running = _RunningTest(test_id="test-1", process=process)
        service = SPRTService(MagicMock())

        await service._drain_stderr(running)  # pyright: ignore[reportPrivateUsage]

        assert stderr_mock.readline.call_count == 3

    @pytest.mark.asyncio
    async def test_drain_stderr_handles_empty_stream(self) -> None:
        """_drain_stderr handles immediate EOF gracefully."""
        stderr_mock = MagicMock()
        stderr_mock.readline = AsyncMock(return_value=b"")

        process = MagicMock()
        process.stderr = stderr_mock

        running = _RunningTest(test_id="test-2", process=process)
        service = SPRTService(MagicMock())

        await service._drain_stderr(running)  # pyright: ignore[reportPrivateUsage]

        stderr_mock.readline.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_test_spawns_stderr_drain_task(self) -> None:
        """start_test creates a background task to drain stderr."""
        repo = MagicMock()
        repo.save_sprt_test = MagicMock()
        service = SPRTService(repo)

        mock_process = MagicMock()
        mock_process.pid = 42
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_exec.return_value = mock_process
            await service.start_test(
                engine_a="engine_a",
                engine_b="engine_b",
                time_control_str="movetime=100",
                elo0=0.0,
                elo1=5.0,
            )

            # Two tasks should be created: _monitor and _drain_stderr
            assert mock_create_task.call_count == 2
