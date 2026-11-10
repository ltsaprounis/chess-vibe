"""Tests for the SPRT runner CLI entry point and orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import re
import shlex
import signal
import time
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from shared.storage.models import GameResult, Move
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    parse_time_control,
)
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import DEFAULT_MOVE_TIMEOUT_MS, DEFAULT_TIME_MARGIN_MS, GameConfig
from sprt_runner.openings import OpeningPair
from sprt_runner.runner import (
    RunConfig,
    WorkerResult,
    WorkerTask,
    _cleanup_workers,  # type: ignore[reportPrivateUsage]
    _drain_results,  # type: ignore[reportPrivateUsage]
    _resolve_run_command,  # type: ignore[reportPrivateUsage]
    _terminate_all_workers,  # type: ignore[reportPrivateUsage]
    build_parser,
    format_complete_message,
    format_error_message,
    format_game_result_message,
    format_interrupted_message,
    format_progress_message,
    main,
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

    def test_result_carries_moves(self) -> None:
        moves = [
            Move(
                uci="e2e4",
                san="e4",
                fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                score_cp=30,
                depth=10,
            ),
        ]
        result = WorkerResult(
            game_id="game-3",
            result=GameResult.DRAW,
            termination="draw_rule",
            move_count=1,
            swap_colors=False,
            moves=moves,
            start_fen=None,
        )
        assert len(result.moves) == 1
        assert result.moves[0].uci == "e2e4"

    def test_result_carries_start_fen(self) -> None:
        fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        result = WorkerResult(
            game_id="game-4",
            result=GameResult.WHITE_WIN,
            termination="checkmate",
            move_count=0,
            swap_colors=False,
            moves=[],
            start_fen=fen,
        )
        assert result.start_fen == fen


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

    def test_worker_restores_default_signal_handling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A worker resets the signal handlers a fork copies from the coordinator.

        Forked from the coordinator, a worker starts with asyncio's SIGTERM
        handler, which makes it ignore ``terminate()``, and a SIGINT handler
        that belongs to the coordinator's event loop. This installs both the
        way a fork leaves them and checks what the worker sees while it plays:
        SIGTERM at its default, and SIGINT handled by the worker's own
        ``asyncio.run``, which puts Python's handler back when it ends.
        """
        seen: dict[str, object] = {}

        async def _record(task: WorkerTask) -> WorkerResult:
            seen["sigterm"] = signal.getsignal(signal.SIGTERM)
            seen["sigint"] = signal.getsignal(signal.SIGINT)
            return WorkerResult(
                game_id=task.game_id,
                result=GameResult.DRAW,
                termination="stalemate",
                move_count=0,
                swap_colors=task.swap_colors,
            )

        def _inherited_handler(signum: int, frame: object) -> None:
            pass

        monkeypatch.setattr("sprt_runner.runner._play_single_game", _record)
        task = WorkerTask(
            game_id="signals",
            white_cmd="engine",
            black_cmd="engine",
            game_config=GameConfig(time_control=DepthTimeControl(depth=1)),
            swap_colors=False,
        )
        result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()
        old_sigterm = signal.signal(signal.SIGTERM, _inherited_handler)
        old_sigint = signal.signal(signal.SIGINT, _inherited_handler)
        try:
            worker_entry(task, result_queue)
            seen["sigint_after"] = signal.getsignal(signal.SIGINT)
            assert result_queue.get(timeout=5).game_id == "signals"
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)
            result_queue.close()
            result_queue.join_thread()

        assert seen["sigterm"] is signal.SIG_DFL
        assert seen["sigint"] is not _inherited_handler
        assert seen["sigint"] is not signal.default_int_handler
        assert seen["sigint_after"] is signal.default_int_handler


