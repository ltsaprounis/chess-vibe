"""Tests for the SPRT runner CLI entry point and orchestration."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from shared.storage.models import GameResult
from shared.time_control import DepthTimeControl, FixedTimeControl, parse_time_control
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig
from sprt_runner.openings import OpeningPair
from sprt_runner.runner import (
    RunConfig,
    WorkerResult,
    WorkerTask,
    _cleanup_workers,  # type: ignore[reportPrivateUsage]
    format_complete_message,
    format_error_message,
    format_game_result_message,
    format_progress_message,
    run_sprt,
    worker_entry,
)


class TestParseTimeControl:
    """Tests for CLI time control string parsing."""

    def test_fixed_time(self) -> None:
        tc = parse_time_control("movetime=1000")
        assert isinstance(tc, FixedTimeControl)
        assert tc.movetime_ms == 1000

    def test_depth(self) -> None:
        from shared.time_control import DepthTimeControl

        tc = parse_time_control("depth=10")
        assert isinstance(tc, DepthTimeControl)
        assert tc.depth == 10

    def test_nodes(self) -> None:
        from shared.time_control import NodesTimeControl

        tc = parse_time_control("nodes=50000")
        assert isinstance(tc, NodesTimeControl)
        assert tc.nodes == 50000

    def test_increment(self) -> None:
        from shared.time_control import IncrementTimeControl

        tc = parse_time_control("wtime=60000,btime=60000,winc=1000,binc=1000")
        assert isinstance(tc, IncrementTimeControl)
        assert tc.wtime_ms == 60000

    def test_invalid_format(self) -> None:
        with pytest.raises(ValueError, match="Unknown time control"):
            parse_time_control("invalid=5")


class TestRunConfig:
    """Tests for RunConfig construction."""

    def test_basic_config(self) -> None:
        config = RunConfig(
            base="random-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
        )
        assert config.base == "random-engine"
        assert config.test == "test-engine"
        assert config.concurrency == 1
        assert config.alpha == 0.05
        assert config.beta == 0.05

    def test_custom_concurrency(self) -> None:
        config = RunConfig(
            base="random-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            concurrency=4,
        )
        assert config.concurrency == 4


class TestJSONLineFormatters:
    """Tests for JSON-lines output formatting."""

    def test_game_result_message(self) -> None:
        msg = format_game_result_message(
            game_id="game-1",
            result=GameResult.WHITE_WIN,
            termination="checkmate",
            move_count=42,
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "game_result"
        assert parsed["game_id"] == "game-1"
        assert parsed["result"] == "1-0"
        assert parsed["termination"] == "checkmate"
        assert parsed["move_count"] == 42

    def test_progress_message(self) -> None:
        msg = format_progress_message(
            wins=10,
            losses=5,
            draws=3,
            llr=1.5,
            lower_bound=-2.94,
            upper_bound=2.94,
            games_total=18,
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "progress"
        assert parsed["wins"] == 10
        assert parsed["losses"] == 5
        assert parsed["draws"] == 3
        assert parsed["llr"] == 1.5

    def test_complete_message(self) -> None:
        msg = format_complete_message(
            result="H1",
            total_games=100,
            llr=3.5,
        )
        parsed = json.loads(msg)
        assert parsed["type"] == "complete"
        assert parsed["result"] == "H1"
        assert parsed["total_games"] == 100

    def test_error_message(self) -> None:
        msg = format_error_message("Engine crashed unexpectedly")
        parsed = json.loads(msg)
        assert parsed["type"] == "error"
        assert parsed["message"] == "Engine crashed unexpectedly"

    def test_all_messages_are_valid_json(self) -> None:
        """All formatters should produce valid single-line JSON."""
        messages = [
            format_game_result_message("g1", GameResult.DRAW, "stalemate", 50),
            format_progress_message(0, 0, 0, 0.0, -3.0, 3.0, 0),
            format_complete_message("H0", 200, -3.5),
            format_error_message("test error"),
        ]
        for msg in messages:
            assert "\n" not in msg
            json.loads(msg)  # Should not raise


class TestWorkerResult:
    """Tests for the worker result dataclass."""

    def test_success_result(self) -> None:
        result = WorkerResult(
            game_id="game-1",
            result=GameResult.WHITE_WIN,
            termination="checkmate",
            move_count=42,
            swap_colors=False,
        )
        assert result.game_id == "game-1"
        assert result.result == GameResult.WHITE_WIN
        assert result.error is None

    def test_error_result(self) -> None:
        result = WorkerResult(
            game_id="game-2",
            result=None,
            termination=None,
            move_count=0,
            swap_colors=True,
            error="Engine crashed",
        )
        assert result.result is None
        assert result.error == "Engine crashed"


class TestWorkerTask:
    """Tests for the worker task dataclass."""

    def test_task_creation(self) -> None:
        config = GameConfig(
            time_control=DepthTimeControl(depth=5),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
        )
        task = WorkerTask(
            game_id="game-1",
            white_cmd="echo white",
            black_cmd="echo black",
            game_config=config,
            swap_colors=False,
        )
        assert task.game_id == "game-1"
        assert task.swap_colors is False


class TestWorkerEntry:
    """Tests for the worker process entry point with IPC via multiprocessing.Queue."""

    def test_worker_puts_result_on_queue(self) -> None:
        """Worker should put a result (even error) on the queue."""
        config = GameConfig(
            time_control=DepthTimeControl(depth=1),
            adjudication=AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0),
        )
        task = WorkerTask(
            game_id="test-game",
            white_cmd="nonexistent_engine",
            black_cmd="nonexistent_engine",
            game_config=config,
            swap_colors=False,
        )

        result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()

        # Run worker in a subprocess
        worker = multiprocessing.Process(
            target=worker_entry,
            args=(task, result_queue),
        )
        worker.start()
        worker.join(timeout=10)

        # Worker should have completed
        assert not worker.is_alive()
        assert worker.exitcode == 0

        # Worker should have put a result on the queue
        assert not result_queue.empty()
        result = result_queue.get(timeout=1)
        assert result.game_id == "test-game"
        # Should be an error since the engine doesn't exist
        assert result.error is not None


class TestCleanupWorkers:
    """Tests for the _cleanup_workers helper that joins finished workers."""

    def test_worker_still_alive_after_join_stays_in_list(self) -> None:
        """A genuinely alive worker stays in active_workers after cleanup."""
        worker = MagicMock(spec=multiprocessing.Process)
        worker.is_alive.return_value = True

        result = _cleanup_workers([worker])

        worker.join.assert_called_once_with(timeout=1)
        assert result == [worker]

    def test_worker_exits_after_join_is_removed(self) -> None:
        """A worker that exits during join() is removed from active_workers."""
        worker = MagicMock(spec=multiprocessing.Process)
        worker.is_alive.return_value = False

        result = _cleanup_workers([worker])

        worker.join.assert_called_once_with(timeout=1)
        assert result == []

    def test_race_condition_worker_alive_then_exits(self) -> None:
        """Simulates the race: worker is alive before join, exits during join.

        Before the fix, is_alive() was checked *before* join(), so the worker
        would stay in active_workers even though it had finished. The fix calls
        join(timeout=1) first, giving the worker time to exit, then checks
        is_alive().
        """
        worker = MagicMock(spec=multiprocessing.Process)

        # Simulate: join() causes the worker to finish, so is_alive() returns False
        def side_effect(timeout: int = 0) -> None:
            worker.is_alive.return_value = False

        worker.join.side_effect = side_effect
        worker.is_alive.return_value = True  # alive before join

        result = _cleanup_workers([worker])

        worker.join.assert_called_once_with(timeout=1)
        worker.is_alive.assert_called()
        assert result == []

    def test_multiple_workers_mixed_states(self) -> None:
        """With multiple workers, only genuinely alive ones remain."""
        alive_worker = MagicMock(spec=multiprocessing.Process)
        alive_worker.is_alive.return_value = True

        dead_worker = MagicMock(spec=multiprocessing.Process)
        dead_worker.is_alive.return_value = False

        racing_worker = MagicMock(spec=multiprocessing.Process)

        def race_side_effect(timeout: int = 0) -> None:
            racing_worker.is_alive.return_value = False

        racing_worker.join.side_effect = race_side_effect
        racing_worker.is_alive.return_value = True

        result = _cleanup_workers([alive_worker, dead_worker, racing_worker])

        assert result == [alive_worker]
        alive_worker.join.assert_called_once_with(timeout=1)
        dead_worker.join.assert_called_once_with(timeout=1)
        racing_worker.join.assert_called_once_with(timeout=1)

    def test_no_zombie_processes_all_finished_joined(self) -> None:
        """All finished workers have join() called to prevent zombies."""
        workers: list[multiprocessing.Process] = []
        for _ in range(3):
            w = MagicMock(spec=multiprocessing.Process)
            w.is_alive.return_value = False
            workers.append(w)

        result = _cleanup_workers(workers)

        assert result == []
        for w in workers:
            w.join.assert_called_once_with(timeout=1)  # type: ignore[union-attr]

    def test_empty_worker_list(self) -> None:
        """Cleanup with no workers returns empty list."""
        result = _cleanup_workers([])
        assert result == []

    def test_concurrency_one_sequential_cleanup(self) -> None:
        """At concurrency=1, a single worker that finishes is cleaned up.

        This is the core scenario: after result_queue.get() returns at
        concurrency=1, the single worker must be cleaned up so a new
        worker can be launched.
        """
        worker = MagicMock(spec=multiprocessing.Process)

        def finish_on_join(timeout: int = 0) -> None:
            worker.is_alive.return_value = False

        worker.join.side_effect = finish_on_join
        worker.is_alive.return_value = True

        result = _cleanup_workers([worker])

        assert result == []
        worker.join.assert_called_once_with(timeout=1)


# ---------------------------------------------------------------------------
# Helpers for run_sprt colour-assignment tests
# ---------------------------------------------------------------------------


class _FakeProcess:
    """Fake multiprocessing.Process that captures tasks and injects results.

    Each "process" immediately puts a predefined result on the queue so the
    coordinator loop advances without spawning a real subprocess.
    """

    captured_tasks: ClassVar[list[WorkerTask]] = []

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        task, queue = args
        _FakeProcess.captured_tasks.append(task)
        queue.put(
            WorkerResult(
                game_id=task.game_id,
                result=GameResult.WHITE_WIN,
                termination="checkmate",
                move_count=20,
                swap_colors=task.swap_colors,
            )
        )
        self._alive = False

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        pass


def _mock_parse_engine_spec(_spec: str) -> None:
    """Mock parse_engine_spec that returns None for any input."""
    return None


def _mock_load_openings(_path: Path) -> list[str]:
    """Mock load_openings that returns an empty list."""
    return []


class TestColorAlternationNoBook:
    """Verify colour alternation when no opening book is provided."""

    @pytest.mark.asyncio
    async def test_no_book_alternates_colors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without an opening book, swap_colors should alternate each game."""
        _FakeProcess.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcess)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        # With alternating WHITE_WIN and elo1=500, SPRT converges quickly.
        assert len(_FakeProcess.captured_tasks) >= 2

        for i, task in enumerate(_FakeProcess.captured_tasks):
            expected_swap = i % 2 == 1
            assert task.swap_colors == expected_swap, (
                f"Game {i}: expected swap_colors={expected_swap}, got {task.swap_colors}"
            )

    @pytest.mark.asyncio
    async def test_no_book_start_fen_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without an opening book, start_fen should always be None."""
        _FakeProcess.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcess)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        for task in _FakeProcess.captured_tasks:
            assert task.game_config.start_fen is None


class TestColorAssignmentWithBook:
    """Verify book-based colour assignment is unchanged."""

    @pytest.mark.asyncio
    async def test_book_based_color_assignment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With an opening book, swap_colors follows the pair list."""
        _FakeProcess.captured_tasks = []

        e1e4_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        d2d4_fen = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1"
        pairs = [
            OpeningPair(fen=e1e4_fen, swap_colors=False),
            OpeningPair(fen=e1e4_fen, swap_colors=True),
            OpeningPair(fen=d2d4_fen, swap_colors=False),
            OpeningPair(fen=d2d4_fen, swap_colors=True),
        ]

        def _mock_make_pairs(_fens: list[str]) -> list[OpeningPair]:
            return pairs

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcess)
        # Inject opening_pairs directly by patching load_openings/make_opening_pairs
        monkeypatch.setattr("sprt_runner.runner.load_openings", _mock_load_openings)
        monkeypatch.setattr("sprt_runner.runner.make_opening_pairs", _mock_make_pairs)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=Path("/fake/book.epd"),
        )

        await run_sprt(config)

        assert len(_FakeProcess.captured_tasks) >= 2

        for i, task in enumerate(_FakeProcess.captured_tasks):
            expected_pair = pairs[i % len(pairs)]
            assert task.swap_colors == expected_pair.swap_colors, (
                f"Game {i}: expected swap_colors={expected_pair.swap_colors}, "
                f"got {task.swap_colors}"
            )
            assert task.game_config.start_fen == expected_pair.fen


