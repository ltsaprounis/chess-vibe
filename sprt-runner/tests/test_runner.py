"""Tests for the SPRT runner CLI entry point and orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import shlex
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
    _resolve_run_command,  # type: ignore[reportPrivateUsage]
    _terminate_all_workers,  # type: ignore[reportPrivateUsage]
    build_parser,
    format_complete_message,
    format_error_message,
    format_game_result_message,
    format_interrupted_message,
    format_progress_message,
    run_sprt,
    worker_entry,
)
from sprt_runner.worktree import EngineSpec

# Re-export for monkeypatching
_QUEUE_TIMEOUT_SECONDS = "sprt_runner.runner._QUEUE_TIMEOUT_SECONDS"


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


class TestResolveRunCommand:
    """Tests for _resolve_run_command with various path formats."""

    def test_path_without_spaces(self) -> None:
        engine_dir = Path("/home/user/engines/my-engine")
        result = _resolve_run_command(".venv/bin/python -m engine", engine_dir)
        parsed = shlex.split(result)
        assert parsed[0] == "/home/user/engines/my-engine/.venv/bin/python"
        assert parsed[1:] == ["-m", "engine"]

    def test_path_with_spaces(self) -> None:
        engine_dir = Path("/Users/My Name/repos/engine")
        result = _resolve_run_command(".venv/bin/python -m engine", engine_dir)
        parsed = shlex.split(result)
        assert parsed[0] == "/Users/My Name/repos/engine/.venv/bin/python"
        assert parsed[1:] == ["-m", "engine"]

    def test_single_executable_with_spaces(self) -> None:
        engine_dir = Path("/path/with spaces/engine")
        result = _resolve_run_command("my_binary", engine_dir)
        parsed = shlex.split(result)
        assert parsed == ["/path/with spaces/engine/my_binary"]

    def test_empty_command(self) -> None:
        result = _resolve_run_command("", Path("/some/dir"))
        assert result == ""

    def test_roundtrip_shlex_split(self) -> None:
        """Round-trip: _resolve_run_command output → shlex.split → correct argv."""
        engine_dir = Path("/Users/My Name/My Projects/chess engine")
        result = _resolve_run_command(".venv/bin/python -m engine --verbose", engine_dir)
        argv = shlex.split(result)
        assert argv[0] == str(engine_dir / ".venv/bin/python")
        assert argv[1:] == ["-m", "engine", "--verbose"]


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
    _next_pid: ClassVar[int] = 7000

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
        self._pid = _FakeProcess._next_pid
        _FakeProcess._next_pid += 1

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        pass


def _mock_parse_engine_spec(_spec: str) -> EngineSpec:
    """Mock parse_engine_spec that returns a no-commit EngineSpec."""
    return EngineSpec(engine_id="mock", commit=None)


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
    _next_pid: ClassVar[int] = 6000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self._task, self._queue = args
        _SlowFakeProcess.captured_tasks.append(self._task)
        self._alive = True
        self._pid = _SlowFakeProcess._next_pid
        _SlowFakeProcess._next_pid += 1

    @property
    def pid(self) -> int:
        return self._pid

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

    def kill(self) -> None:
        self._alive = False


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


# ---------------------------------------------------------------------------
# Dead-worker error reporting tests
# ---------------------------------------------------------------------------


class TestProgressMessageWithErrors:
    """Verify format_progress_message includes the errors field."""

    def test_progress_message_default_errors(self) -> None:
        """Errors defaults to 0 when not specified."""
        msg = format_progress_message(
            wins=1, losses=0, draws=0, llr=0.5, lower_bound=-2.94, upper_bound=2.94, games_total=1
        )
        parsed = json.loads(msg)
        assert parsed["errors"] == 0

    def test_progress_message_with_errors(self) -> None:
        """Errors field is included when explicitly set."""
        msg = format_progress_message(
            wins=5,
            losses=2,
            draws=1,
            llr=1.0,
            lower_bound=-2.94,
            upper_bound=2.94,
            games_total=8,
            errors=3,
        )
        parsed = json.loads(msg)
        assert parsed["errors"] == 3
        assert parsed["games_total"] == 8


class _DeadFakeProcess:
    """Fake process that simulates a worker dying without producing a result.

    Never puts anything on the queue, immediately reports ``is_alive()``
    as ``False``, and exposes a deterministic ``pid``.
    """

    captured_tasks: ClassVar[list[WorkerTask]] = []
    next_pid: ClassVar[int] = 9000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        task, _queue = args
        _DeadFakeProcess.captured_tasks.append(task)
        # Do NOT put anything on the queue — simulates death before result
        self._alive = False
        self._pid = _DeadFakeProcess.next_pid
        _DeadFakeProcess.next_pid += 1

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        pass


class TestDeadWorkerErrorReporting:
    """Verify that dead workers produce error messages with game IDs."""

    @pytest.mark.asyncio
    async def test_dead_worker_emits_error_with_game_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When a worker dies without producing a result, an error identifying
        the game ID is emitted to stdout."""
        _DeadFakeProcess.captured_tasks = []
        _DeadFakeProcess.next_pid = 9000

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _DeadFakeProcess)
        # Use a tiny timeout so the test doesn't wait 300 s
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().split("\n") if line]

        # There should be at least two error messages:
        # 1) per-game error with game ID
        # 2) "All workers died unexpectedly"
        error_msgs = [json.loads(line) for line in lines if '"type": "error"' in line]
        assert len(error_msgs) >= 2

        # First error should identify the game with game ID and PID
        per_game_error = error_msgs[0]
        assert per_game_error["type"] == "error"
        assert "died unexpectedly" in per_game_error["message"]
        assert "pid=" in per_game_error["message"]

        # The game ID in the error should match the captured task
        captured_game_id = _DeadFakeProcess.captured_tasks[0].game_id
        assert captured_game_id in per_game_error["message"]

        # Last error should be the "all workers died" message
        all_dead_error = error_msgs[-1]
        assert "All workers died unexpectedly" in all_dead_error["message"]

    @pytest.mark.asyncio
    async def test_dead_worker_errors_are_counted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Dead-worker games are detected and logged.

        We combine a dead worker (first) with a healthy worker (second)
        that delivers a normal result, triggering a progress message.
        The dead worker should produce a warning log identifying its game.
        """

        class _FirstDeadThenAliveFakeProcess:
            """First worker dies; subsequent workers succeed immediately."""

            captured_tasks: ClassVar[list[WorkerTask]] = []
            call_count: ClassVar[int] = 0
            next_pid: ClassVar[int] = 8000

            def __init__(
                self,
                *,
                target: object = None,
                args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
            ) -> None:
                task, queue = args
                _FirstDeadThenAliveFakeProcess.captured_tasks.append(task)
                self._pid = _FirstDeadThenAliveFakeProcess.next_pid
                _FirstDeadThenAliveFakeProcess.next_pid += 1
                call = _FirstDeadThenAliveFakeProcess.call_count
                _FirstDeadThenAliveFakeProcess.call_count += 1

                if call == 0:
                    # First worker dies without putting a result
                    self._alive = False
                else:
                    # Subsequent workers succeed immediately
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

            @property
            def pid(self) -> int:
                return self._pid

            def start(self) -> None:
                pass

            def is_alive(self) -> bool:
                return self._alive

            def join(self, timeout: float | None = None) -> None:
                pass

            def terminate(self) -> None:
                pass

        _FirstDeadThenAliveFakeProcess.captured_tasks = []
        _FirstDeadThenAliveFakeProcess.call_count = 0
        _FirstDeadThenAliveFakeProcess.next_pid = 8000

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr(
            "sprt_runner.runner.multiprocessing.Process", _FirstDeadThenAliveFakeProcess
        )
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
            concurrency=2,
        )

        with caplog.at_level(logging.WARNING, logger="sprt_runner.runner"):
            await run_sprt(config)

        # The dead worker's game ID should appear in a warning log
        dead_game_id = _FirstDeadThenAliveFakeProcess.captured_tasks[0].game_id
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(dead_game_id in msg for msg in warning_msgs), (
            f"Expected warning containing game ID {dead_game_id}, got: {warning_msgs}"
        )


# ---------------------------------------------------------------------------
# Queue exception handling tests
# ---------------------------------------------------------------------------


class _QueueErrorFakeProcess:
    """Fake process whose queue raises a non-Empty exception.

    The queue injected into the constructor is replaced with one that raises
    the configured exception on ``get()``.
    """

    next_pid: ClassVar[int] = 11000
    exception_to_raise: ClassVar[type[BaseException]] = EOFError

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        _task, real_queue = args
        self._pid = _QueueErrorFakeProcess.next_pid
        _QueueErrorFakeProcess.next_pid += 1

        # Poison the shared queue so that the *next* get() raises the
        # configured exception instead of returning a result.
        _poison_queue(real_queue, _QueueErrorFakeProcess.exception_to_raise)

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        pass


def _poison_queue(
    q: multiprocessing.Queue[WorkerResult],
    exc_type: type[BaseException],
) -> None:
    """Replace ``q.get`` with a callable that raises *exc_type*."""

    def _raise(timeout: float | None = None) -> WorkerResult:
        raise exc_type("simulated queue corruption")

    q.get = _raise  # type: ignore[assignment]


class TestQueueExceptionHandling:
    """Verify that only queue.Empty triggers the timeout path.

    Other exceptions must be logged and cause the SPRT run to abort
    rather than being silently swallowed.
    """

    @pytest.mark.asyncio
    async def test_queue_empty_triggers_dead_worker_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """queue.Empty from a timed-out get() should enter the
        dead-worker detection path and not abort immediately."""
        _DeadFakeProcess.captured_tasks = []
        _DeadFakeProcess.next_pid = 9000

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _DeadFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().split("\n") if line]
        error_msgs = [json.loads(line) for line in lines if '"type": "error"' in line]

        # Dead-worker path should have produced error messages (not a fatal abort)
        assert any("died unexpectedly" in m["message"] for m in error_msgs)
        # The fatal "Fatal error reading result queue" should NOT appear
        assert not any("Fatal error reading result queue" in m["message"] for m in error_msgs)

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_not_swallowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Non-Empty exceptions (e.g. EOFError) must not be silently
        swallowed.  They should be logged and cause the run to abort."""
        _QueueErrorFakeProcess.next_pid = 11000
        _QueueErrorFakeProcess.exception_to_raise = EOFError

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _QueueErrorFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().split("\n") if line]
        error_msgs = [json.loads(line) for line in lines if '"type": "error"' in line]

        # Should see the fatal error message, NOT the dead-worker message
        assert any("Fatal error reading result queue" in m["message"] for m in error_msgs)
        # Should NOT see "died unexpectedly" — that's the queue.Empty path
        assert not any("died unexpectedly" in m["message"] for m in error_msgs)

    @pytest.mark.asyncio
    async def test_oserror_is_not_swallowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """OSError on queue.get() must also be treated as fatal."""
        _QueueErrorFakeProcess.next_pid = 11000
        _QueueErrorFakeProcess.exception_to_raise = OSError

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _QueueErrorFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().split("\n") if line]
        error_msgs = [json.loads(line) for line in lines if '"type": "error"' in line]

        assert any("Fatal error reading result queue" in m["message"] for m in error_msgs)