class TestWorkerLogging:
    """A worker sets up logging like the runner, since forkserver and spawn don't pass it on."""

    def test_worker_logs_info_to_stderr_in_the_runner_format(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A worker that starts with no logging setup logs INFO, not DEBUG, to stderr.

        Clearing the root logger's handlers recreates what forkserver and
        spawn workers start with; pytest's own handlers would otherwise make
        the setup a no-op. The game logs one INFO and one DEBUG record, as the
        UCI client does.
        """

        async def _play(task: WorkerTask) -> WorkerResult:
            uci_logger = logging.getLogger("shared.uci_client")
            uci_logger.info("Started engine process: engine (pid=1)")
            uci_logger.debug("debug detail")
            return WorkerResult(
                game_id=task.game_id,
                result=GameResult.DRAW,
                termination="stalemate",
                move_count=0,
                swap_colors=task.swap_colors,
            )

        monkeypatch.setattr("sprt_runner.runner._play_single_game", _play)
        monkeypatch.setattr(logging.root, "handlers", [])
        task = WorkerTask(
            game_id="logs",
            white_cmd="engine",
            black_cmd="engine",
            game_config=GameConfig(time_control=DepthTimeControl(depth=1)),
            swap_colors=False,
        )
        result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()
        old_level = logging.root.level
        old_sigterm = signal.getsignal(signal.SIGTERM)
        old_sigint = signal.getsignal(signal.SIGINT)
        try:
            worker_entry(task, result_queue)
            assert result_queue.get(timeout=5).game_id == "logs"
        finally:
            logging.root.setLevel(old_level)
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)
            result_queue.close()
            result_queue.join_thread()

        captured = capsys.readouterr()
        assert captured.out == ""
        lines = captured.err.splitlines()
        assert len(lines) == 1, lines
        assert re.fullmatch(
            r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO shared\.uci_client: "
            r"Started engine process: engine \(pid=1\)",
            lines[0],
        ), lines[0]


class TestCleanupWorkers:
    """Tests for _cleanup_workers, which reaps workers without ever waiting on one.

    A worker that has delivered its result may still be shutting down, and
    one still playing can take minutes. Joining either with a timeout made
    the coordinator wait about a second per running worker after every
    result (#187), so only workers that have already exited are joined.
    """

    def test_running_worker_is_kept_without_joining(self) -> None:
        """A worker that hasn't exited is kept and never joined."""
        worker = MagicMock(spec=multiprocessing.Process)
        worker.is_alive.return_value = True

        result = _cleanup_workers([worker])

        worker.join.assert_not_called()
        assert result == [worker]

    def test_exited_worker_is_joined_and_dropped(self) -> None:
        """A worker that has exited is joined, which reaps it, and dropped."""
        worker = MagicMock(spec=multiprocessing.Process)
        worker.is_alive.return_value = False

        result = _cleanup_workers([worker])

        assert worker.join.call_count == 1
        assert result == []

    def test_mixed_workers_keep_only_running_ones(self) -> None:
        """Only exited workers are joined; running ones are kept in order."""
        running_a = MagicMock(spec=multiprocessing.Process)
        running_a.is_alive.return_value = True
        exited = MagicMock(spec=multiprocessing.Process)
        exited.is_alive.return_value = False
        running_b = MagicMock(spec=multiprocessing.Process)
        running_b.is_alive.return_value = True

        result = _cleanup_workers([running_a, exited, running_b])

        assert result == [running_a, running_b]
        running_a.join.assert_not_called()
        running_b.join.assert_not_called()
        assert exited.join.call_count == 1

    def test_empty_worker_list(self) -> None:
        """Cleanup with no workers returns empty list."""
        result = _cleanup_workers([])
        assert result == []


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


class _FakeProcessWithMoves:
    """Fake process that includes moves in WorkerResult for game persistence tests."""

    captured_tasks: ClassVar[list[WorkerTask]] = []
    _next_pid: ClassVar[int] = 9000

    _test_moves: ClassVar[list[Move]] = [
        Move(
            uci="e2e4",
            san="e4",
            fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            score_cp=30,
            depth=10,
        ),
    ]

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        task, q = args
        _FakeProcessWithMoves.captured_tasks.append(task)
        q.put(
            WorkerResult(
                game_id=task.game_id,
                result=GameResult.WHITE_WIN,
                termination="checkmate",
                move_count=1,
                swap_colors=task.swap_colors,
                moves=list(_FakeProcessWithMoves._test_moves),
                start_fen=task.game_config.start_fen,
            )
        )
        self._pid = _FakeProcessWithMoves._next_pid
        _FakeProcessWithMoves._next_pid += 1

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


class TestGamePersistence:
    """Tests verifying games are saved to the output directory during SPRT runs."""

    @pytest.mark.asyncio
    async def test_games_saved_when_output_dir_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Games should be persisted as .eval.json + .pgn when output_dir is set."""
        _FakeProcessWithMoves.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcessWithMoves)

        games_dir = tmp_path / "games"
        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
            output_dir=games_dir,
        )

        await run_sprt(config)

        # Verify game files were created
        assert games_dir.is_dir()

        eval_files = list(games_dir.glob("*.eval.json"))
        pgn_files = list(games_dir.glob("*.pgn"))
        assert len(eval_files) >= 1, "At least one .eval.json should be saved"
        assert len(pgn_files) >= 1, "At least one .pgn should be saved"

    @pytest.mark.asyncio
    async def test_no_games_saved_when_output_dir_is_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Games should NOT be persisted when output_dir is not set."""
        _FakeProcessWithMoves.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcessWithMoves)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await run_sprt(config)

        # No data directory should be created
        assert not (tmp_path / "games").exists()

    @pytest.mark.asyncio
    async def test_sidecar_records_both_clocks_of_every_move(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Clock values reach the .eval.json unchanged, each under its own colour."""
        _FakeProcessWithMoves.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcessWithMoves)
        monkeypatch.setattr(
            _FakeProcessWithMoves,
            "_test_moves",
            [
                Move(
                    uci="e2e4",
                    san="e4",
                    fen_after="fen 1",
                    clock_white_ms=59_500,
                    clock_black_ms=60_000,
                ),
                Move(
                    uci="e7e5",
                    san="e5",
                    fen_after="fen 2",
                    clock_white_ms=59_500,
                    clock_black_ms=59_250,
                ),
            ],
        )

        games_dir = tmp_path / "games"
        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=IncrementTimeControl(wtime_ms=60_000, btime_ms=60_000),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
            output_dir=games_dir,
        )

        await run_sprt(config)

        sidecars = list(games_dir.glob("*.eval.json"))
        assert len(sidecars) >= 1
        for sidecar in sidecars:
            moves = json.loads(sidecar.read_text())["moves"]
            assert [(move["clock_white_ms"], move["clock_black_ms"]) for move in moves] == [
                (59_500, 60_000),
                (59_500, 59_250),
            ]


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
        until the queue timeout expired.  With the executor wrapper the
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
        # Shorten the 0.5 s poll so the death is found quickly
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await _run_within(config, 5.0, monkeypatch)

        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().split("\n") if line]

        # There should be at least two error messages:
        # 1) per-game error with game ID
        # 2) the fatal stop after a full round of deaths
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

        # Last error should be the fatal stop
        assert error_msgs[-1]["message"] == "Aborting after 1 worker death in a row with no result"

    @pytest.mark.asyncio
    async def test_dead_worker_errors_are_counted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A worker that dies among successful games is reported and counted.

        The first and fourth workers die without a result; the rest finish
        normally. Each death gets an ``error`` line naming its game and
        raises ``errors`` in ``progress``. A death with successes on either
        side of it isn't fatal, so the run still reaches a decision. The
        queue timeout isn't shortened.
        """

        class _SomeDieFakeProcess:
            """Workers 0 and 3 die without a result; the others deliver and linger."""

            dying_calls: ClassVar[frozenset[int]] = frozenset({0, 3})
            captured_tasks: ClassVar[list[WorkerTask]] = []
            instances: ClassVar[list[_SomeDieFakeProcess]] = []
            call_count: ClassVar[int] = 0
            next_pid: ClassVar[int] = 8000

            def __init__(
                self,
                *,
                target: object = None,
                args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
            ) -> None:
                self._task, self._queue = args
                _SomeDieFakeProcess.captured_tasks.append(self._task)
                self._dies = _SomeDieFakeProcess.call_count in _SomeDieFakeProcess.dying_calls
                _SomeDieFakeProcess.call_count += 1
                self._pid = _SomeDieFakeProcess.next_pid
                _SomeDieFakeProcess.next_pid += 1
                self._alive = True
                self.joined = False
                _SomeDieFakeProcess.instances.append(self)

            @property
            def pid(self) -> int:
                return self._pid

            def start(self) -> None:
                if self._dies:
                    self._alive = False
                else:
                    self._queue.put(_white_win(self._task))

            def is_alive(self) -> bool:
                return self._alive

            def join(self, timeout: float | None = None) -> None:
                if not self._alive:
                    self.joined = True

            def terminate(self) -> None:
                self._alive = False

        _patch_engines(monkeypatch, _SomeDieFakeProcess)

        await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        dead_ids = [_SomeDieFakeProcess.captured_tasks[i].game_id for i in (0, 3)]
        for dead_id in dead_ids:
            assert any(dead_id in e and "died unexpectedly" in e for e in errors), errors
        assert not any(e.startswith("Aborting") for e in errors)
        assert [m["errors"] for m in messages if m["type"] == "progress"][-1] == 2
        assert len([m for m in messages if m["type"] == "complete"]) == 1
        # Dead workers are joined, like every other worker the run is done with.
        assert all(_SomeDieFakeProcess.instances[i].joined for i in (0, 3))

    @pytest.mark.asyncio
    async def test_a_full_round_of_deaths_is_fatal_within_a_poll(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Both workers dying with no result between them ends the run.

        The queue timeout isn't shortened: both deaths are found on the
        first 0.5 s poll, so the run ends well within 2 s, and no worker is
        launched after them.
        """
        _DeadFakeProcess.captured_tasks = []
        _DeadFakeProcess.next_pid = 9000
        _patch_engines(monkeypatch, _DeadFakeProcess)

        await _run_within(_quick_config(concurrency=2), 2.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        tasks = _DeadFakeProcess.captured_tasks
        assert len(tasks) == 2
        assert errors == [
            f"Worker for game {tasks[0].game_id} died unexpectedly (pid=9000)",
            f"Worker for game {tasks[1].game_id} died unexpectedly (pid=9001)",
            "Aborting after 2 worker deaths in a row with no result",
        ]
        assert [m for m in messages if m["type"] == "game_result"] == []

    @pytest.mark.asyncio
    async def test_deaths_on_separate_polls_are_fatal_while_another_worker_plays(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """One slot dies twice in a row, a poll apart, while the other plays on.

        Two deaths with no result in between are a full round at two workers,
        so the run ends. The worker still playing is terminated.
        """
        _ScriptedFakeProcess.reset(["play_forever", "die", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert len(_ScriptedFakeProcess.instances) == 3
        assert errors[-1] == "Aborting after 2 worker deaths in a row with no result"
        assert len([e for e in errors if "died unexpectedly" in e]) == 2
        assert not _ScriptedFakeProcess.instances[0].is_alive()

    @pytest.mark.asyncio
    async def test_a_result_drained_with_deaths_clears_the_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A result read while looking for dead workers counts as a delivered result.

        At three workers, two die on the first poll. On the second, the first
        worker delivers and exits just as the coordinator checks, while two
        more die. Since that result, there have been two deaths, not four, so
        the run goes on. A spy checks that the result really arrived through
        the second check's drain, which the test depends on.
        """
        drains: list[list[str]] = []

        def _spy(result_queue: multiprocessing.Queue[WorkerResult]) -> list[WorkerResult]:
            drained = _drain_results(result_queue)
            drains.append([r.game_id for r in drained])
            return drained

        monkeypatch.setattr("sprt_runner.runner._drain_results", _spy)
        _ScriptedFakeProcess.reset(["deliver_on_second_check", "die", "die", "die", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 5.0, monkeypatch)

        first = _ScriptedFakeProcess.instances[0].task.game_id
        assert drains[:2] == [[], [first]]
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert not any(e.startswith("Aborting") for e in errors)
        assert len([e for e in errors if "died unexpectedly" in e]) == 4
        assert first in [m["game_id"] for m in messages if m["type"] == "game_result"]
        assert len([m for m in messages if m["type"] == "complete"]) == 1

    @pytest.mark.asyncio
    async def test_a_delivered_result_clears_the_whole_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Two deaths, a failed game, then two more deaths isn't a full round of three.

        Two slots play on; the third has the deaths and the failed game.
        """
        _ScriptedFakeProcess.reset(
            ["play_forever", "die", "die", "error", "play_forever", "die", "die"]
        )
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert not any(e.startswith("Aborting") for e in errors)
        assert len([e for e in errors if "died unexpectedly" in e]) == 4
        assert len([m for m in messages if m["type"] == "complete"]) == 1

    @pytest.mark.asyncio
    async def test_the_fatal_message_counts_every_death_in_the_round(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Two deaths, then two more on the next poll, stop a three-worker run at four."""
        _ScriptedFakeProcess.reset(["play_forever", "die", "die", "die", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert errors[-1] == "Aborting after 4 worker deaths in a row with no result"
        assert not _ScriptedFakeProcess.instances[0].is_alive()

    @pytest.mark.asyncio
    async def test_an_error_result_resets_the_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A game that failed still came from a working worker.

        One slot plays on while the other has a death, then a failed game,
        then a death. With the failed game resetting the count, neither
        death completes a round, so the run goes on to a decision.
        """
        _ScriptedFakeProcess.reset(["die", "play_forever", "error", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert not any(e.startswith("Aborting") for e in errors)
        assert len([e for e in errors if "died unexpectedly" in e]) == 2
        assert any("engine failed to start" in e for e in errors)
        assert len([m for m in messages if m["type"] == "complete"]) == 1


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
    async def test_error_while_draining_the_queue_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A queue error while reading results for a death check aborts cleanly.

        The worker still playing is terminated.
        """
        _ScriptedFakeProcess.reset(["play_forever", "die_and_break_queue"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert errors == ["Fatal error reading result queue; aborting"]
        assert not _ScriptedFakeProcess.instances[0].is_alive()

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

        await _run_within(config, 5.0, monkeypatch)

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

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await _run_within(config, 5.0, monkeypatch)

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

        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=500.0,
            book_path=None,
        )

        await _run_within(config, 5.0, monkeypatch)

        captured = capsys.readouterr().out
        lines = [line for line in captured.strip().split("\n") if line]
        error_msgs = [json.loads(line) for line in lines if '"type": "error"' in line]

        assert any("Fatal error reading result queue" in m["message"] for m in error_msgs)


# ---------------------------------------------------------------------------
# Coordinator slots: results free slots, and nothing waits on a worker (#187)
# ---------------------------------------------------------------------------

_REAL_QUEUE = multiprocessing.Queue


class _QueueRecorder:
    """Stands in for ``multiprocessing.Queue`` and records each queue created."""

    def __init__(self) -> None:
        self.queues: list[multiprocessing.Queue[WorkerResult]] = []

    def __call__(self) -> multiprocessing.Queue[WorkerResult]:
        result_queue: multiprocessing.Queue[WorkerResult] = _REAL_QUEUE()
        self.queues.append(result_queue)
        return result_queue


async def _run_within(config: RunConfig, limit_s: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run ``run_sprt`` and fail if it takes longer than ``limit_s``.

    On a timeout, a dummy result goes onto every queue the run created. That
    releases the executor thread blocked in ``result_queue.get``, so a
    regression fails at the limit instead of hanging pytest's shutdown.
    """
    recorder = _QueueRecorder()
    monkeypatch.setattr("sprt_runner.runner.multiprocessing.Queue", recorder)
    try:
        await asyncio.wait_for(run_sprt(config), timeout=limit_s)
    except TimeoutError:
        for result_queue in recorder.queues:
            result_queue.put(
                WorkerResult(
                    game_id="unblock",
                    result=None,
                    termination=None,
                    move_count=0,
                    swap_colors=False,
                    error="unblock",
                )
            )
        pytest.fail(f"run_sprt did not finish within {limit_s} s")


def _patch_engines(monkeypatch: pytest.MonkeyPatch, process_class: type) -> None:
    """Resolve both engines to a fake command and use ``process_class`` for workers."""

    async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
        return "engine_cmd", Path("/fake/dir")

    monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
    monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
    monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", process_class)


def _quick_config(concurrency: int) -> RunConfig:
    """A config whose alternating wins and losses accept H0 after about 20 games."""
    return RunConfig(
        base="base-engine",
        test="test-engine",
        time_control=FixedTimeControl(movetime_ms=100),
        elo0=0.0,
        elo1=200.0,
        book_path=None,
        concurrency=concurrency,
    )


def _white_win(task: WorkerTask) -> WorkerResult:
    """A finished game that White won."""
    return WorkerResult(
        game_id=task.game_id,
        result=GameResult.WHITE_WIN,
        termination="checkmate",
        move_count=20,
        swap_colors=task.swap_colors,
    )


class _LingeringFakeProcess:
    """Delivers its result as soon as it starts, then stays alive until terminated.

    A real worker can still be shutting down when the coordinator reads its
    result: interpreter teardown takes time, and a result that doesn't fit in
    the pipe's buffer (64 KiB measured on this machine) keeps the process
    alive until it has been read. Concurrent random-engine games also tend to
    finish together. A coordinator that frees a slot only
    when its process exits launches nothing more and waits on the queue.
    """

    instances: ClassVar[list[_LingeringFakeProcess]] = []
    next_pid: ClassVar[int] = 15000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self._task, self._queue = args
        self._alive = True
        self._pid = _LingeringFakeProcess.next_pid
        _LingeringFakeProcess.next_pid += 1
        _LingeringFakeProcess.instances.append(self)

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        self._queue.put(_white_win(self._task))

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False


class _BusyThenFastFakeProcess:
    """The first three workers play one long game each; the rest deliver at once.

    Delivering workers stay alive until terminated (see
    ``_LingeringFakeProcess``). ``join(timeout)`` sleeps for ``timeout`` while
    the process is alive, as a real join does, and a join with no timeout on
    a running process fails the test, since a real one would never return.
    """

    busy_slots: ClassVar[int] = 3
    created: ClassVar[int] = 0
    instances: ClassVar[list[_BusyThenFastFakeProcess]] = []
    next_pid: ClassVar[int] = 16000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self._task, self._queue = args
        self.busy = _BusyThenFastFakeProcess.created < _BusyThenFastFakeProcess.busy_slots
        _BusyThenFastFakeProcess.created += 1
        self._alive = True
        self._pid = _BusyThenFastFakeProcess.next_pid
        _BusyThenFastFakeProcess.next_pid += 1
        _BusyThenFastFakeProcess.instances.append(self)

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        if not self.busy:
            self._queue.put(_white_win(self._task))

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        if self._alive:
            if timeout is None:
                raise AssertionError("join() with no timeout on a running worker never returns")
            time.sleep(timeout)

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False


class _ExitingFakeProcess:
    """Delivers its result and exits at once, recording how many were left unreaped.

    Each new worker records how many earlier workers had exited without being
    joined when it was created. Use at concurrency 1: a real worker exits only
    after its result is in the pipe, but this fake's put goes through the
    queue's feeder thread, so a sibling could look dead before its result is
    readable.
    """

    instances: ClassVar[list[_ExitingFakeProcess]] = []
    unreaped_at_launch: ClassVar[list[int]] = []
    next_pid: ClassVar[int] = 17000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self._task, self._queue = args
        self._alive = True
        self.joined = False
        self._pid = _ExitingFakeProcess.next_pid
        _ExitingFakeProcess.next_pid += 1
        _ExitingFakeProcess.unreaped_at_launch.append(
            sum(1 for p in _ExitingFakeProcess.instances if not p._alive and not p.joined)
        )
        _ExitingFakeProcess.instances.append(self)

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        self._queue.put(_white_win(self._task))
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        if not self._alive:
            self.joined = True

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False


class _ScriptedFakeProcess:
    """Each worker follows the behaviour its creation index has in ``script``.

    Workers past the end of the script follow ``default``. Behaviours:

    - ``deliver``: puts a result as it starts, then stays alive until terminated.
    - ``deliver_and_exit``: puts a result, sleeps 50 ms, then exits. The sleep
      lets the queue's feeder thread flush the result into the pipe first,
      which is the order a real worker exits in.
    - ``error``: puts a result saying the game failed, then stays alive.
    - ``die``: exits at once without a result.
    - ``play_forever``: stays alive without a result until terminated.
    - ``deliver_on_second_check``: on the second ``is_alive()`` call, puts a
      result, sleeps 50 ms and exits, so its result arrives just as the
      coordinator checks for dead workers.
    - ``die_and_break_queue``: exits without a result and makes every
      non-blocking ``get`` on the queue raise ``OSError``. Blocking reads
      still work.

    "Exits" means ``is_alive()`` turns False. ``join`` records a join once
    the worker has exited.
    """

    script: ClassVar[list[str]] = []
    default: ClassVar[str] = "deliver"
    instances: ClassVar[list[_ScriptedFakeProcess]] = []
    next_pid: ClassVar[int] = 19000

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self.task, self._queue = args
        index = len(_ScriptedFakeProcess.instances)
        script = _ScriptedFakeProcess.script
        self.behaviour = script[index] if index < len(script) else _ScriptedFakeProcess.default
        self._alive = True
        self._checks = 0
        self.joined = False
        self._pid = _ScriptedFakeProcess.next_pid
        _ScriptedFakeProcess.next_pid += 1
        _ScriptedFakeProcess.instances.append(self)

    @classmethod
    def reset(cls, script: list[str], default: str = "deliver") -> None:
        cls.script = script
        cls.default = default
        cls.instances = []

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        if self.behaviour in ("deliver", "deliver_and_exit"):
            self._queue.put(_white_win(self.task))
        elif self.behaviour == "error":
            self._queue.put(
                WorkerResult(
                    game_id=self.task.game_id,
                    result=None,
                    termination=None,
                    move_count=0,
                    swap_colors=self.task.swap_colors,
                    error="engine failed to start",
                )
            )
        if self.behaviour == "deliver_and_exit":
            time.sleep(0.05)
            self._alive = False
        elif self.behaviour == "die":
            self._alive = False
        elif self.behaviour == "die_and_break_queue":
            blocking_get = self._queue.get

            def _broken(block: bool = True, timeout: float | None = None) -> WorkerResult:
                if not block:
                    raise OSError("result queue broken")
                return blocking_get(block, timeout)

            setattr(self._queue, "get", _broken)  # noqa: B010
            self._alive = False

    def is_alive(self) -> bool:
        if self.behaviour == "deliver_on_second_check" and self._alive:
            self._checks += 1
            if self._checks == 2:
                self._queue.put(_white_win(self.task))
                time.sleep(0.05)
                self._alive = False
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        if not self._alive:
            self.joined = True

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False


class TestDrainResults:
    """Tests for _drain_results, which reads what is already waiting on the queue."""

    def test_reads_every_waiting_result_in_order_then_nothing(self) -> None:
        result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()
        results = [
            WorkerResult(
                game_id=f"g{i}",
                result=GameResult.DRAW,
                termination="stalemate",
                move_count=i,
                swap_colors=False,
            )
            for i in range(3)
        ]
        for result in results:
            result_queue.put(result)
        time.sleep(0.1)  # let the feeder thread flush all three into the pipe

        assert [r.game_id for r in _drain_results(result_queue)] == ["g0", "g1", "g2"]
        assert _drain_results(result_queue) == []
        result_queue.close()
        result_queue.join_thread()


class TestCoordinatorSlots:
    """A delivered result frees its slot at once, and no result waits on a worker (#187)."""

    @pytest.mark.asyncio
    async def test_delivered_workers_free_their_slots_before_exiting(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Four workers that deliver but haven't exited don't stall the run.

        The queue timeout keeps its default, so the run can't rely on it.
        """
        _LingeringFakeProcess.instances = []
        _patch_engines(monkeypatch, _LingeringFakeProcess)

        await _run_within(_quick_config(concurrency=4), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert [m for m in messages if m["type"] == "error"] == []
        assert len([m for m in messages if m["type"] == "complete"]) == 1
        assert len([m for m in messages if m["type"] == "game_result"]) > 4
        # Workers that delivered but never exited are terminated when the run ends.
        assert not any(p.is_alive() for p in _LingeringFakeProcess.instances)

    @pytest.mark.asyncio
    async def test_cancellation_terminates_workers_that_delivered_but_never_exited(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cancelling a run terminates every worker, including ones whose results were read."""
        _LingeringFakeProcess.instances = []
        _patch_engines(monkeypatch, _LingeringFakeProcess)
        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=-0.1,
            elo1=0.1,
            book_path=None,
            concurrency=2,
        )

        task = asyncio.create_task(run_sprt(config))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)

        assert len(_LingeringFakeProcess.instances) > 2
        assert not any(p.is_alive() for p in _LingeringFakeProcess.instances)

    @pytest.mark.asyncio
    async def test_exited_workers_with_unread_results_are_not_reported_dead(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Every worker exits with its result unread; none is reported dead.

        At three workers, two results are often waiting behind the one the
        coordinator reads, so each check for dead workers has to read all
        of them. Results are handled in the order they arrived. Reading what
        is waiting must not wait for more, or each check would cost a whole
        poll.
        """
        _ScriptedFakeProcess.reset([], default="deliver_and_exit")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 4.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert [m for m in messages if m["type"] == "error"] == []
        assert len([m for m in messages if m["type"] == "complete"]) == 1
        game_ids = [m["game_id"] for m in messages if m["type"] == "game_result"]
        assert len(game_ids) > 4
        launched = [p.task.game_id for p in _ScriptedFakeProcess.instances]
        assert game_ids == launched[: len(game_ids)]

    @pytest.mark.asyncio
    async def test_result_handling_never_waits_on_running_workers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Three long games in progress don't slow the games finishing in the fourth slot.

        Joining each running worker for a second after every result would
        cost three seconds per game here.
        """
        _BusyThenFastFakeProcess.created = 0
        _BusyThenFastFakeProcess.instances = []
        _patch_engines(monkeypatch, _BusyThenFastFakeProcess)

        await _run_within(_quick_config(concurrency=4), 3.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert [m for m in messages if m["type"] == "error"] == []
        assert len([m for m in messages if m["type"] == "complete"]) == 1
        assert len([m for m in messages if m["type"] == "game_result"]) > 4

    @pytest.mark.asyncio
    async def test_finished_workers_are_reaped_as_the_run_goes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A worker that has exited is dropped and joined before the next game launches.

        Keeping every finished worker until the end of the run would hold
        each one's ``Process`` object, and the file descriptors it keeps
        open, for every game played.
        """
        _ExitingFakeProcess.instances = []
        _ExitingFakeProcess.unreaped_at_launch = []
        _patch_engines(monkeypatch, _ExitingFakeProcess)

        await _run_within(_quick_config(concurrency=1), 3.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert [m for m in messages if m["type"] == "error"] == []
        assert len(_ExitingFakeProcess.instances) > 4
        assert max(_ExitingFakeProcess.unreaped_at_launch) == 0
        assert all(p.joined for p in _ExitingFakeProcess.instances)


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


class TestOutputDirCLI:
    """Tests for --output-dir CLI argument."""

    def test_output_dir_absent(self) -> None:
        """Without --output-dir, the flag should default to None."""
        parser = build_parser()
        args = parser.parse_args(["run", "--base", "eng1", "--test", "eng2", "--tc", "depth=1"])
        assert args.output_dir is None

    def test_output_dir_present(self) -> None:
        """With --output-dir, the value should be set."""
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--base",
                "eng1",
                "--test",
                "eng2",
                "--tc",
                "depth=1",
                "--output-dir",
                "/tmp/games",
            ]
        )
        assert args.output_dir == "/tmp/games"


class TestSyzygyPathCLI:
    """Tests for --syzygy-path CLI argument."""

    def test_syzygy_path_absent(self) -> None:
        """Without --syzygy-path, the flag should default to None."""
        parser = build_parser()
        args = parser.parse_args(["run", "--base", "eng1", "--test", "eng2", "--tc", "depth=1"])
        assert args.syzygy_path is None

    def test_syzygy_path_present(self) -> None:
        """With --syzygy-path, the raw string value should be set (not a Path)."""
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--base",
                "eng1",
                "--test",
                "eng2",
                "--tc",
                "depth=1",
                "--syzygy-path",
                "/tmp/tables",
            ]
        )
        assert args.syzygy_path == "/tmp/tables"


class TestMainSyzygyPathWiring:
    """main()-level tests proving --syzygy-path reaches AdjudicationConfig.

    A parser-level test alone only proves argparse works — the actual bug
    in issue #173 was the missing kwarg in main()'s AdjudicationConfig(...)
    call, which parser tests cannot see. These tests patch run_sprt itself
    and inspect the RunConfig it is handed instead of executing the game
    loop.
    """

    def test_syzygy_path_reaches_adjudication_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """--syzygy-path must reach RunConfig.adjudication.syzygy_path."""
        tb_dir = tmp_path / "tables"
        tb_dir.mkdir()
        (tb_dir / "KQvK.rtbw").touch()

        captured: dict[str, RunConfig] = {}

        async def _fake_run_sprt(config: RunConfig) -> None:
            captured["config"] = config

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fake_run_sprt)
        monkeypatch.setattr(
            "sys.argv",
            [
                "sprt_runner",
                "run",
                "--base",
                "eng1",
                "--test",
                "eng2",
                "--tc",
                "depth=1",
                "--syzygy-path",
                str(tb_dir),
            ],
        )

        main()

        assert captured["config"].adjudication.syzygy_path == tb_dir

    def test_syzygy_path_absent_defaults_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Omitting --syzygy-path must leave syzygy_path as None (default behaviour unchanged)."""
        captured: dict[str, RunConfig] = {}

        async def _fake_run_sprt(config: RunConfig) -> None:
            captured["config"] = config

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fake_run_sprt)
        monkeypatch.setattr(
            "sys.argv",
            ["sprt_runner", "run", "--base", "eng1", "--test", "eng2", "--tc", "depth=1"],
        )

        main()

        assert captured["config"].adjudication.syzygy_path is None


class TestSyzygyPathValidationCLI:
    """main()-level fail-fast validation of --syzygy-path (issue #173, part B).

    An explicitly-supplied path that is unusable must fail before any game
    runs, on the same channel (JSON error line on stdout) and with the same
    exit code as the existing --tc pre-flight check.
    """

    @staticmethod
    def _make_bad_path(tmp_path: Path, kind: str) -> str:
        """Build a --syzygy-path value that must be rejected."""
        if kind == "missing":
            return str(tmp_path / "does-not-exist")
        if kind == "empty-dir":
            empty_dir = tmp_path / "empty-tables"
            empty_dir.mkdir()
            return str(empty_dir)
        if kind == "dtz-only":
            dtz_dir = tmp_path / "3-4-5-dtz"
            dtz_dir.mkdir()
            (dtz_dir / "KQvK.rtbz").touch()
            return str(dtz_dir)
        if kind == "empty-value":
            return ""
        raise AssertionError(f"unknown kind: {kind}")

    @pytest.mark.parametrize("kind", ["missing", "empty-dir", "dtz-only", "empty-value"])
    def test_bad_path_exits_nonzero_with_stdout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        kind: str,
    ) -> None:
        async def _fail_if_called(config: RunConfig) -> None:
            raise AssertionError("run_sprt must not be reached when validation fails")

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fail_if_called)
        monkeypatch.setattr(
            "sys.argv",
            [
                "sprt_runner",
                "run",
                "--base",
                "eng1",
                "--test",
                "eng2",
                "--tc",
                "depth=1",
                "--syzygy-path",
                self._make_bad_path(tmp_path, kind),
            ],
        )

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code != 0
        message = json.loads(capsys.readouterr().out.strip())
        assert message["type"] == "error"


_RUN_ARGV = ["run", "--base", "eng1", "--test", "eng2", "--tc", "depth=1"]


class TestTimeLimitFlagsCLI:
    """Parser-level tests for --time-margin-ms and --move-timeout-ms."""

    def test_defaults(self) -> None:
        args = build_parser().parse_args(_RUN_ARGV)
        assert args.time_margin_ms == DEFAULT_TIME_MARGIN_MS == 100
        assert args.move_timeout_ms == DEFAULT_MOVE_TIMEOUT_MS == 30_000

    def test_explicit_values(self) -> None:
        args = build_parser().parse_args(
            [*_RUN_ARGV, "--time-margin-ms", "250", "--move-timeout-ms", "45000"]
        )
        assert args.time_margin_ms == 250
        assert args.move_timeout_ms == 45_000


class TestMainTimeLimitWiring:
    """main()-level tests proving the flags reach RunConfig.

    Parser tests cannot see a kwarg dropped from main()'s RunConfig(...) call,
    so these patch run_sprt and inspect the config it is handed.
    """

    @staticmethod
    def _run_main(monkeypatch: pytest.MonkeyPatch, *extra_args: str) -> RunConfig:
        """Run main() with the given extra flags and return the resulting RunConfig."""
        captured: dict[str, RunConfig] = {}

        async def _fake_run_sprt(config: RunConfig) -> None:
            captured["config"] = config

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fake_run_sprt)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV, *extra_args])

        main()

        return captured["config"]

    def test_flags_reach_run_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(
            monkeypatch, "--time-margin-ms", "250", "--move-timeout-ms", "45000"
        )
        assert config.time_margin_ms == 250
        assert config.move_timeout_ms == 45_000

    def test_margin_of_zero_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--time-margin-ms", "0")
        assert config.time_margin_ms == 0

    def test_smallest_move_timeout_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--move-timeout-ms", "1")
        assert config.move_timeout_ms == 1


class TestMainStartMethod:
    """main() starts workers from a forkserver with the runner preloaded.

    A forkserver child starts with default signal handling, unlike a fork of
    the multi-threaded coordinator. With ``sprt_runner.runner`` preloaded it
    starts about as fast as a fork, where spawn is markedly slower. main()
    only chooses when nothing else has, and only where forkserver exists.
    """

    @staticmethod
    def _run_main(monkeypatch: pytest.MonkeyPatch) -> tuple[str | None, list[list[str]]]:
        """Run main() and return the start method run_sprt saw, and every preload set."""
        seen: dict[str, str | None] = {}
        preloads: list[list[str]] = []

        async def _fake_run_sprt(config: RunConfig) -> None:
            seen["method"] = multiprocessing.get_start_method(allow_none=True)

        def _record_preload(modules: list[str]) -> None:
            preloads.append(list(modules))

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fake_run_sprt)
        # Every public way to set the preload ends in this function.
        monkeypatch.setattr("multiprocessing.forkserver.set_forkserver_preload", _record_preload)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV])

        main()

        return seen["method"], preloads

    def test_chooses_a_preloaded_forkserver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if "forkserver" not in multiprocessing.get_all_start_methods():
            pytest.skip("forkserver isn't available on this platform")
        multiprocessing.set_start_method(None, force=True)

        method, preloads = self._run_main(monkeypatch)

        assert method == "forkserver"
        assert preloads == [["sprt_runner.runner"]]

    @pytest.mark.parametrize("chosen", multiprocessing.get_all_start_methods())
    def test_keeps_a_start_method_already_chosen(
        self, monkeypatch: pytest.MonkeyPatch, chosen: str
    ) -> None:
        """Whatever the caller chose, fork and forkserver included, stays, with no preload."""
        multiprocessing.set_start_method(chosen, force=True)

        method, preloads = self._run_main(monkeypatch)

        assert method == chosen
        assert preloads == []

    def test_keeps_the_default_where_forkserver_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        multiprocessing.set_start_method(None, force=True)
        monkeypatch.setattr(
            "sprt_runner.runner.multiprocessing.get_all_start_methods", lambda: ["spawn"]
        )

        method, preloads = self._run_main(monkeypatch)

        assert method is None
        assert preloads == []


class TestTimeLimitValidationCLI:
    """Invalid limits fail before any game runs, on the --tc error channel."""

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--time-margin-ms", "-1"),
            ("--move-timeout-ms", "0"),
            ("--move-timeout-ms", "-1"),
        ],
        ids=["negative_margin", "zero_timeout", "negative_timeout"],
    )
    def test_invalid_limit_exits_nonzero_with_stdout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        flag: str,
        value: str,
    ) -> None:
        async def _fail_if_called(config: RunConfig) -> None:
            raise AssertionError("run_sprt must not be reached when validation fails")

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fail_if_called)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV, flag, value])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code != 0
        message = json.loads(capsys.readouterr().out.strip())
        assert message["type"] == "error"
        assert flag in message["message"]