class _SlowFakeProcess:
    """Fake process that delays putting the result on the queue.

    This simulates a real worker that takes time to produce a result,
    allowing us to verify that the event loop remains responsive while
    waiting for ``result_queue.get()``.
    """

    captured_tasks: ClassVar[list[WorkerTask]] = []

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self._task, self._queue = args
        _SlowFakeProcess.captured_tasks.append(self._task)
        self._alive = True

    def start(self) -> None:
        """Schedule delayed result delivery via the event loop."""
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, self._deliver)

    def _deliver(self) -> None:
        self._queue.put(
            WorkerResult(
                game_id=self._task.game_id,
                result=GameResult.WHITE_WIN,
                termination="checkmate",
                move_count=20,
                swap_colors=self._task.swap_colors,
            )
        )
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        pass


class TestRunSprtNonBlocking:
    """Verify that run_sprt() does not block the event loop."""

    @pytest.mark.asyncio
    async def test_cancellation_during_queue_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """run_sprt() should be cancellable while waiting for a worker result.

        If the blocking ``result_queue.get()`` were called directly (without
        ``run_in_executor``), ``asyncio.CancelledError`` could not be delivered
        until the 300-second timeout expired.  With the executor wrapper the
        cancellation is delivered promptly.
        """
        _SlowFakeProcess.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _SlowFakeProcess)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        task = asyncio.create_task(run_sprt(config))

        # Let the first worker be launched and the await to begin
        await asyncio.sleep(0.01)

        # Cancel the task — should succeed promptly
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