class TestRunConfigKeepWorktrees:
    """Tests for RunConfig keep_worktrees field."""

    def test_keep_worktrees_defaults_false(self) -> None:
        config = RunConfig(
            base="random-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
        )
        assert config.keep_worktrees is False

    def test_keep_worktrees_set_true(self) -> None:
        config = RunConfig(
            base="random-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            keep_worktrees=True,
        )
        assert config.keep_worktrees is True


class TestKeepWorktreesCLI:
    """Tests for --keep-worktrees CLI argument."""

    def test_keep_worktrees_flag_absent(self) -> None:
        """Without --keep-worktrees, the flag should default to False."""
        parser = build_parser()
        args = parser.parse_args(["run", "--base", "eng1", "--test", "eng2", "--tc", "depth=1"])
        assert args.keep_worktrees is False

    def test_keep_worktrees_flag_present(self) -> None:
        """With --keep-worktrees, the flag should be True."""
        parser = build_parser()
        args = parser.parse_args(
            ["run", "--base", "eng1", "--test", "eng2", "--tc", "depth=1", "--keep-worktrees"]
        )
        assert args.keep_worktrees is True


class TestWorktreeCleanupAfterSprt:
    """Tests verifying worktree cleanup after run_sprt() completes."""

    @pytest.mark.asyncio
    async def test_cleanup_called_after_run_sprt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Worktree cleanup should be called after run_sprt() completes."""
        _FakeProcess.captured_tasks = []

        created_worktrees: list[Path] = [Path("/fake/worktree/abc123")]

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        def _mock_collect(base: object, test: object, repo_root: Path) -> list[Path]:
            return created_worktrees

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcess)
        monkeypatch.setattr("sprt_runner.runner._collect_worktree_paths", _mock_collect)

        cleanup_calls: list[Path] = []

        async def _mock_cleanup(path: Path, *, repo_root: Path) -> None:
            cleanup_calls.append(path)

        monkeypatch.setattr("sprt_runner.runner.cleanup_worktree", _mock_cleanup)

        config = RunConfig(
            base="base-engine",
            test="test-engine:abc123",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        assert cleanup_calls == [Path("/fake/worktree/abc123")]

    @pytest.mark.asyncio
    async def test_keep_worktrees_skips_cleanup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With keep_worktrees=True, cleanup should not be called."""
        _FakeProcess.captured_tasks = []

        created_worktrees: list[Path] = [Path("/fake/worktree/abc123")]

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        def _mock_collect(base: object, test: object, repo_root: Path) -> list[Path]:
            return created_worktrees

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcess)
        monkeypatch.setattr("sprt_runner.runner._collect_worktree_paths", _mock_collect)

        cleanup_calls: list[Path] = []

        async def _mock_cleanup(path: Path, *, repo_root: Path) -> None:
            cleanup_calls.append(path)

        monkeypatch.setattr("sprt_runner.runner.cleanup_worktree", _mock_cleanup)

        config = RunConfig(
            base="base-engine",
            test="test-engine:abc123",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
            keep_worktrees=True,
        )

        await run_sprt(config)

        assert cleanup_calls == []

    @pytest.mark.asyncio
    async def test_cleanup_called_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Worktree cleanup should be called even when run_sprt() exits due to error."""
        created_worktrees: list[Path] = [Path("/fake/worktree/abc123")]

        async def _failing_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            raise RuntimeError("engine resolution failed")

        def _mock_collect(base: object, test: object, repo_root: Path) -> list[Path]:
            return created_worktrees

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _failing_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner._collect_worktree_paths", _mock_collect)

        cleanup_calls: list[Path] = []

        async def _mock_cleanup(path: Path, *, repo_root: Path) -> None:
            cleanup_calls.append(path)

        monkeypatch.setattr("sprt_runner.runner.cleanup_worktree", _mock_cleanup)

        config = RunConfig(
            base="base-engine",
            test="test-engine:abc123",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
        )

        await run_sprt(config)

        assert cleanup_calls == [Path("/fake/worktree/abc123")]


# ---------------------------------------------------------------------------
# SIGKILL fallback tests
# ---------------------------------------------------------------------------


class _StubbornFakeProcess:
    """Fake process that ignores SIGTERM (stays alive after terminate+join).

    Puts a result on the queue so SPRT can converge, but ``is_alive()``
    remains ``True`` after ``terminate()`` until ``kill()`` is called.
    """

    captured_tasks: ClassVar[list[WorkerTask]] = []
    next_pid: ClassVar[int] = 11000
    kill_called: ClassVar[list[int]] = []

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        task, queue = args
        _StubbornFakeProcess.captured_tasks.append(task)
        queue.put(
            WorkerResult(
                game_id=task.game_id,
                result=GameResult.WHITE_WIN,
                termination="checkmate",
                move_count=20,
                swap_colors=task.swap_colors,
            )
        )
        self._alive = True
        self._pid = _StubbornFakeProcess.next_pid
        _StubbornFakeProcess.next_pid += 1

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        # Intentionally does NOT set self._alive = False
        pass

    def kill(self) -> None:
        _StubbornFakeProcess.kill_called.append(self._pid)
        self._alive = False


class TestSigkillFallback:
    """Verify SIGKILL fallback when workers ignore SIGTERM."""

    @pytest.mark.asyncio
    async def test_kill_called_when_worker_ignores_sigterm(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When a worker stays alive after terminate()+join(), kill() is called
        and a warning is logged."""
        _StubbornFakeProcess.captured_tasks = []
        _StubbornFakeProcess.next_pid = 11000
        _StubbornFakeProcess.kill_called = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _StubbornFakeProcess)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
            concurrency=10,
        )

        with caplog.at_level(logging.WARNING, logger="sprt_runner.runner"):
            await run_sprt(config)

        # kill() must have been called on at least one worker
        assert len(_StubbornFakeProcess.kill_called) > 0

        # A warning must have been logged for each force-killed worker
        warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("SIGKILL" in msg for msg in warning_msgs), (
            f"Expected warning containing 'SIGKILL', got: {warning_msgs}"
        )

    @pytest.mark.asyncio
    async def test_kill_not_called_when_worker_exits_promptly(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a worker exits promptly after terminate(), kill() is NOT called."""
        kill_called: list[int] = []

        class _PromptFakeProcess(_FakeProcess):
            """_FakeProcess with kill() tracking."""

            next_pid: ClassVar[int] = 7000

            def __init__(
                self,
                *,
                target: object = None,
                args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
            ) -> None:
                task, queue = args
                _PromptFakeProcess.captured_tasks.append(task)
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
                self._pid = _PromptFakeProcess.next_pid
                _PromptFakeProcess.next_pid += 1

            def kill(self) -> None:
                kill_called.append(self._pid)

        _PromptFakeProcess.captured_tasks = []
        _PromptFakeProcess.next_pid = 7000

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _PromptFakeProcess)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        # kill() must NOT have been called — workers exit promptly
        assert kill_called == []


# ---------------------------------------------------------------------------
# format_interrupted_message tests
# ---------------------------------------------------------------------------


class TestFormatInterruptedMessage:
    """Tests for the format_interrupted_message JSON-lines formatter."""

    def test_basic_interrupted_message(self) -> None:
        """Interrupted message contains type and games_played."""
        msg = format_interrupted_message(games_played=5)
        parsed = json.loads(msg)
        assert parsed["type"] == "interrupted"
        assert parsed["games_played"] == 5

    def test_zero_games_played(self) -> None:
        """Interrupted with zero games played."""
        msg = format_interrupted_message(games_played=0)
        parsed = json.loads(msg)
        assert parsed["type"] == "interrupted"
        assert parsed["games_played"] == 0

    def test_is_valid_json(self) -> None:
        """Interrupted message is valid single-line JSON."""
        msg = format_interrupted_message(games_played=10)
        assert "\n" not in msg
        json.loads(msg)


# ---------------------------------------------------------------------------
# _terminate_all_workers tests
# ---------------------------------------------------------------------------


class TestTerminateAllWorkers:
    """Tests for the _terminate_all_workers helper."""

    def test_terminates_and_joins_all(self) -> None:
        """All workers are terminated and joined."""
        workers: list[multiprocessing.Process] = []
        for _ in range(3):
            w = MagicMock(spec=multiprocessing.Process)
            w.is_alive.return_value = False
            workers.append(w)

        _terminate_all_workers(workers)

        for w in workers:
            w.terminate.assert_called_once()  # type: ignore[union-attr]
            w.join.assert_called_once_with(timeout=5)  # type: ignore[union-attr]
            w.kill.assert_not_called()  # type: ignore[union-attr]

    def test_kills_stubborn_worker(self) -> None:
        """Workers that ignore SIGTERM get SIGKILL."""
        w = MagicMock(spec=multiprocessing.Process)
        w.pid = 42
        w.is_alive.return_value = True  # stays alive after terminate

        _terminate_all_workers([w])

        w.terminate.assert_called_once()  # type: ignore[union-attr]
        w.kill.assert_called_once()  # type: ignore[union-attr]
        # join called twice: once after terminate, once after kill
        assert w.join.call_count == 2  # type: ignore[union-attr]

    def test_empty_list_is_noop(self) -> None:
        """Terminating an empty list does nothing."""
        _terminate_all_workers([])  # Should not raise


# ---------------------------------------------------------------------------
# Graceful shutdown tests
# ---------------------------------------------------------------------------


class _InterruptibleFakeProcess:
    """Fake process: delivers first N results, then stays alive for interruption.

    Tracks terminate() and kill() calls for verification.
    """

    captured_tasks: ClassVar[list[WorkerTask]] = []
    call_count: ClassVar[int] = 0
    interrupt_after: ClassVar[int] = 2
    terminate_called: ClassVar[list[int]] = []
    kill_called: ClassVar[list[int]] = []
    next_pid: ClassVar[int] = 12000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        task, queue = args
        _InterruptibleFakeProcess.captured_tasks.append(task)
        call = _InterruptibleFakeProcess.call_count
        _InterruptibleFakeProcess.call_count += 1

        self._pid = _InterruptibleFakeProcess.next_pid
        _InterruptibleFakeProcess.next_pid += 1

        if call < _InterruptibleFakeProcess.interrupt_after:
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
        else:
            # Don't put result — simulate an ongoing game
            self._alive = True

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        pass

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        _InterruptibleFakeProcess.terminate_called.append(self._pid)
        self._alive = False

    def kill(self) -> None:
        _InterruptibleFakeProcess.kill_called.append(self._pid)
        self._alive = False


class TestGracefulShutdown:
    """Verify graceful shutdown on SIGINT / CancelledError."""

    @staticmethod
    def _reset_interruptible() -> None:
        _InterruptibleFakeProcess.captured_tasks = []
        _InterruptibleFakeProcess.call_count = 0
        _InterruptibleFakeProcess.interrupt_after = 2
        _InterruptibleFakeProcess.terminate_called = []
        _InterruptibleFakeProcess.kill_called = []
        _InterruptibleFakeProcess.next_pid = 12000

    @pytest.mark.asyncio
    async def test_cancel_terminates_remaining_workers(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelling run_sprt() terminates workers that are still running."""
        self._reset_interruptible()

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _InterruptibleFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 1.0)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            book_path=None,
        )

        task = asyncio.create_task(run_sprt(config))
        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # The third worker (pid=12002) should have been terminated
        assert 12002 in _InterruptibleFakeProcess.terminate_called

    @pytest.mark.asyncio
    async def test_partial_progress_on_cancel(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Partial progress is output before the interrupted message on cancel."""
        self._reset_interruptible()

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _InterruptibleFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 1.0)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            book_path=None,
        )

        task = asyncio.create_task(run_sprt(config))
        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        captured = capsys.readouterr().out
        lines = [line.strip() for line in captured.strip().split("\n") if line.strip()]

        # Find the interrupted message — it should be the last line
        interrupted_msgs = [json.loads(ln) for ln in lines if '"type": "interrupted"' in ln]
        assert len(interrupted_msgs) == 1
        assert interrupted_msgs[0]["games_played"] == 2

        # A progress message should appear right before the interrupted message
        # showing partial W/D/L/LLR
        progress_msgs = [json.loads(ln) for ln in lines if '"type": "progress"' in ln]
        assert len(progress_msgs) >= 1
        last_progress = progress_msgs[-1]
        assert last_progress["games_total"] == 2
        assert "llr" in last_progress
        assert "wins" in last_progress
        assert "losses" in last_progress

    @pytest.mark.asyncio
    async def test_cancel_with_zero_games_outputs_interrupted_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """If cancelled before any games complete, only interrupted message is output."""
        self._reset_interruptible()
        _InterruptibleFakeProcess.interrupt_after = 0  # All workers stay alive

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _InterruptibleFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 1.0)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
            book_path=None,
        )

        task = asyncio.create_task(run_sprt(config))
        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        captured = capsys.readouterr().out
        lines = [line.strip() for line in captured.strip().split("\n") if line.strip()]

        # Only the interrupted message (no progress since zero games)
        interrupted_msgs = [json.loads(ln) for ln in lines if '"type": "interrupted"' in ln]
        assert len(interrupted_msgs) == 1
        assert interrupted_msgs[0]["games_played"] == 0

        # No progress messages (no games completed)
        progress_in_interrupted = [ln for ln in lines if '"type": "progress"' in ln]
        assert len(progress_in_interrupted) == 0
