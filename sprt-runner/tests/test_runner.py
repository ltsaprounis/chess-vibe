"""Tests for the SPRT runner CLI entry point and orchestration."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from shared.storage.models import GameResult
from shared.time_control import DepthTimeControl, FixedTimeControl, parse_time_control
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig
from sprt_runner.runner import (
    RunConfig,
    WorkerResult,
    WorkerTask,
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


class _FakeProcess:
    """Fake multiprocessing.Process that simulates the race condition.

    After ``start()``, ``is_alive()`` returns ``True`` (the process has
    put its result on the queue but has not yet exited).  Only after
    ``join()`` does ``is_alive()`` return ``False``.

    This reproduces the timing window fixed by calling ``join()``
    before ``is_alive()`` in ``run_sprt()``'s worker cleanup loop.
    """

    def __init__(self, *, target: Any, args: Any) -> None:
        self._task: WorkerTask = args[0]
        self._queue: multiprocessing.Queue[WorkerResult] = args[1]
        self._alive = True

    def start(self) -> None:
        self._queue.put(
            WorkerResult(
                game_id=self._task.game_id,
                result=GameResult.WHITE_WIN,
                termination="checkmate",
                move_count=10,
                swap_colors=self._task.swap_colors,
            )
        )
        # Simulate race: process is still alive after put()
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self._alive = False

    def terminate(self) -> None:
        self._alive = False


class TestRunSprt:
    """Tests for the run_sprt orchestration loop."""

    @pytest.mark.asyncio
    async def test_terminates_with_decision(self, capsys: pytest.CaptureFixture[str]) -> None:
        """run_sprt completes an SPRT test and outputs a decision."""
        config = RunConfig(
            base="random-engine",
            test="random-engine",
            time_control=DepthTimeControl(depth=1),
            elo0=-500.0,
            elo1=500.0,
        )

        with (
            patch(
                "sprt_runner.runner.resolve_engine_path",
                new_callable=AsyncMock,
                return_value=("fake_cmd", Path("/fake")),
            ),
            patch(
                "sprt_runner.runner._resolve_run_command",
                return_value="fake_resolved_cmd",
            ),
            patch("sprt_runner.runner.multiprocessing.Process", _FakeProcess),
        ):
            await run_sprt(config)

        captured = capsys.readouterr()
        lines = [json.loads(line) for line in captured.out.strip().split("\n")]

        types = [msg["type"] for msg in lines]
        assert "game_result" in types
        assert "progress" in types
        assert "complete" in types

        complete = next(msg for msg in lines if msg["type"] == "complete")
        assert complete["result"] in ("H0", "H1")
        assert complete["total_games"] >= 1

    @pytest.mark.asyncio
    async def test_worker_cleanup_handles_race_condition(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Workers that are still alive after put() are cleaned up by join().

        Without the join-before-is_alive fix, the loop would stall because
        the worker stays in active_workers and no new worker is launched.
        With wide SPRT bounds a single decisive result triggers termination,
        so the test completes quickly only if the race condition is handled.
        """
        config = RunConfig(
            base="random-engine",
            test="random-engine",
            time_control=DepthTimeControl(depth=1),
            elo0=-500.0,
            elo1=500.0,
        )

        with (
            patch(
                "sprt_runner.runner.resolve_engine_path",
                new_callable=AsyncMock,
                return_value=("fake_cmd", Path("/fake")),
            ),
            patch(
                "sprt_runner.runner._resolve_run_command",
                return_value="fake_resolved_cmd",
            ),
            patch("sprt_runner.runner.multiprocessing.Process", _FakeProcess),
        ):
            await run_sprt(config)

        captured = capsys.readouterr()
        lines = [json.loads(line) for line in captured.out.strip().split("\n")]
        complete = next(msg for msg in lines if msg["type"] == "complete")
        # With the fix, SPRT terminates quickly (no 300 s stall per game)
        assert complete["total_games"] <= 5