class TestConcurrencyValidationCLI:
    """--concurrency below 1 fails before any game runs, on the --tc error channel.

    With no worker slot the coordinator would launch nothing and wait forever.
    """

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_concurrency_below_one_exits_nonzero_with_stdout_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        value: str,
    ) -> None:
        async def _fail_if_called(config: RunConfig) -> None:
            raise AssertionError("run_sprt must not be reached when validation fails")

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fail_if_called)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV, "--concurrency", value])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code != 0
        message = json.loads(capsys.readouterr().out.strip())
        assert message["type"] == "error"
        assert "--concurrency" in message["message"]


class TestRunSprtTimeLimitWiring:
    """The limits on RunConfig reach the GameConfig each worker plays with."""

    @staticmethod
    async def _captured_game_configs(
        monkeypatch: pytest.MonkeyPatch, config: RunConfig
    ) -> list[GameConfig]:
        """Run the SPRT loop against fake workers and return their game configs."""
        _FakeProcess.captured_tasks = []

        async def _mock_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _mock_resolve)
        monkeypatch.setattr("sprt_runner.runner.parse_engine_spec", _mock_parse_engine_spec)
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _FakeProcess)

        await run_sprt(config)

        return [task.game_config for task in _FakeProcess.captured_tasks]

    @pytest.mark.asyncio
    async def test_limits_reach_every_game_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        game_configs = await self._captured_game_configs(
            monkeypatch,
            RunConfig(
                base="base-engine",
                test="test-engine",
                time_control=FixedTimeControl(movetime_ms=100),
                elo0=0.0,
                elo1=500.0,
                time_margin_ms=250,
                move_timeout_ms=45_000,
            ),
        )

        assert len(game_configs) >= 2
        assert {config.time_margin_ms for config in game_configs} == {250}
        assert {config.move_timeout_ms for config in game_configs} == {45_000}


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
