"""Tests for the SPRT service."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
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


def _json_line(**fields: Any) -> bytes:
    """Encode *fields* as one JSON-lines message from the runner."""
    return json.dumps(fields).encode() + b"\n"


class TestSPRTServiceMonitorTerminalStatus:
    """Tests that ``_monitor`` always writes a terminal status.

    The runner emits ``complete`` only when SPRT reaches a decision. Every
    other way it can stop — a fatal error, every worker dying (which exits
    0), SIGTERM, SIGKILL — used to leave the record at ``RUNNING`` forever,
    un-cancellable because ``_running`` had already been popped.
    """

    @pytest.fixture
    def sprt_repo(self) -> MagicMock:
        repo = MagicMock()
        repo.get_sprt_test = MagicMock(
            return_value=SPRTTest(
                id="test-1",
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
        )
        repo.update_sprt_results = MagicMock()
        return repo

    @staticmethod
    def _running(lines: list[bytes], *, returncode: int = 0) -> Any:
        stdout = MagicMock()
        stdout.readline = AsyncMock(side_effect=[*lines, b""])
        process = MagicMock()
        process.stdout = stdout
        process.wait = AsyncMock(return_value=returncode)
        process.returncode = returncode
        return _RunningTest(test_id="test-1", process=process)

    @staticmethod
    def _final_status(sprt_repo: MagicMock) -> SPRTStatus:
        assert sprt_repo.update_sprt_results.call_args is not None
        status: SPRTStatus = sprt_repo.update_sprt_results.call_args[0][0].status
        return status

    @pytest.mark.asyncio
    async def test_complete_marks_completed(self, sprt_repo: MagicMock) -> None:
        running = self._running([_json_line(type="complete", result="H1", total_games=10, llr=3.0)])
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_interrupted_marks_cancelled(self, sprt_repo: MagicMock) -> None:
        """SIGTERM from cancel_test makes the runner emit ``interrupted``."""
        running = self._running([_json_line(type="interrupted", games_played=4)], returncode=1)
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_fatal_error_exit_marks_failed(self, sprt_repo: MagicMock) -> None:
        """A fail-fast runner exit emits ``error`` and never ``complete``."""
        running = self._running(
            [_json_line(type="error", message="Failed to resolve engines: no such engine")],
            returncode=1,
        )
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.FAILED

    @pytest.mark.asyncio
    async def test_clean_exit_without_complete_marks_failed(self, sprt_repo: MagicMock) -> None:
        """An all-workers-died abort breaks the runner's loop but still exits 0."""
        running = self._running(
            [_json_line(type="error", message="All workers died unexpectedly")],
            returncode=0,
        )
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.FAILED

    @pytest.mark.asyncio
    async def test_silent_death_marks_failed(self, sprt_repo: MagicMock) -> None:
        """SIGKILL leaves no output at all."""
        running = self._running([], returncode=-9)
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.FAILED

    @pytest.mark.asyncio
    async def test_silent_death_after_cancel_marks_cancelled(self, sprt_repo: MagicMock) -> None:
        """A cancelled run that dies before emitting ``interrupted`` is still a cancel."""
        running = self._running([], returncode=-15)
        running.cancel_requested = True
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_nonfatal_error_does_not_end_the_test(self, sprt_repo: MagicMock) -> None:
        """``error`` is per-game and non-fatal at 2 of its 4 runner call sites.

        A single dead worker must not mark the whole test FAILED — the runner
        carries on and can still reach a decision.
        """
        running = self._running(
            [
                _json_line(type="error", message="Worker for game g1 died unexpectedly (pid=7)"),
                _json_line(type="progress", wins=5, losses=3, draws=2, llr=1.0, games_total=10),
                _json_line(type="complete", result="H1", total_games=10, llr=3.0),
            ]
        )
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert self._final_status(sprt_repo) == SPRTStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_broadcasts_test_finished_to_subscribers(self, sprt_repo: MagicMock) -> None:
        """Without a terminal message the WS handler's queue.get() blocks forever."""
        running = self._running([_json_line(type="error", message="boom")], returncode=1)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        running.subscribers.append(queue)
        service = SPRTService(sprt_repo)

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert queue.get_nowait()["type"] == "error"
        finished = queue.get_nowait()
        assert finished["type"] == "test_finished"
        assert finished["status"] == "failed"

    @pytest.mark.asyncio
    async def test_removes_test_from_running(self, sprt_repo: MagicMock) -> None:
        running = self._running([], returncode=1)
        service = SPRTService(sprt_repo)
        service._running["test-1"] = running  # pyright: ignore[reportPrivateUsage]

        await service._monitor(running)  # pyright: ignore[reportPrivateUsage]

        assert service.running_tests == []


class TestSPRTServiceCancelFlag:
    """Tests that a deliberate cancel is distinguishable from a crash."""

    @pytest.mark.asyncio
    async def test_cancel_test_records_the_request(self) -> None:
        process = MagicMock()
        process.send_signal = MagicMock()
        running = _RunningTest(test_id="test-1", process=process)
        service = SPRTService(MagicMock())
        service._running["test-1"] = running  # pyright: ignore[reportPrivateUsage]

        assert await service.cancel_test("test-1") is True
        assert running.cancel_requested is True

    @pytest.mark.asyncio
    async def test_failed_signal_does_not_record_a_cancel(self) -> None:
        process = MagicMock()
        process.send_signal = MagicMock(side_effect=ProcessLookupError)
        running = _RunningTest(test_id="test-1", process=process)
        service = SPRTService(MagicMock())
        service._running["test-1"] = running  # pyright: ignore[reportPrivateUsage]

        assert await service.cancel_test("test-1") is False
        assert running.cancel_requested is False


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
            coroutine_names = [
                call.args[0].cr_code.co_qualname for call in mock_create_task.call_args_list
            ]
            assert "SPRTService._monitor" in coroutine_names
            assert "SPRTService._drain_stderr" in coroutine_names
