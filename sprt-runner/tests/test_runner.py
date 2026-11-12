"""Tests for the SPRT runner CLI entry point and orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import multiprocessing
import os
import re
import secrets
import shlex
import signal
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import MagicMock

import pytest
from shared.storage.models import GameResult, Move
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    parse_time_control,
)
from shared.uci_client import UCIEngineError, UCITimeoutError
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import (
    DEFAULT_MOVE_TIMEOUT_MS,
    DEFAULT_TIME_MARGIN_MS,
    GameConfig,
    GameOutcome,
    TerminationReason,
)
from sprt_runner.openings import OpeningPair, load_openings, shuffle_openings
from sprt_runner.runner import (
    DEFAULT_BOOK,
    DEFAULT_HANDSHAKE_TIMEOUT_MS,
    RunAbortedError,
    RunConfig,
    WorkerResult,
    WorkerTask,
    _cleanup_workers,  # type: ignore[reportPrivateUsage]
    _drain_results,  # type: ignore[reportPrivateUsage]
    _play_single_game,  # type: ignore[reportPrivateUsage]
    _resolve_run_command,  # type: ignore[reportPrivateUsage]
    _start_forkserver_ignoring_sighup,  # type: ignore[reportPrivateUsage]
    _terminate_all_workers,  # type: ignore[reportPrivateUsage]
    build_parser,
    format_complete_message,
    format_error_message,
    format_game_result_message,
    format_interrupted_message,
    format_progress_message,
    format_start_message,
    main,
    run_sprt,
    worker_entry,
)
from sprt_runner.worktree import EngineSpec, WorktreeError

# Re-export for monkeypatching
_QUEUE_TIMEOUT_SECONDS = "sprt_runner.runner._QUEUE_TIMEOUT_SECONDS"


# Fake workers' pids start here, past any real pid: Linux allows at most
# 4,194,304, and macOS 99,999. A signal sent to one can't reach a process.
_FAKE_PID_BASE = 4_200_000
_REAL_KILL = os.kill


@dataclass
class _ProcessGroups:
    """The runner's process-group calls, recorded instead of made.

    Attributes:
        led: The arguments of each ``os.setpgid`` call, in order.
        sessions_led: How many times ``os.setsid`` was called.
        killed: The arguments of each ``os.killpg`` call, in order. A signal
            ``os.kill`` sends to a group (a pid below -1) is recorded here as
            that group's, and one to the caller's own group or to every
            process (pid 0 or -1) as given.
        signalled: Signals ``os.kill`` was asked to send to one process other
            than this one, recorded instead of sent. Every fake worker's pid is
            made up, so such a signal could only reach a stranger. The fixture
            fails the test if there are any.
        sighup_when_leading: SIGHUP's disposition at each ``os.setpgid`` call.
        errors: Raised by the next ``os.killpg`` calls, one each, in order.
        log: When set, each ``os.killpg`` call also adds ``("killpg", pgid)``
            here, so a test can see it among its workers' own events.
        forkserver_sighup: SIGHUP's disposition each time the forkserver was
            started, which is recorded instead.
    """

    led: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    sessions_led: int = 0
    killed: list[tuple[int | None, int]] = field(default_factory=list[tuple[int | None, int]])
    signalled: list[tuple[int, int]] = field(default_factory=list[tuple[int, int]])
    sighup_when_leading: list[object] = field(default_factory=list[object])
    errors: list[OSError] = field(default_factory=list[OSError])
    log: list[tuple[str, int | None]] | None = None
    forkserver_sighup: list[object] = field(default_factory=list[object])

    def setpgid(self, pid: int, pgid: int) -> None:
        self.led.append((pid, pgid))
        self.sighup_when_leading.append(signal.getsignal(signal.SIGHUP))

    def setsid(self) -> int:
        self.sessions_led += 1
        return 0

    def killpg(self, pgid: int | None, sig: int) -> None:
        self.killed.append((pgid, sig))
        if self.log is not None:
            self.log.append(("killpg", pgid))
        if self.errors:
            raise self.errors.pop(0)

    def kill(self, pid: int, sig: int) -> None:
        if pid == os.getpid():
            _REAL_KILL(pid, sig)
        elif pid > 0:
            self.signalled.append((pid, sig))
        else:
            self.killpg(-pid if pid < -1 else pid, sig)

    def ensure_forkserver(self) -> None:
        self.forkserver_sighup.append(signal.getsignal(signal.SIGHUP))
        if self.log is not None:
            self.log.append(("forkserver", None))


@pytest.fixture(autouse=True)
def process_groups(monkeypatch: pytest.MonkeyPatch) -> Iterator[_ProcessGroups]:
    """Record the runner's process-group calls instead of making them.

    The fake workers' pids are made up, so a real group kill could reach an
    unrelated process group. ``worker_entry``, which some tests run in this
    process, would move pytest out of its own group or session and change how
    it handles SIGTTOU and SIGHUP, and ``main()`` would start a real
    forkserver. Only a signal ``os.kill`` sends to this process goes through,
    so a test can signal itself; one aimed at any other process fails the
    test.
    """
    groups = _ProcessGroups()
    monkeypatch.setattr(os, "setpgid", groups.setpgid)
    monkeypatch.setattr(os, "setsid", groups.setsid)
    monkeypatch.setattr(os, "killpg", groups.killpg)
    monkeypatch.setattr(os, "kill", groups.kill)
    monkeypatch.setattr("multiprocessing.forkserver.ensure_running", groups.ensure_forkserver)
    dispositions = {sig: signal.getsignal(sig) for sig in (signal.SIGTTOU, signal.SIGHUP)}
    yield groups
    for sig, handler in dispositions.items():
        if handler is not None:
            signal.signal(sig, handler)
    assert groups.signalled == [], f"signalled processes the test didn't start: {groups.signalled}"


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
        # A run built in code plays from the initial position unless it's given
        # a book: only the CLI defaults to one (#12).
        assert config.book_path is None
        assert config.seed is None

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

    def test_runs_have_no_game_limit_by_default(self) -> None:
        """Without a limit, a run plays until SPRT decides, as before #189."""
        config = RunConfig(
            base="random-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
        )
        assert config.max_games is None
        assert config.early_stop is True


class TestJSONLineFormatters:
    """Tests for JSON-lines output formatting."""

    def test_start_message(self) -> None:
        """A run's first line, recording the seed for the order of its openings (#12)."""
        assert json.loads(format_start_message(seed=7)) == {"type": "start", "seed": 7}

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
        """``complete`` carries the tallies and an Elo estimate from them (#189).

        30 wins, 10 losses and 60 draws are worth +70.4 Elo, between +28.7 and +114.3.
        """
        msg = format_complete_message(
            result="H1",
            total_games=100,
            llr=3.5,
            wins=30,
            losses=10,
            draws=60,
            errors=2,
            terminations={"checkmate": 25, "adjudication": 75},
        )
        parsed = json.loads(msg)
        assert parsed == {
            "type": "complete",
            "result": "H1",
            "total_games": 100,
            "llr": 3.5,
            "wins": 30,
            "losses": 10,
            "draws": 60,
            "errors": 2,
            "terminations": {"checkmate": 25, "adjudication": 75},
            "elo": pytest.approx(70.43650362227247, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_lower": pytest.approx(28.694507394308168, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_upper": pytest.approx(114.29699219108149, rel=1e-9),  # type: ignore[reportUnknownMemberType]
        }

    def test_complete_message_reports_an_infinite_elo_as_null(self) -> None:
        """Three wins and nothing else are infinitely many Elo, which JSON can only say as null."""
        msg = format_complete_message(
            result="H1",
            total_games=3,
            llr=3.0,
            wins=3,
            losses=0,
            draws=0,
            errors=0,
            terminations={"checkmate": 3},
        )
        parsed = json.loads(msg)
        assert {key: parsed.get(key, "missing") for key in ("elo", "elo_lower", "elo_upper")} == {
            "elo": None,
            "elo_lower": None,
            "elo_upper": None,
        }

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
            format_complete_message(
                "H0",
                200,
                -3.5,
                wins=50,
                losses=60,
                draws=90,
                errors=0,
                terminations={"checkmate": 110, "draw_rule": 90},
            ),
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
            white_label="base engine eng1",
            black_label="test engine eng2",
        )
        assert task.game_id == "game-1"
        assert task.swap_colors is False
        assert task.handshake_timeout_ms == DEFAULT_HANDSHAKE_TIMEOUT_MS


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
            white_label="base engine eng1",
            black_label="test engine eng2",
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
        # Should be an error since neither engine exists. White starts first.
        assert result.error is not None
        assert result.error.startswith("base engine eng1 (White) failed to start: ")

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
            white_label="base engine eng1",
            black_label="test engine eng2",
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

    def test_worker_leads_its_own_process_group_before_playing(
        self, monkeypatch: pytest.MonkeyPatch, process_groups: _ProcessGroups
    ) -> None:
        """The engines a worker starts join its group, for the coordinator to kill (#209).

        The worker leads the group before its game starts an engine. Out of the
        terminal's foreground group, it ignores SIGTTOU, which a terminal set to
        ``stty tostop`` would otherwise stop it with the first time it logs. It
        puts SIGHUP back to its default, since the forkserver it comes from
        ignores it, but only once it has left the runner's group, which a
        terminal's hangup reaches.
        """
        seen: dict[str, object] = {}

        async def _record(task: WorkerTask) -> WorkerResult:
            seen["led"] = list(process_groups.led)
            seen["sigttou"] = signal.getsignal(signal.SIGTTOU)
            seen["sighup"] = signal.getsignal(signal.SIGHUP)
            return WorkerResult(
                game_id=task.game_id,
                result=GameResult.DRAW,
                termination="stalemate",
                move_count=0,
                swap_colors=task.swap_colors,
            )

        monkeypatch.setattr("sprt_runner.runner._play_single_game", _record)
        task = WorkerTask(
            game_id="group",
            white_cmd="engine",
            black_cmd="engine",
            game_config=GameConfig(time_control=DepthTimeControl(depth=1)),
            swap_colors=False,
            white_label="base engine eng1",
            black_label="test engine eng2",
        )
        result_queue: multiprocessing.Queue[WorkerResult] = multiprocessing.Queue()
        old_sigterm = signal.getsignal(signal.SIGTERM)
        old_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            worker_entry(task, result_queue)
            assert result_queue.get(timeout=5).game_id == "group"
        finally:
            signal.signal(signal.SIGTERM, old_sigterm)
            signal.signal(signal.SIGINT, old_sigint)
            result_queue.close()
            result_queue.join_thread()

        assert seen == {"led": [(0, 0)], "sigttou": signal.SIG_IGN, "sighup": signal.SIG_DFL}
        assert process_groups.sighup_when_leading == [signal.SIG_IGN]


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
            white_label="base engine eng1",
            black_label="test engine eng2",
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


@dataclass(frozen=True)
class _Startup:
    """How a fake engine starts.

    Attributes:
        uci_s: Seconds the engine takes to answer ``uci``.
        isready_s: Seconds it takes to answer ``isready``.
        exits: The engine exits at startup, so its first answer never comes.
        unlaunchable: The engine's command can't be launched at all.
        hangs: The engine never answers ``uci``, whatever the limit.
        error: An exception ``uci`` raises instead of answering.
        quit_raises: ``quit`` raises, as nothing in the real client is known
            to since #209, so that the guard around it is what's tested.
        quit_hangs: ``quit`` never returns.
    """

    uci_s: float = 0.0
    isready_s: float = 0.0
    exits: bool = False
    unlaunchable: bool = False
    hangs: bool = False
    error: Exception | None = None
    quit_raises: bool = False
    quit_hangs: bool = False


class _UnforeseenQuitError(Exception):
    """Something ``quit()`` isn't known to raise, of no particular kind."""


class _StartupFakeClient:
    """Stands in for ``UCIClient`` in ``_play_single_game``, with scripted startups.

    ``startups`` maps each engine command to how that engine starts. Time is
    simulated the way the real client counts it: an answer that takes longer
    than the call's ``timeout``, or than ``default_timeout`` (10 s, as in the
    real client) when no timeout is passed, raises ``UCITimeoutError`` at once.
    """

    startups: ClassVar[dict[str, _Startup]] = {}
    instances: ClassVar[list[_StartupFakeClient]] = []

    def __init__(self, engine_path: str, *, default_timeout: float = 10.0) -> None:
        self.engine_path = engine_path
        self.default_timeout = default_timeout
        self.startup = _StartupFakeClient.startups[engine_path]
        self.waiting = False
        self.quit_called = False
        self.quitting = False
        _StartupFakeClient.instances.append(self)

    async def start(self) -> None:
        if self.startup.unlaunchable:
            raise UCIEngineError(
                f"Failed to start engine '{self.engine_path}': [Errno 2] No such file or directory"
            )

    async def uci(self, *, timeout: float | None = None) -> list[str]:
        if self.startup.error is not None:
            raise self.startup.error
        if self.startup.hangs:
            self.waiting = True
            await asyncio.Event().wait()
        self._answer(self.startup.uci_s, timeout)
        return []

    async def isready(self, *, timeout: float | None = None) -> None:
        self._answer(self.startup.isready_s, timeout)

    def _answer(self, seconds: float, timeout: float | None) -> None:
        if self.startup.exits:
            raise UCIEngineError("Engine process terminated unexpectedly (returncode=2)")
        limit = self.default_timeout if timeout is None else timeout
        if seconds > limit:
            raise UCITimeoutError(f"Engine did not respond within {limit}s")

    async def quit(self) -> None:
        self.quit_called = True
        if self.startup.quit_raises:
            raise _UnforeseenQuitError("quit failed")
        if self.startup.quit_hangs:
            self.quitting = True
            await asyncio.Event().wait()


async def _drawn_game(*, white: object, black: object, config: GameConfig) -> GameOutcome:
    """Stands in for ``play_game``: every game that gets going is drawn."""
    return GameOutcome(result=GameResult.DRAW, termination=TerminationReason.STALEMATE, moves=[])


def _startup_task(handshake_timeout_ms: int = DEFAULT_HANDSHAKE_TIMEOUT_MS) -> WorkerTask:
    """A game in which the base engine, ``white-engine``, plays White against the test engine."""
    return WorkerTask(
        game_id="startup",
        white_cmd="white-engine",
        black_cmd="black-engine",
        game_config=GameConfig(time_control=DepthTimeControl(depth=1)),
        swap_colors=False,
        white_label="base engine base-x",
        black_label="test engine test-y",
        handshake_timeout_ms=handshake_timeout_ms,
    )


async def _start_game(
    monkeypatch: pytest.MonkeyPatch,
    white: _Startup,
    black: _Startup,
    task: WorkerTask | None = None,
    play: Callable[..., Awaitable[GameOutcome]] = _drawn_game,
) -> WorkerResult:
    """Play ``task``, by default ``_startup_task()``, with fake engines that start as given.

    ``play`` stands in for ``play_game`` once both engines have started.
    """
    _StartupFakeClient.startups = {"white-engine": white, "black-engine": black}
    _StartupFakeClient.instances = []
    monkeypatch.setattr("sprt_runner.runner.UCIClient", _StartupFakeClient)
    monkeypatch.setattr("sprt_runner.runner.play_game", play)
    return await _play_single_game(task or _startup_task())


class TestEngineStartup:
    """A game whose engine can't start fails with an error naming that engine (#197).

    Each engine has the task's ``handshake_timeout_ms`` to answer ``uci``, and
    then the same again to answer ``isready``.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("white", "black", "error"),
        [
            (
                _Startup(exits=True),
                _Startup(),
                "base engine base-x (White) failed to start: "
                "Engine process terminated unexpectedly (returncode=2)",
            ),
            (
                _Startup(),
                _Startup(exits=True),
                "test engine test-y (Black) failed to start: "
                "Engine process terminated unexpectedly (returncode=2)",
            ),
            (
                _Startup(exits=True),
                _Startup(exits=True),
                "base engine base-x (White) failed to start: "
                "Engine process terminated unexpectedly (returncode=2)",
            ),
        ],
        ids=["white", "black", "both_white_first"],
    )
    async def test_an_engine_that_exits_at_startup_is_named(
        self, monkeypatch: pytest.MonkeyPatch, white: _Startup, black: _Startup, error: str
    ) -> None:
        result = await _start_game(monkeypatch, white, black)

        assert result.result is None
        assert result.error == error

    @pytest.mark.asyncio
    async def test_an_engine_that_cannot_be_launched_is_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await _start_game(monkeypatch, _Startup(), _Startup(unlaunchable=True))

        assert result.result is None
        assert result.error == (
            "test engine test-y (Black) failed to start: "
            "Failed to start engine 'black-engine': [Errno 2] No such file or directory"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "slow", [_Startup(uci_s=2.0), _Startup(isready_s=2.0)], ids=["uci", "isready"]
    )
    async def test_an_engine_slower_than_the_limit_fails_to_start(
        self, monkeypatch: pytest.MonkeyPatch, slow: _Startup
    ) -> None:
        """An answer that takes 2 s fails at a 1 s limit, though the client's own is 10 s.

        Both engines are still told to quit, so one still starting doesn't outlive the game.
        """
        task = _startup_task(handshake_timeout_ms=1000)

        result = await _start_game(monkeypatch, _Startup(), slow, task)

        assert result.result is None
        assert result.error == (
            "test engine test-y (Black) failed to start: Engine did not respond within 1.0s"
        )
        assert [client.quit_called for client in _StartupFakeClient.instances] == [True, True]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("slow", "limit_ms"),
        [
            (_Startup(uci_s=11.0), 20_000),
            (_Startup(isready_s=11.0), 20_000),
            (_Startup(uci_s=35.0), 45_000),
        ],
        ids=["uci", "isready", "above_the_default"],
    )
    async def test_an_engine_within_a_raised_limit_starts(
        self, monkeypatch: pytest.MonkeyPatch, slow: _Startup, limit_ms: int
    ) -> None:
        """#197's engine took 11 s to answer ``uci``. With a 20 s limit, its game is played.

        A limit can also be raised past the 30 s default.
        """
        task = _startup_task(handshake_timeout_ms=limit_ms)

        result = await _start_game(monkeypatch, slow, _Startup(), task)

        assert result.error is None
        assert result.result == GameResult.DRAW

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("seconds", "starts"),
        [
            (DEFAULT_HANDSHAKE_TIMEOUT_MS / 1000, True),
            (DEFAULT_HANDSHAKE_TIMEOUT_MS / 1000 + 0.001, False),
        ],
        ids=["at_the_limit", "just_over"],
    )
    async def test_a_task_gets_the_default_limit(
        self, monkeypatch: pytest.MonkeyPatch, seconds: float, starts: bool
    ) -> None:
        result = await _start_game(monkeypatch, _Startup(uci_s=seconds), _Startup())

        assert (result.error is None) is starts

    @pytest.mark.asyncio
    async def test_a_failure_after_both_engines_start_names_neither(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error from the game itself isn't blamed on either engine's start.

        Here the game raises what an unparseable ``info`` line would.
        """

        async def _bad_info_line(
            *, white: object, black: object, config: GameConfig
        ) -> GameOutcome:
            raise ValueError("invalid literal for int() with base 10: 'x'")

        result = await _start_game(monkeypatch, _Startup(), _Startup(), play=_bad_info_line)

        assert result.result is None
        assert result.error == "invalid literal for int() with base 10: 'x'"

    @pytest.mark.asyncio
    async def test_an_error_with_no_message_is_named_by_its_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bare exception still says why, at startup and during the game."""

        async def _bare_error(*, white: object, black: object, config: GameConfig) -> GameOutcome:
            raise EOFError

        at_start = await _start_game(monkeypatch, _Startup(error=EOFError()), _Startup())
        in_game = await _start_game(monkeypatch, _Startup(), _Startup(), play=_bare_error)

        assert at_start.error == "base engine base-x (White) failed to start: EOFError"
        assert in_game.error == "EOFError"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("white", "black", "result", "error", "label"),
        [
            (
                _Startup(exits=True, quit_raises=True),
                _Startup(),
                None,
                "base engine base-x (White) failed to start: "
                "Engine process terminated unexpectedly (returncode=2)",
                "base engine base-x",
            ),
            (
                _Startup(),
                _Startup(exits=True, quit_raises=True),
                None,
                "test engine test-y (Black) failed to start: "
                "Engine process terminated unexpectedly (returncode=2)",
                "test engine test-y",
            ),
            (_Startup(quit_raises=True), _Startup(), GameResult.DRAW, None, "base engine base-x"),
        ],
        ids=["white_failed_to_start", "black_failed_to_start", "played_game"],
    )
    async def test_a_quit_that_raises_keeps_the_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        white: _Startup,
        black: _Startup,
        result: GameResult | None,
        error: str | None,
        label: str,
    ) -> None:
        """Should ``quit()`` raise, the game's result still comes back.

        The real client kills an engine it can't tell to quit rather than
        raise (#209), so this guards against anything else. The other engine
        is still told to quit, and a warning with the traceback names the
        engine.
        """
        outcome = await _start_game(monkeypatch, white, black)

        assert (outcome.result, outcome.error) == (result, error)
        assert [client.quit_called for client in _StartupFakeClient.instances] == [True, True]
        warnings = [
            record
            for record in caplog.records
            if record.name == "sprt_runner.runner" and record.levelno == logging.WARNING
        ]
        assert [record.getMessage() for record in warnings] == [
            f"Couldn't tell the {label} to quit"
        ]
        assert warnings[0].exc_info is not None
        assert warnings[0].exc_info[0] is _UnforeseenQuitError

    @pytest.mark.asyncio
    async def test_cancelling_a_game_while_it_quits_an_engine_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard around ``quit()`` doesn't swallow cancellation."""
        game = asyncio.ensure_future(
            _start_game(monkeypatch, _Startup(quit_hangs=True), _Startup())
        )
        for _ in range(100):
            await asyncio.sleep(0)
            if any(client.quitting for client in _StartupFakeClient.instances):
                break
        assert any(client.quitting for client in _StartupFakeClient.instances)

        game.cancel()

        with pytest.raises(asyncio.CancelledError):
            await game

    @pytest.mark.asyncio
    async def test_cancelling_a_game_mid_handshake_is_not_a_failed_start(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cancellation, such as Ctrl-C in a worker, propagates instead of failing the game.

        Both engines are still told to quit.
        """
        game = asyncio.ensure_future(_start_game(monkeypatch, _Startup(hangs=True), _Startup()))
        for _ in range(100):
            await asyncio.sleep(0)
            if any(client.waiting for client in _StartupFakeClient.instances):
                break
        assert any(client.waiting for client in _StartupFakeClient.instances)

        game.cancel()

        with pytest.raises(asyncio.CancelledError):
            await game
        assert [client.quit_called for client in _StartupFakeClient.instances] == [True, True]


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
    _next_pid: ClassVar[int] = _FAKE_PID_BASE + 7000

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
    _next_pid: ClassVar[int] = _FAKE_PID_BASE + 9000

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
    _next_pid: ClassVar[int] = _FAKE_PID_BASE + 6000

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
    next_pid: ClassVar[int] = _FAKE_PID_BASE + 9000

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
        _DeadFakeProcess.next_pid = _FAKE_PID_BASE + 9000

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

        messages = await _run_to_fatal_stop(config, 5.0, monkeypatch, capsys)

        # There should be at least two error messages:
        # 1) per-game error with game ID
        # 2) the fatal stop after a full round of deaths
        error_msgs = [m for m in messages if m["type"] == "error"]
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
            next_pid: ClassVar[int] = _FAKE_PID_BASE + 8000

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
        _DeadFakeProcess.next_pid = _FAKE_PID_BASE + 9000
        _patch_engines(monkeypatch, _DeadFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=2), 2.0, monkeypatch, capsys)

        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        tasks = _DeadFakeProcess.captured_tasks
        assert len(tasks) == 2
        assert errors == [
            f"Worker for game {tasks[0].game_id} died unexpectedly (pid={_FAKE_PID_BASE + 9000})",
            f"Worker for game {tasks[1].game_id} died unexpectedly (pid={_FAKE_PID_BASE + 9001})",
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

        messages = await _run_to_fatal_stop(_quick_config(concurrency=2), 5.0, monkeypatch, capsys)

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

        messages = await _run_to_fatal_stop(_quick_config(concurrency=3), 5.0, monkeypatch, capsys)

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


class TestWorkerGroupsAreKilled:
    """Each worker's engines are in its process group, which the coordinator kills (#209).

    An engine that never reads its input again would otherwise outlive its
    worker. The fakes' pids stand for their groups, and the autouse
    ``process_groups`` fixture records the kills.
    """

    @pytest.mark.asyncio
    async def test_a_dead_workers_group_is_killed_when_the_death_is_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_groups: _ProcessGroups,
    ) -> None:
        """The run goes on to a decision after one death.

        The dead worker isn't among those terminated when the run ends, so its
        group has to go when its death is found.
        """
        _ScriptedFakeProcess.reset(["die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        dead = _ScriptedFakeProcess.instances[0]
        assert (dead.pid, signal.SIGKILL) in process_groups.killed

    @pytest.mark.asyncio
    async def test_every_dead_workers_group_is_killed_when_deaths_share_a_poll(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_groups: _ProcessGroups,
    ) -> None:
        """Two of three workers die before the first poll, which finds both.

        Two deaths are short of a full round at three workers, so the run goes
        on to a decision.
        """
        _ScriptedFakeProcess.reset(["play_forever", "die", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 5.0, monkeypatch)

        killed = {pgid for pgid, sig in process_groups.killed if sig == signal.SIGKILL}
        assert {p.pid for p in _ScriptedFakeProcess.instances[1:3]} <= killed

    @pytest.mark.asyncio
    async def test_dead_workers_groups_are_killed_when_their_deaths_stop_the_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        process_groups: _ProcessGroups,
    ) -> None:
        """Both dead workers' groups go, and so does the group of the worker still playing."""
        _ScriptedFakeProcess.reset(["play_forever", "die", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_to_fatal_stop(_quick_config(concurrency=2), 5.0, monkeypatch, capsys)

        killed = {pgid for pgid, sig in process_groups.killed if sig == signal.SIGKILL}
        assert {p.pid for p in _ScriptedFakeProcess.instances} <= killed

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("script", "fatal"),
        [(["play_forever"], False), (["play_forever", "error", "error"], True)],
        ids=["decision", "fatal_stop"],
    )
    async def test_a_worker_still_playing_has_its_group_killed_when_the_run_ends(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        process_groups: _ProcessGroups,
        script: list[str],
        fatal: bool,
    ) -> None:
        _ScriptedFakeProcess.reset(script)
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        if fatal:
            await _run_to_fatal_stop(_quick_config(concurrency=2), 5.0, monkeypatch, capsys)
        else:
            await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        playing = _ScriptedFakeProcess.instances[0]
        assert (playing.pid, signal.SIGKILL) in process_groups.killed

    @pytest.mark.asyncio
    async def test_a_worker_that_finished_its_game_has_its_group_left_alone(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_groups: _ProcessGroups,
    ) -> None:
        """Each worker delivers, exits and is reaped before the next starts.

        At one worker, nothing is left running when the run decides, so no
        group is killed at all. The user declined killing a finished worker's
        group as it's reaped (2026-11-12): its engines have been told to quit.
        """
        _ScriptedFakeProcess.reset([], default="deliver_and_exit")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=1), 10.0, monkeypatch)

        assert len(_ScriptedFakeProcess.instances) > 1
        assert all(p.joined for p in _ScriptedFakeProcess.instances)
        assert process_groups.killed == []

    @pytest.mark.asyncio
    async def test_a_cancelled_run_kills_the_groups_of_workers_still_playing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_groups: _ProcessGroups,
    ) -> None:
        _ScriptedFakeProcess.reset([], default="play_forever")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        run = asyncio.create_task(run_sprt(_quick_config(concurrency=2)))
        deadline = time.monotonic() + 5
        while len(_ScriptedFakeProcess.instances) < 2:
            assert time.monotonic() < deadline, "the run didn't start two workers within 5 s"
            await asyncio.sleep(0.01)
        run.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=5)
        killed = {pgid for pgid, sig in process_groups.killed if sig == signal.SIGKILL}
        assert {p.pid for p in _ScriptedFakeProcess.instances} <= killed


class TestHangup:
    """SIGHUP cancels a run as SIGTERM does, unless the run started out ignoring it (#209).

    A terminal that hangs up sends SIGHUP to its foreground process group,
    which no longer holds the workers. Without a handler, the runner would die
    without its cleanup and leave every worker to play out its game. A test
    sends SIGHUP only once it has checked the signal is handled or ignored,
    so a run that doesn't handle it fails the test instead of killing pytest.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("before", ["default", "handler"])
    async def test_sighup_cancels_the_run_and_ends_its_workers(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        process_groups: _ProcessGroups,
        before: str,
    ) -> None:
        """Both workers are still playing when the hangup arrives.

        The run starts from SIGHUP's default, as the CLI does, or from a
        handler its caller installed, which it takes over as well. While it
        cleans up, SIGHUP is still handled, so a second hangup, as a shell and
        then the kernel can send, doesn't kill it halfway.
        """
        received: list[int] = []

        def _record(signum: int, frame: object) -> None:
            received.append(signum)

        during_cleanup: list[object] = []

        def _spy(workers: list[multiprocessing.Process]) -> None:
            during_cleanup.append(signal.getsignal(signal.SIGHUP))
            _terminate_all_workers(workers)

        monkeypatch.setattr("sprt_runner.runner._terminate_all_workers", _spy)
        old_sighup = signal.signal(
            signal.SIGHUP, _record if before == "handler" else signal.SIG_DFL
        )
        _ScriptedFakeProcess.reset([], default="play_forever")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        run = asyncio.create_task(run_sprt(_quick_config(concurrency=2)))
        try:
            deadline = time.monotonic() + 5
            while len(_ScriptedFakeProcess.instances) < 2:
                assert time.monotonic() < deadline, "the run didn't start two workers within 5 s"
                await asyncio.sleep(0.01)
            assert signal.getsignal(signal.SIGHUP) is not signal.SIG_DFL, (
                "the run doesn't handle SIGHUP"
            )

            os.kill(os.getpid(), signal.SIGHUP)
            done, _ = await asyncio.wait({run}, timeout=5)

            assert done, "the run was still going 5 s after SIGHUP"
            with pytest.raises(asyncio.CancelledError):
                await run
        finally:
            if not run.done():
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run
            signal.signal(signal.SIGHUP, old_sighup)

        assert received == []
        assert during_cleanup
        assert all(handler is not signal.SIG_DFL for handler in during_cleanup)
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        assert messages[-1]["type"] == "interrupted"
        killed = {pgid for pgid, sig in process_groups.killed if sig == signal.SIGKILL}
        assert {p.pid for p in _ScriptedFakeProcess.instances} <= killed

    @pytest.mark.asyncio
    async def test_a_run_started_ignoring_sighup_plays_on_through_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """As under nohup: SIGHUP stays ignored, so a hangup changes nothing.

        SIGTERM is still handled. The hangup is sent only while it's still
        ignored and the run is still going, so a run that took SIGHUP over
        fails here instead of killing pytest with it.
        """
        old_sighup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        sigterm_before = signal.getsignal(signal.SIGTERM)
        _ScriptedFakeProcess.reset([], default="play_forever")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        run = asyncio.create_task(run_sprt(_quick_config(concurrency=2)))
        try:
            deadline = time.monotonic() + 5
            while len(_ScriptedFakeProcess.instances) < 2:
                assert time.monotonic() < deadline, "the run didn't start two workers within 5 s"
                await asyncio.sleep(0.01)
            assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
            assert signal.getsignal(signal.SIGTERM) is not sigterm_before, (
                "the run doesn't handle SIGTERM"
            )

            os.kill(os.getpid(), signal.SIGHUP)
            await asyncio.sleep(0.2)

            assert not run.done()
        finally:
            if not run.done():
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run
            after = signal.getsignal(signal.SIGHUP)
            signal.signal(signal.SIGHUP, old_sighup)
        assert after is signal.SIG_IGN

    @pytest.mark.asyncio
    async def test_a_finished_run_leaves_no_signal_handler_behind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No SIGTERM or SIGHUP handler is left on the caller's loop.

        SIGHUP is back at its default, where asyncio leaves a signal it
        handled, rather than ignored.
        """
        old_sighup = signal.signal(signal.SIGHUP, signal.SIG_DFL)
        try:
            _ScriptedFakeProcess.reset([])
            _patch_engines(monkeypatch, _ScriptedFakeProcess)

            await _run_within(_quick_config(concurrency=1), 10.0, monkeypatch)

            loop = asyncio.get_running_loop()
            assert loop.remove_signal_handler(signal.SIGTERM) is False
            assert loop.remove_signal_handler(signal.SIGHUP) is False
            assert signal.getsignal(signal.SIGHUP) is signal.SIG_DFL
        finally:
            signal.signal(signal.SIGHUP, old_sighup)


class TestFailedGames:
    """A game with an error result isn't scored, but is counted in ``errors`` (#197).

    The scripted workers' failed games carry the error ``engine failed to
    start``. ``concurrency`` failed games in a row, with no scored game
    between them, are fatal. Worker deaths keep their own count.
    """

    @pytest.mark.asyncio
    async def test_failed_games_are_counted_in_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """At three workers, two games in every three fail, so never three in a row.

        Each failure is reported and counted, both of each pair included, and
        the run goes on to a decision.
        """
        _ScriptedFakeProcess.reset(["error", "error", "deliver"] * 20)
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 5.0, monkeypatch)

        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        failed = [e for e in errors if e.endswith(" failed: engine failed to start")]
        assert len(failed) == len(errors) >= 2
        progress = [m for m in messages if m["type"] == "progress"]
        assert progress[0]["errors"] == 2
        assert progress[-1]["errors"] == len(failed)
        assert len([m for m in messages if m["type"] == "complete"]) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            "test engine eng2 (Black) failed to start: Engine did not respond within 30.0s",
            "invalid literal for int() with base 10: 'x'",
            "",
        ],
        ids=["failed_start", "failed_during_the_game", "no_message"],
    )
    async def test_a_full_round_of_failed_games_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        error: str,
    ) -> None:
        """Three failed games in a row stop a three-worker run once they're reported.

        Every failed game counts, whatever its error says, even when it's empty.
        """
        _ScriptedFakeProcess.reset([], default="error", error=error)
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=3), 3.0, monkeypatch, capsys)

        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        game_ids = [worker.task.game_id for worker in _ScriptedFakeProcess.instances[:3]]
        assert errors == [
            *(f"Game {game_id} failed: {error}" for game_id in game_ids),
            f"Aborting after 3 failed games in a row; the last: {error}",
        ]
        assert [m for m in messages if m["type"] in ("game_result", "progress")] == []
        assert not any(worker.is_alive() for worker in _ScriptedFakeProcess.instances)

    @pytest.mark.asyncio
    async def test_the_fatal_line_repeats_the_last_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Each failed game says something different here, and the fatal line quotes the last."""
        _ScriptedFakeProcess.reset([], default="error", error="failure {index}")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=3), 3.0, monkeypatch, capsys)

        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert [e.rsplit(": ", 1)[1] for e in errors[:3]] == ["failure 0", "failure 1", "failure 2"]
        assert errors[3] == "Aborting after 3 failed games in a row; the last: failure 2"

    @pytest.mark.asyncio
    async def test_failed_games_after_scored_ones_are_fatal_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Two games are scored, then two fail in a row at two workers, which stops the run."""
        _ScriptedFakeProcess.reset(["deliver", "deliver", "error", "error"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=2), 3.0, monkeypatch, capsys)

        assert len([m for m in messages if m["type"] == "game_result"]) == 2
        assert messages[-1]["message"] == (
            "Aborting after 2 failed games in a row; the last: engine failed to start"
        )

    @pytest.mark.asyncio
    async def test_failed_games_found_while_checking_for_dead_workers_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A real worker whose engine can't start delivers its failed game and exits.

        So the coordinator often reads the failed game while it checks for
        dead workers, rather than from its wait on the queue. Such a game is
        reported and counted as a failed game, not as a worker death. Reading
        results there doesn't clear the count, either: here a scored game is
        read that way before the second failed game is handled, and the two
        failures still stop the run. The spy checks that the second failed
        game came through that check.
        """
        drains: list[list[str]] = []

        def _spy(result_queue: multiprocessing.Queue[WorkerResult]) -> list[WorkerResult]:
            drained = _drain_results(result_queue)
            drains.append([r.game_id for r in drained])
            return drained

        monkeypatch.setattr("sprt_runner.runner._drain_results", _spy)
        _ScriptedFakeProcess.reset(["error", "error_and_exit", "deliver_and_exit"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=2), 3.0, monkeypatch, capsys)

        second = _ScriptedFakeProcess.instances[1].task.game_id
        assert [second] in drains
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert not any("died unexpectedly" in e for e in errors)
        assert errors[-1] == (
            "Aborting after 2 failed games in a row; the last: engine failed to start"
        )

    @pytest.mark.asyncio
    async def test_a_failed_game_found_while_checking_for_dead_workers_clears_the_deaths(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A failed game read while looking for dead workers resets the death count.

        At three workers, two die on the first poll. On the second, the first
        worker delivers a failed game and exits just as the coordinator
        checks, while two more die. Its worker ran, so since its result there
        have been two deaths, not four, and the run goes on to a decision.
        """
        drains: list[list[str]] = []

        def _spy(result_queue: multiprocessing.Queue[WorkerResult]) -> list[WorkerResult]:
            drained = _drain_results(result_queue)
            drains.append([r.game_id for r in drained])
            return drained

        monkeypatch.setattr("sprt_runner.runner._drain_results", _spy)
        _ScriptedFakeProcess.reset(["error_on_second_check", "die", "die", "die", "die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=3), 5.0, monkeypatch)

        first = _ScriptedFakeProcess.instances[0].task.game_id
        assert drains[:2] == [[], [first]]
        messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert not any(e.startswith("Aborting") for e in errors)
        assert len([e for e in errors if "died unexpectedly" in e]) == 4
        assert f"Game {first} failed: engine failed to start" in errors
        assert len([m for m in messages if m["type"] == "complete"]) == 1

    @pytest.mark.asyncio
    async def test_one_failed_game_is_fatal_at_one_worker(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _ScriptedFakeProcess.reset(["error"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=1), 3.0, monkeypatch, capsys)

        assert messages[-1]["message"] == (
            "Aborting after 1 failed game in a row; the last: engine failed to start"
        )

    @pytest.mark.asyncio
    async def test_a_worker_death_neither_clears_nor_adds_to_the_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """One slot plays on while the other has a failed game, a death, then a failed game.

        At two workers those are two failed games in a row: the death between
        them is neither a scored game nor a failed one.
        """
        _ScriptedFakeProcess.reset(["play_forever", "error", "die", "error"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=2), 3.0, monkeypatch, capsys)

        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert len([e for e in errors if "died unexpectedly" in e]) == 1
        assert errors[-1] == (
            "Aborting after 2 failed games in a row; the last: engine failed to start"
        )

    @pytest.mark.asyncio
    async def test_tasks_name_their_engines_by_role_and_spec(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whichever colour the test engine has, a task's labels say which engine is which.

        The labels keep each spec's commit, which tells two commits of one engine apart.
        """
        _FakeProcess.captured_tasks = []
        _patch_engines(monkeypatch, _FakeProcess)
        config = _quick_config(concurrency=1)
        config.base = "lab:abc1234"
        config.test = "lab:def5678"

        await run_sprt(config)

        tasks = _FakeProcess.captured_tasks
        assert {task.swap_colors for task in tasks} == {False, True}
        base, test = "base engine lab:abc1234", "test engine lab:def5678"
        for task in tasks:
            expected = (test, base) if task.swap_colors else (base, test)
            assert (task.white_label, task.black_label) == expected


# ---------------------------------------------------------------------------
# Queue exception handling tests
# ---------------------------------------------------------------------------


class _QueueErrorFakeProcess:
    """Fake process whose queue raises a non-Empty exception.

    The queue injected into the constructor is replaced with one that raises
    the configured exception on ``get()``.
    """

    next_pid: ClassVar[int] = _FAKE_PID_BASE + 11000
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
    @pytest.mark.parametrize("queue_error", [OSError, RuntimeError])
    async def test_error_while_draining_the_queue_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        queue_error: type[Exception],
    ) -> None:
        """A queue error while reading results for a death check aborts cleanly.

        Any exception counts, not only an I/O error. The worker still playing
        is terminated.
        """
        _ScriptedFakeProcess.reset(["play_forever", "die_and_break_queue"], queue_error=queue_error)
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=2), 5.0, monkeypatch, capsys)

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
        _DeadFakeProcess.next_pid = _FAKE_PID_BASE + 9000

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

        messages = await _run_to_fatal_stop(config, 5.0, monkeypatch, capsys)
        error_msgs = [m for m in messages if m["type"] == "error"]

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
        _QueueErrorFakeProcess.next_pid = _FAKE_PID_BASE + 11000
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

        messages = await _run_to_fatal_stop(config, 5.0, monkeypatch, capsys)
        error_msgs = [m for m in messages if m["type"] == "error"]

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
        _QueueErrorFakeProcess.next_pid = _FAKE_PID_BASE + 11000
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

        messages = await _run_to_fatal_stop(config, 5.0, monkeypatch, capsys)
        error_msgs = [m for m in messages if m["type"] == "error"]

        assert any("Fatal error reading result queue" in m["message"] for m in error_msgs)

    @pytest.mark.asyncio
    async def test_any_other_exception_is_fatal_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An exception from ``get`` that isn't an I/O error also aborts the run."""
        monkeypatch.setattr(_QueueErrorFakeProcess, "exception_to_raise", RuntimeError)
        _patch_engines(monkeypatch, _QueueErrorFakeProcess)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=1), 5.0, monkeypatch, capsys)

        assert [m["message"] for m in messages if m["type"] == "error"] == [
            "Fatal error reading result queue; aborting"
        ]


class TestSetupFailuresAreFatal:
    """A run that can't resolve its engines or load its book is a fatal stop (#188)."""

    @pytest.mark.asyncio
    async def test_engine_build_failure_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A failed build is reported as the run's only line."""

        async def _failing_resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            raise WorktreeError("Engine build failed in 'engines/x':\nboom")

        _patch_engines(monkeypatch, _FakeProcess)
        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _failing_resolve)

        messages = await _run_to_fatal_stop(_quick_config(concurrency=1), 5.0, monkeypatch, capsys)

        assert messages == [
            {
                "type": "error",
                "message": "Failed to resolve engines: Engine build failed in 'engines/x':\nboom",
            }
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("book_name", "content", "reason"),
        [
            ("missing.epd", None, "EPD file not found: {path}"),
            ("book.txt", None, "Unsupported opening book format: '.txt' (expected .epd or .pgn)"),
            ("empty.epd", "", "no positions in empty.epd"),
            ("unparseable.epd", "not a position\n", "no positions in unparseable.epd"),
            ("empty.pgn", "", "no positions in empty.pgn"),
            ("not_pgn.pgn", "not a position\n", "no positions in not_pgn.pgn"),
        ],
        ids=["missing", "unsupported", "empty", "no_line_parses", "empty_pgn", "not_pgn"],
    )
    async def test_book_load_failure_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        book_name: str,
        content: str | None,
        reason: str,
    ) -> None:
        """A book that can't be loaded is reported as the run's only line.

        The real loader runs, so all of its errors are covered: a missing
        file (``FileNotFoundError``), an unsupported suffix, and a book with
        no positions (``ValueError``). The run used to fall back to the
        initial position for that last one (#12). The workers would deliver
        games, so a run that carried on without its book would print results
        too.
        """
        _patch_engines(monkeypatch, _FakeProcess)
        book_path = tmp_path / book_name
        if content is not None:
            book_path.write_text(content)
        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=200.0,
            book_path=book_path,
        )

        messages = await _run_to_fatal_stop(config, 5.0, monkeypatch, capsys)

        message = f"Failed to load opening book: {reason.format(path=book_path)}"
        assert messages == [{"type": "error", "message": message}]

    @pytest.mark.asyncio
    async def test_any_book_error_is_fatal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Any exception from the loader stops the run, not only the two it raises itself.

        A book that exists but can't be read raises ``PermissionError``, for one.
        """

        def _failing_load(path: Path) -> list[str]:
            raise RuntimeError("unreadable")

        _patch_engines(monkeypatch, _FakeProcess)
        monkeypatch.setattr("sprt_runner.runner.load_openings", _failing_load)
        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=200.0,
            book_path=Path("book.epd"),
        )

        messages = await _run_to_fatal_stop(config, 5.0, monkeypatch, capsys)

        assert messages == [{"type": "error", "message": "Failed to load opening book: unreadable"}]


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


async def _run_to_fatal_stop(
    config: RunConfig,
    limit_s: float,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> list[dict[str, Any]]:
    """Run ``run_sprt``, which must stop on a fatal error, and return its messages.

    The run must raise ``RunAbortedError`` within ``limit_s``, and its last
    line must be the ``error`` carrying the exception's message (#188).
    """
    with pytest.raises(RunAbortedError) as exc_info:
        await _run_within(config, limit_s, monkeypatch)
    messages = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert messages[-1] == {"type": "error", "message": str(exc_info.value)}
    return messages


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
    next_pid: ClassVar[int] = _FAKE_PID_BASE + 15000

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
    next_pid: ClassVar[int] = _FAKE_PID_BASE + 16000

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
    next_pid: ClassVar[int] = _FAKE_PID_BASE + 17000

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
    - ``error``: puts a result saying the game failed, with ``reset``'s
      ``error`` as its message, then stays alive. ``{index}`` in that message
      becomes the worker's creation index.
    - ``error_and_exit``: puts that failed game, sleeps 50 ms, then exits, as a
      real worker whose engine couldn't start does.
    - ``die``: exits at once without a result.
    - ``play_forever``: stays alive without a result until terminated.
    - ``deliver_on_second_check``: on the second ``is_alive()`` call, puts a
      result, sleeps 50 ms and exits, so its result arrives just as the
      coordinator checks for dead workers.
    - ``error_on_second_check``: the same, with a failed game.
    - ``die_and_break_queue``: exits without a result and makes every
      non-blocking ``get`` on the queue raise ``queue_error`` (``OSError``
      unless ``reset`` says otherwise). Blocking reads still work.

    "Exits" means ``is_alive()`` turns False. ``join`` records a join once
    the worker has exited.

    A delivered game is a White win by checkmate, unless ``reset`` gives
    ``outcomes``: then each worker's game has the result and termination at
    its creation index, cycling through the list.
    """

    script: ClassVar[list[str]] = []
    default: ClassVar[str] = "deliver"
    queue_error: ClassVar[type[Exception]] = OSError
    error: ClassVar[str] = "engine failed to start"
    outcomes: ClassVar[list[tuple[GameResult, str | None]]] = []
    instances: ClassVar[list[_ScriptedFakeProcess]] = []
    next_pid: ClassVar[int] = _FAKE_PID_BASE

    def __init__(
        self,
        *,
        target: object = None,
        args: tuple[WorkerTask, multiprocessing.Queue[WorkerResult]] = ...,  # type: ignore[assignment]
    ) -> None:
        self.task, self._queue = args
        index = len(_ScriptedFakeProcess.instances)
        self.index = index
        script = _ScriptedFakeProcess.script
        self.behaviour = script[index] if index < len(script) else _ScriptedFakeProcess.default
        self._alive = True
        self._checks = 0
        self.joined = False
        self._pid = _ScriptedFakeProcess.next_pid
        _ScriptedFakeProcess.next_pid += 1
        _ScriptedFakeProcess.instances.append(self)

    @classmethod
    def reset(
        cls,
        script: list[str],
        default: str = "deliver",
        queue_error: type[Exception] = OSError,
        error: str = "engine failed to start",
        outcomes: list[tuple[GameResult, str | None]] | None = None,
    ) -> None:
        cls.script = script
        cls.default = default
        cls.queue_error = queue_error
        cls.error = error
        cls.outcomes = outcomes if outcomes is not None else []
        cls.instances = []

    @property
    def pid(self) -> int:
        return self._pid

    def _result(self) -> WorkerResult:
        """This worker's result: a failed game for the ``error`` behaviours, else a finished one."""
        if self.behaviour.startswith("error"):
            return WorkerResult(
                game_id=self.task.game_id,
                result=None,
                termination=None,
                move_count=0,
                swap_colors=self.task.swap_colors,
                error=_ScriptedFakeProcess.error.replace("{index}", str(self.index)),
            )
        outcomes = _ScriptedFakeProcess.outcomes
        if not outcomes:
            return _white_win(self.task)
        result, termination = outcomes[self.index % len(outcomes)]
        return WorkerResult(
            game_id=self.task.game_id,
            result=result,
            termination=termination,
            move_count=20,
            swap_colors=self.task.swap_colors,
        )

    def start(self) -> None:
        if self.behaviour in ("deliver", "deliver_and_exit", "error", "error_and_exit"):
            self._queue.put(self._result())
        if self.behaviour in ("deliver_and_exit", "error_and_exit"):
            time.sleep(0.05)
            self._alive = False
        elif self.behaviour == "die":
            self._alive = False
        elif self.behaviour == "die_and_break_queue":
            blocking_get = self._queue.get

            def _broken(block: bool = True, timeout: float | None = None) -> WorkerResult:
                if not block:
                    raise _ScriptedFakeProcess.queue_error("result queue broken")
                return blocking_get(block, timeout)

            setattr(self._queue, "get", _broken)  # noqa: B010
            self._alive = False

    def is_alive(self) -> bool:
        on_second_check = ("deliver_on_second_check", "error_on_second_check")
        if self.behaviour in on_second_check and self._alive:
            self._checks += 1
            if self._checks == 2:
                self._queue.put(self._result())
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


_NO_TERMINATIONS = {reason.value: 0 for reason in TerminationReason}


class TestGameLimits:
    """``max_games`` ends a run at N scored games, and ``early_stop=False`` plays all N (#189).

    N counts scored games. A failed game or a dead worker frees its slot, and
    another game takes its place. A decision before N still ends the run
    unless ``early_stop`` is off. Otherwise ``complete`` says ``inconclusive``,
    or ``fixed`` without early stopping, and whatever the result it carries
    the run's tallies.

    ``_quick_config``'s alternating losses and wins accept H0 at game 17.
    """

    @staticmethod
    def _config(concurrency: int, max_games: int, *, early_stop: bool = True) -> RunConfig:
        config = _quick_config(concurrency)
        config.max_games = max_games
        config.early_stop = early_stop
        return config

    @staticmethod
    def _messages(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]

    @staticmethod
    def _of_type(messages: list[dict[str, Any]], message_type: str) -> list[dict[str, Any]]:
        return [m for m in messages if m["type"] == message_type]

    @pytest.mark.asyncio
    async def test_a_run_with_no_decision_by_the_limit_is_inconclusive(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Five games come before the decision at game 17, so the run stops at five."""
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(concurrency=1, max_games=5), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert len(self._of_type(messages, "game_result")) == 5
        assert self._of_type(messages, "complete") == [messages[-1]]
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("inconclusive", 5)
        assert len(_ScriptedFakeProcess.instances) == 5

    @pytest.mark.asyncio
    async def test_no_game_launches_once_n_are_scored_or_in_flight(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """At three workers and a limit of four, the fourth game is the last to launch.

        Every worker is terminated when the run ends.
        """
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(concurrency=3, max_games=4), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert len(_ScriptedFakeProcess.instances) == 4
        assert len(self._of_type(messages, "game_result")) == 4
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("inconclusive", 4)
        assert not any(worker.is_alive() for worker in _ScriptedFakeProcess.instances)

    @pytest.mark.asyncio
    async def test_a_game_still_playing_at_the_limit_is_waited_for(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The second of two games ends after the first, and its result still counts.

        No third game launches while it plays, since the limit is two.
        """
        _ScriptedFakeProcess.reset(["deliver", "deliver_on_second_check"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        await _run_within(self._config(concurrency=2, max_games=2), 5.0, monkeypatch)

        messages = self._messages(capsys)
        launched = [worker.task.game_id for worker in _ScriptedFakeProcess.instances]
        assert [m["game_id"] for m in self._of_type(messages, "game_result")] == launched
        assert len(launched) == 2
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("inconclusive", 2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("early_stop", "result"),
        [(True, "inconclusive"), (False, "fixed")],
        ids=["limit", "fixed_length"],
    )
    async def test_failed_games_are_replaced_and_dont_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        early_stop: bool,
        result: str,
    ) -> None:
        """At two workers and a limit of three, the first game fails, so four games launch.

        The three scored games are a win, a loss and a win for the test
        engine, which plays White in the second and fourth. The failed game
        is in ``errors`` alone, not in the results or the terminations.
        """
        _ScriptedFakeProcess.reset(["error"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        config = self._config(concurrency=2, max_games=3, early_stop=early_stop)

        await _run_within(config, 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert len(_ScriptedFakeProcess.instances) == 4
        assert len(self._of_type(messages, "game_result")) == 3
        assert messages[-1] == {
            "type": "complete",
            "result": result,
            "total_games": 3,
            "llr": self._of_type(messages, "progress")[-1]["llr"],
            "wins": 2,
            "losses": 1,
            "draws": 0,
            "errors": 1,
            "terminations": {**_NO_TERMINATIONS, "checkmate": 3},
            "elo": pytest.approx(120.41199826559247, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_lower": pytest.approx(-325.31767471105275, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_upper": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("early_stop", "result"),
        [(True, "inconclusive"), (False, "fixed")],
        ids=["limit", "fixed_length"],
    )
    async def test_dead_workers_are_replaced_and_dont_count(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        early_stop: bool,
        result: str,
    ) -> None:
        """At two workers and a limit of two, the first worker dies, so three games launch.

        The two scored games are a win and a loss for the test engine. The
        death is in ``errors`` alone, not in the results or the terminations.
        """
        _ScriptedFakeProcess.reset(["die"])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        config = self._config(concurrency=2, max_games=2, early_stop=early_stop)

        await _run_within(config, 5.0, monkeypatch)

        messages = self._messages(capsys)
        errors = [str(m["message"]) for m in self._of_type(messages, "error")]
        assert len([e for e in errors if "died unexpectedly" in e]) == len(errors) == 1
        assert len(_ScriptedFakeProcess.instances) == 3
        assert len(self._of_type(messages, "game_result")) == 2
        assert messages[-1] == {
            "type": "complete",
            "result": result,
            "total_games": 2,
            "llr": self._of_type(messages, "progress")[-1]["llr"],
            "wins": 1,
            "losses": 1,
            "draws": 0,
            "errors": 1,
            "terminations": {**_NO_TERMINATIONS, "checkmate": 2},
            "elo": 0.0,
            "elo_lower": None,
            "elo_upper": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("early_stop", [True, False], ids=["limit", "fixed_length"])
    async def test_a_limited_run_still_stops_when_every_game_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        early_stop: bool,
    ) -> None:
        """Failed games are replaced, so #197's limit ends a run whose engine can't start."""
        _ScriptedFakeProcess.reset([], default="error")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        config = self._config(concurrency=3, max_games=5, early_stop=early_stop)

        messages = await _run_to_fatal_stop(config, 3.0, monkeypatch, capsys)

        assert messages[-1]["message"] == (
            "Aborting after 3 failed games in a row; the last: engine failed to start"
        )
        assert [m for m in messages if m["type"] in ("game_result", "complete")] == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("early_stop", [True, False], ids=["limit", "fixed_length"])
    async def test_a_limited_run_still_stops_when_every_worker_dies(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        early_stop: bool,
    ) -> None:
        """Dead workers are replaced too, so #205's limit is what ends a run whose workers die."""
        _ScriptedFakeProcess.reset([], default="die")
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)
        config = self._config(concurrency=3, max_games=5, early_stop=early_stop)

        messages = await _run_to_fatal_stop(config, 3.0, monkeypatch, capsys)

        assert messages[-1]["message"] == "Aborting after 3 worker deaths in a row with no result"
        assert [m for m in messages if m["type"] in ("game_result", "complete")] == []

    @pytest.mark.asyncio
    async def test_a_limit_below_the_concurrency_launches_only_that_many(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """At three workers and a limit of one, one game launches, rather than three cut short."""
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(concurrency=3, max_games=1), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert len(_ScriptedFakeProcess.instances) == 1
        assert len(self._of_type(messages, "game_result")) == 1
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("inconclusive", 1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("behaviour", "fatal"),
        [
            ("error", "Aborting after 3 failed games in a row; the last: engine failed to start"),
            ("die", "Aborting after 3 worker deaths in a row with no result"),
        ],
        ids=["failed_games", "dead_workers"],
    )
    async def test_a_limit_below_the_concurrency_still_stops_after_a_full_round(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        behaviour: str,
        fatal: str,
    ) -> None:
        """At three workers and a limit of one, games launch one at a time.

        Every one fails or its worker dies, and the third in a row stops the run.
        """
        _ScriptedFakeProcess.reset([], default=behaviour)
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)
        config = self._config(concurrency=3, max_games=1)

        messages = await _run_to_fatal_stop(config, 3.0, monkeypatch, capsys)

        assert messages[-1]["message"] == fatal
        assert len(_ScriptedFakeProcess.instances) == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize("first", ["error", "die"], ids=["failed_game", "dead_worker"])
    async def test_a_limit_below_the_concurrency_doesnt_shrink_the_round(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        first: str,
    ) -> None:
        """At three workers, it takes three failures in a row to stop a run, even at a limit of one.

        Here the first game fails or its worker dies, and the replacement is
        scored: a win for the test engine, which plays White.
        """
        _ScriptedFakeProcess.reset([first])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        monkeypatch.setattr(_QUEUE_TIMEOUT_SECONDS, 0.05)

        await _run_within(self._config(concurrency=3, max_games=1), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert len(_ScriptedFakeProcess.instances) == 2
        assert messages[-1] == {
            "type": "complete",
            "result": "inconclusive",
            "total_games": 1,
            "llr": self._of_type(messages, "progress")[-1]["llr"],
            "wins": 1,
            "losses": 0,
            "draws": 0,
            "errors": 1,
            "terminations": {**_NO_TERMINATIONS, "checkmate": 1},
            "elo": None,
            "elo_lower": None,
            "elo_upper": None,
        }

    @pytest.mark.asyncio
    async def test_a_game_with_no_termination_is_counted_as_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A scored result always has a termination, but one without would still be counted.

        Its ``game_result`` says ``unknown``, and so do the terminations, which
        then still add up to the games scored.
        """
        _ScriptedFakeProcess.reset([], outcomes=[(GameResult.DRAW, None)])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(concurrency=1, max_games=1), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert [m["termination"] for m in self._of_type(messages, "game_result")] == ["unknown"]
        assert messages[-1]["terminations"] == {**_NO_TERMINATIONS, "unknown": 1}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("max_games", "early_stop", "expected"),
        [
            (0, True, "max_games must be at least 1, got 0"),
            (-3, True, "max_games must be at least 1, got -3"),
            (0, False, "max_games must be at least 1, got 0"),
            (None, False, "early_stop=False requires max_games"),
        ],
        ids=[
            "zero_games",
            "negative_games",
            "zero_games_without_early_stop",
            "no_early_stop_without_a_limit",
        ],
    )
    async def test_a_limit_that_cannot_end_the_run_is_refused(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        max_games: int | None,
        early_stop: bool,
        expected: str,
    ) -> None:
        """Either would make the run go on forever, so it doesn't start.

        main() refuses these with an ``error`` line. A caller of ``run_sprt``
        gets a ``ValueError`` before anything launches or prints.
        """
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        config = _quick_config(concurrency=2)
        config.max_games = max_games
        config.early_stop = early_stop

        with pytest.raises(ValueError, match=re.escape(expected)):
            await _run_within(config, 3.0, monkeypatch)

        assert _ScriptedFakeProcess.instances == []
        assert capsys.readouterr().out == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("early_stop", "seed", "refusal"),
        [
            (False, None, "early_stop=False requires max_games"),
            (True, -1, "seed must not be negative, got -1"),
        ],
        ids=["no_limit", "negative_seed"],
    )
    async def test_a_refused_run_leaves_nothing_behind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        early_stop: bool,
        seed: int | None,
        refusal: str,
    ) -> None:
        """The refusal comes before anything else ``run_sprt`` does.

        So no SIGTERM or SIGHUP handler is left on the caller's loop, no
        engine is resolved or built, and no worktree is removed for the commit
        specs.
        """
        resolved: list[object] = []
        removed: list[Path] = []

        async def _resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            resolved.append(spec)
            return "engine_cmd", Path("/fake/dir")

        async def _cleanup(worktree_path: Path, *, repo_root: Path) -> None:
            removed.append(worktree_path)

        _ScriptedFakeProcess.reset([])
        monkeypatch.setattr("sprt_runner.runner.multiprocessing.Process", _ScriptedFakeProcess)
        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _resolve)
        monkeypatch.setattr("sprt_runner.runner.cleanup_worktree", _cleanup)
        config = _quick_config(concurrency=1)
        config.base, config.test = "lab:abc1234", "lab:def5678"
        config.early_stop = early_stop
        config.seed = seed

        with pytest.raises(ValueError, match=re.escape(refusal)):
            await asyncio.wait_for(run_sprt(config), timeout=3.0)

        loop = asyncio.get_running_loop()
        assert loop.remove_signal_handler(signal.SIGTERM) is False
        assert loop.remove_signal_handler(signal.SIGHUP) is False
        assert (resolved, removed, _ScriptedFakeProcess.instances) == ([], [], [])

    @pytest.mark.asyncio
    async def test_without_early_stop_the_run_plays_on_past_a_decision(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The LLR passes the H0 bound at game 17, and the run plays on to its 25th.

        Every worker is terminated when the run ends, as at any other end.
        """
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(
            self._config(concurrency=2, max_games=25, early_stop=False), 5.0, monkeypatch
        )

        messages = self._messages(capsys)
        progress = self._of_type(messages, "progress")
        assert len(progress) == 25
        assert progress[16]["llr"] <= progress[16]["lower_bound"]
        assert self._of_type(messages, "complete") == [messages[-1]]
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("fixed", 25)
        assert len(_ScriptedFakeProcess.instances) == 25
        assert not any(worker.is_alive() for worker in _ScriptedFakeProcess.instances)

    @pytest.mark.asyncio
    async def test_without_early_stop_the_run_plays_on_past_h1_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The test engine wins every game, which accepts H1 at game 8, and all 10 are played.

        It has Black in the first game and White in the next, alternately, so
        the winning colour alternates with it.
        """
        _ScriptedFakeProcess.reset(
            [],
            outcomes=[(GameResult.BLACK_WIN, "checkmate"), (GameResult.WHITE_WIN, "checkmate")],
        )
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(
            self._config(concurrency=2, max_games=10, early_stop=False), 5.0, monkeypatch
        )

        messages = self._messages(capsys)
        progress = self._of_type(messages, "progress")
        assert progress[7]["llr"] >= progress[7]["upper_bound"]
        complete = messages[-1]
        assert (complete["result"], complete["total_games"], complete["wins"]) == ("fixed", 10, 10)

    @pytest.mark.asyncio
    async def test_a_decision_before_the_limit_still_ends_the_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(concurrency=1, max_games=25), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("H0", 17)

    @pytest.mark.asyncio
    async def test_a_decision_on_the_last_game_is_the_result(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A run limited to 17 games accepts H0 on its last one, which beats inconclusive."""
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(concurrency=1, max_games=17), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert (messages[-1]["result"], messages[-1]["total_games"]) == ("H0", 17)

    @pytest.mark.asyncio
    async def test_complete_counts_the_results_and_every_termination(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Six games give the test engine a win, a win, a draw, a loss, a win and a draw.

        At one worker the test engine has Black in the first game and White in
        the next, alternately. Terminations that never happened are counted as 0.
        """
        _ScriptedFakeProcess.reset(
            [],
            outcomes=[
                (GameResult.BLACK_WIN, "checkmate"),
                (GameResult.WHITE_WIN, "timeout"),
                (GameResult.DRAW, "draw_rule"),
                (GameResult.BLACK_WIN, "adjudication"),
                (GameResult.BLACK_WIN, "checkmate"),
                (GameResult.DRAW, "max_moves"),
            ],
        )
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(
            self._config(concurrency=1, max_games=6, early_stop=False), 5.0, monkeypatch
        )

        messages = self._messages(capsys)
        assert messages[-1] == {
            "type": "complete",
            "result": "fixed",
            "total_games": 6,
            "llr": self._of_type(messages, "progress")[-1]["llr"],
            "wins": 3,
            "losses": 1,
            "draws": 2,
            "errors": 0,
            "terminations": {
                **_NO_TERMINATIONS,
                "checkmate": 2,
                "timeout": 1,
                "draw_rule": 1,
                "adjudication": 1,
                "max_moves": 1,
            },
            "elo": pytest.approx(120.41199826559247, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_lower": pytest.approx(-93.59850043701118, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_upper": pytest.approx(575.4941770005972, rel=1e-9),  # type: ignore[reportUnknownMemberType]
        }

    @pytest.mark.asyncio
    async def test_a_decided_run_reports_its_tallies_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A run with no limit still ends at its decision, and its ``complete`` counts its games.

        Its first game fails, which ``errors`` counts. Then, alternating
        colours, the test engine wins six games and draws two, which accepts
        H1. Six wins and two draws score 87.5%, which is 400 * log10(7) Elo,
        and the interval's upper end is past 100%.
        """
        _ScriptedFakeProcess.reset(
            ["error"],
            outcomes=[
                (GameResult.BLACK_WIN, "checkmate"),
                (GameResult.WHITE_WIN, "adjudication"),
                (GameResult.DRAW, "draw_rule"),
                (GameResult.WHITE_WIN, "checkmate"),
            ],
        )
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(_quick_config(concurrency=2), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert messages[-1] == {
            "type": "complete",
            "result": "H1",
            "total_games": 8,
            "llr": self._of_type(messages, "progress")[-1]["llr"],
            "wins": 6,
            "losses": 0,
            "draws": 2,
            "errors": 1,
            "terminations": {
                **_NO_TERMINATIONS,
                "checkmate": 4,
                "adjudication": 2,
                "draw_rule": 2,
            },
            "elo": pytest.approx(338.0392160057027, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_lower": pytest.approx(168.37730232002275, rel=1e-9),  # type: ignore[reportUnknownMemberType]
            "elo_upper": None,
        }


# Five distinct positions, each after one move by White.
_BOOK_FENS = [
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/2P5/8/PP1PPPPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1",
    "rnbqkbnr/pppppppp/8/8/8/6P1/PPPPPP1P/RNBQKBNR b KQkq - 0 1",
]


class TestOpeningOrder:
    """A run plays its book in an order a seed fixes, and its first line records the seed (#12).

    In file order, every run would start on the same openings. A position's
    two games, one with each colour, stay together. Each run plays exactly
    ``max_games`` games, so none ends early on a decision.
    """

    @staticmethod
    def _config(book: Path | None, *, seed: int | None, games: int) -> RunConfig:
        config = _quick_config(1)
        config.book_path = book
        config.seed = seed
        config.max_games = games
        config.early_stop = False
        return config

    @staticmethod
    def _book(tmp_path: Path) -> Path:
        book = tmp_path / "book.epd"
        book.write_text("".join(f"{fen}\n" for fen in _BOOK_FENS))
        return book

    @staticmethod
    def _messages(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]

    @staticmethod
    def _played() -> list[tuple[str | None, bool]]:
        """Each launched game's starting position and whether the test engine was White."""
        return [
            (worker.task.game_config.start_fen, worker.task.swap_colors)
            for worker in _ScriptedFakeProcess.instances
        ]

    @staticmethod
    def _in_pairs(order: list[str]) -> list[tuple[str | None, bool]]:
        """The games that ``order`` gives: each position twice, the base engine White first."""
        return [(fen, swap) for fen in order for swap in (False, True)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", [0, 42])
    async def test_the_first_line_records_the_seed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        seed: int,
    ) -> None:
        """0 is a seed like any other, not a missing one."""
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(self._book(tmp_path), seed=seed, games=2), 5.0, monkeypatch)

        messages = self._messages(capsys)
        assert messages[0] == {"type": "start", "seed": seed}
        assert [m["type"] for m in messages].count("start") == 1
        assert self._played() == self._in_pairs(shuffle_openings(_BOOK_FENS, seed))[:2]

    @pytest.mark.asyncio
    async def test_a_run_without_a_book_records_its_seed_too(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A run that gets past loading its book starts with ``start``, with a book or without."""
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(None, seed=5, games=2), 5.0, monkeypatch)

        assert self._messages(capsys)[0] == {"type": "start", "seed": 5}

    @pytest.mark.asyncio
    async def test_positions_come_in_the_seeds_order_in_colour_reversed_pairs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        order = shuffle_openings(_BOOK_FENS, 42)
        assert order != _BOOK_FENS, "seed 42 must not give the file's order, or this proves nothing"
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)

        await _run_within(self._config(self._book(tmp_path), seed=42, games=10), 5.0, monkeypatch)

        assert self._played() == self._in_pairs(order)

    @pytest.mark.asyncio
    async def test_without_a_seed_the_run_picks_one_and_records_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Each run picks its own seed, and plays the order its recorded seed gives.

        Each seed is drawn from 2**32 values, so the hundreds of runs in #190's
        loop rarely share an order. Five runs with five different seeds also rule
        out a seed that doesn't change.
        """
        real_randbelow = secrets.randbelow
        draws: list[int] = []

        def _recording_randbelow(exclusive_upper_bound: int) -> int:
            draws.append(exclusive_upper_bound)
            return real_randbelow(exclusive_upper_bound)

        monkeypatch.setattr("sprt_runner.runner.secrets.randbelow", _recording_randbelow)
        book = self._book(tmp_path)
        seeds: list[int] = []
        for _ in range(5):
            _ScriptedFakeProcess.reset([])
            _patch_engines(monkeypatch, _ScriptedFakeProcess)

            await _run_within(self._config(book, seed=None, games=4), 5.0, monkeypatch)

            start = self._messages(capsys)[0]
            assert start["type"] == "start"
            seed = start["seed"]
            assert isinstance(seed, int)
            assert 0 <= seed < 2**32
            assert self._played() == self._in_pairs(shuffle_openings(_BOOK_FENS, seed))[:4]
            seeds.append(seed)
        assert len(set(seeds)) == 5
        assert draws == [2**32] * 5

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("games", "passes_begun"),
        [(10, 0), (11, 1), (21, 2)],
        ids=["book_played_once", "one_repeat", "two_repeats"],
    )
    async def test_a_warning_each_time_the_book_starts_again(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        tmp_path: Path,
        games: int,
        passes_begun: int,
    ) -> None:
        """Five positions give ten games, so the eleventh repeats a position, as does the 21st."""
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        config = self._config(self._book(tmp_path), seed=42, games=games)

        with caplog.at_level(logging.WARNING, logger="sprt_runner.runner"):
            await _run_within(config, 5.0, monkeypatch)

        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.name == "sprt_runner.runner" and record.levelno == logging.WARNING
        ]
        assert (
            warnings
            == ["Every position in the opening book has been used; positions now repeat"]
            * passes_begun
        )
        # Each pass through the book plays the run's order again.
        one_pass = self._in_pairs(shuffle_openings(_BOOK_FENS, 42))
        assert self._played() == (one_pass * 3)[:games]

    @pytest.mark.asyncio
    async def test_a_negative_seed_is_refused_before_anything_starts(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """random gives -1 the order of 1, so the start line would record a seed --seed refuses.

        main() refuses one with an ``error`` line. A caller of ``run_sprt`` gets a
        ``ValueError`` before anything launches or prints.
        """
        _ScriptedFakeProcess.reset([])
        _patch_engines(monkeypatch, _ScriptedFakeProcess)
        config = self._config(self._book(tmp_path), seed=-1, games=2)

        with pytest.raises(ValueError, match=re.escape("seed must not be negative, got -1")):
            await _run_within(config, 3.0, monkeypatch)

        assert _ScriptedFakeProcess.instances == []
        assert capsys.readouterr().out == ""


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

    def test_handshake_timeout(self) -> None:
        default = build_parser().parse_args(_RUN_ARGV).handshake_timeout_ms
        assert default == DEFAULT_HANDSHAKE_TIMEOUT_MS == 30_000
        args = build_parser().parse_args([*_RUN_ARGV, "--handshake-timeout-ms", "45000"])
        assert args.handshake_timeout_ms == 45_000


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

    def test_handshake_timeout_reaches_run_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--handshake-timeout-ms", "45000")
        assert config.handshake_timeout_ms == 45_000

    def test_smallest_handshake_timeout_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--handshake-timeout-ms", "1")
        assert config.handshake_timeout_ms == 1


class TestGameLimitFlagsCLI:
    """Parser-level tests for --max-games and --no-early-stop (#189)."""

    def test_defaults(self) -> None:
        args = build_parser().parse_args(_RUN_ARGV)
        assert args.max_games is None
        assert args.no_early_stop is False

    def test_explicit_values(self) -> None:
        args = build_parser().parse_args([*_RUN_ARGV, "--max-games", "1000", "--no-early-stop"])
        assert args.max_games == 1000
        assert args.no_early_stop is True


class TestMainGameLimits:
    """main() hands the game limits to RunConfig, and refuses ones that can't work (#189).

    A run that never stops early needs a limit, or it would never stop.
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

    def test_no_limit_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch)
        assert (config.max_games, config.early_stop) == (None, True)

    def test_a_limit_reaches_run_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--max-games", "1000")
        assert (config.max_games, config.early_stop) == (1000, True)

    def test_no_early_stop_reaches_run_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--max-games", "1", "--no-early-stop")
        assert (config.max_games, config.early_stop) == (1, False)

    def test_a_limit_below_the_concurrency_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The run then launches only as many games as the limit allows."""
        config = self._run_main(monkeypatch, "--max-games", "1", "--concurrency", "4")
        assert (config.max_games, config.concurrency) == (1, 4)

    @pytest.mark.parametrize(
        ("extra_args", "expected"),
        [
            (["--max-games", "0"], "--max-games must be at least 1, got 0"),
            (["--max-games", "-1"], "--max-games must be at least 1, got -1"),
            (["--max-games", "0", "--no-early-stop"], "--max-games must be at least 1, got 0"),
            (["--no-early-stop"], "--no-early-stop requires --max-games"),
        ],
        ids=[
            "zero_games",
            "negative_games",
            "zero_games_without_early_stop",
            "no_early_stop_without_a_limit",
        ],
    )
    def test_a_limit_that_cannot_work_exits_1_with_an_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        extra_args: list[str],
        expected: str,
    ) -> None:
        async def _fail_if_called(config: RunConfig) -> None:
            raise AssertionError("run_sprt must not be reached when validation fails")

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fail_if_called)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV, *extra_args])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert capsys.readouterr().out.splitlines() == [format_error_message(expected)]


class TestMainBookAndSeed:
    """main() plays the default book without ``--book``, and hands ``--seed`` to RunConfig (#12).

    Without a book, every game would start from the initial position, so a
    deterministic engine would replay the same game.
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

    def test_without_book_the_default_book_is_played(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch)
        assert config.book_path == Path(DEFAULT_BOOK) == Path("books/2moves_v1.epd")

    def test_a_book_reaches_run_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config = self._run_main(monkeypatch, "--book", "my/book.pgn")
        assert config.book_path == Path("my/book.pgn")

    def test_an_empty_book_flag_is_not_the_initial_position(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--book ''`` used to mean no book, so every game began at the initial position.

        Now it's a path like any other, and one no run can load.
        """
        config = self._run_main(monkeypatch, "--book", "")
        assert config.book_path == Path("")
        with pytest.raises(ValueError, match="Unsupported opening book format"):
            load_openings(Path(""))

    def test_no_seed_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The run then picks one, and records it on its first line."""
        assert self._run_main(monkeypatch).seed is None

    @pytest.mark.parametrize("seed", [0, 7, 2**40])
    def test_a_seed_reaches_run_config(self, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
        assert self._run_main(monkeypatch, "--seed", str(seed)).seed == seed

    def test_a_negative_seed_exits_1_with_an_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """:mod:`random` gives a seed and its negative the same order, so -1 would replay 1."""

        async def _fail_if_called(config: RunConfig) -> None:
            raise AssertionError("run_sprt must not be reached when validation fails")

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fail_if_called)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV, "--seed", "-1"])

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1
        assert capsys.readouterr().out.splitlines() == [
            format_error_message("--seed must not be negative, got -1")
        ]


class TestMainStartMethod:
    """main() starts workers from a forkserver with the runner preloaded.

    A forkserver child starts with default signal handling, unlike a fork of
    the multi-threaded coordinator. With ``sprt_runner.runner`` preloaded it
    starts about as fast as a fork, where spawn is markedly slower. main()
    only chooses when nothing else has, and only where forkserver exists.
    When it chooses, it starts the forkserver itself, with SIGHUP ignored, so
    that the forkserver and its resource tracker outlive a terminal's hangup
    and the runner can clean up after it (#209).
    """

    @staticmethod
    def _run_main(
        monkeypatch: pytest.MonkeyPatch, events: list[tuple[str, int | None]] | None = None
    ) -> tuple[str | None, list[list[str]]]:
        """Run main() and return the start method run_sprt saw, and every preload set.

        Each preload set is also added to ``events``, if given, as
        ``("preload", None)``.
        """
        seen: dict[str, str | None] = {}
        preloads: list[list[str]] = []

        async def _fake_run_sprt(config: RunConfig) -> None:
            seen["method"] = multiprocessing.get_start_method(allow_none=True)

        def _record_preload(modules: list[str]) -> None:
            preloads.append(list(modules))
            if events is not None:
                events.append(("preload", None))

        monkeypatch.setattr("sprt_runner.runner.run_sprt", _fake_run_sprt)
        # Every public way to set the preload ends in this function.
        monkeypatch.setattr("multiprocessing.forkserver.set_forkserver_preload", _record_preload)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV])

        main()

        return seen["method"], preloads

    @pytest.mark.parametrize(
        "sighup", [signal.SIG_DFL, signal.SIG_IGN], ids=["sighup_default", "sighup_ignored"]
    )
    def test_chooses_a_preloaded_forkserver_started_ignoring_sighup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_groups: _ProcessGroups,
        sighup: signal.Handlers,
    ) -> None:
        """SIGHUP is ignored while the forkserver starts, then as it was before."""
        if "forkserver" not in multiprocessing.get_all_start_methods():
            pytest.skip("forkserver isn't available on this platform")
        multiprocessing.set_start_method(None, force=True)
        signal.signal(signal.SIGHUP, sighup)

        method, preloads = self._run_main(monkeypatch)

        assert method == "forkserver"
        assert preloads == [["sprt_runner.runner"]]
        assert process_groups.forkserver_sighup == [signal.SIG_IGN]
        assert signal.getsignal(signal.SIGHUP) is sighup

    def test_sets_the_preload_before_it_starts_the_forkserver(
        self, monkeypatch: pytest.MonkeyPatch, process_groups: _ProcessGroups
    ) -> None:
        """The forkserver takes its preload as it starts, so a later one would be lost."""
        if "forkserver" not in multiprocessing.get_all_start_methods():
            pytest.skip("forkserver isn't available on this platform")
        multiprocessing.set_start_method(None, force=True)
        events: list[tuple[str, int | None]] = []
        process_groups.log = events

        self._run_main(monkeypatch, events)

        assert events == [("preload", None), ("forkserver", None)]

    @pytest.mark.parametrize(
        "sighup", [signal.SIG_DFL, signal.SIG_IGN], ids=["sighup_default", "sighup_ignored"]
    )
    def test_puts_sighup_back_even_if_the_forkserver_fails_to_start(
        self, monkeypatch: pytest.MonkeyPatch, sighup: signal.Handlers
    ) -> None:
        def _fail() -> None:
            raise OSError("no forkserver")

        monkeypatch.setattr("multiprocessing.forkserver.ensure_running", _fail)
        signal.signal(signal.SIGHUP, sighup)

        with pytest.raises(OSError, match="no forkserver"):
            _start_forkserver_ignoring_sighup()

        assert signal.getsignal(signal.SIGHUP) is sighup

    @pytest.mark.parametrize("chosen", multiprocessing.get_all_start_methods())
    def test_keeps_a_start_method_already_chosen(
        self, monkeypatch: pytest.MonkeyPatch, process_groups: _ProcessGroups, chosen: str
    ) -> None:
        """Whatever the caller chose, fork and forkserver included, stays, with no preload.

        main() doesn't start a forkserver either: the caller's choice decides
        when one starts.
        """
        multiprocessing.set_start_method(chosen, force=True)

        method, preloads = self._run_main(monkeypatch)

        assert method == chosen
        assert preloads == []
        assert process_groups.forkserver_sighup == []

    def test_keeps_the_default_where_forkserver_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, process_groups: _ProcessGroups
    ) -> None:
        multiprocessing.set_start_method(None, force=True)
        monkeypatch.setattr(
            "sprt_runner.runner.multiprocessing.get_all_start_methods", lambda: ["spawn"]
        )

        method, preloads = self._run_main(monkeypatch)

        assert method is None
        assert preloads == []
        assert process_groups.forkserver_sighup == []


class TestMainExitCodes:
    """main() exits 1 after a fatal stop and keeps its other exit codes (#188).

    A caller that checks the exit code must not take a run that stopped on a
    fatal error for a finished test. A fatal stop has already printed its
    ``error`` line, so main() adds nothing to stdout. A decision still exits
    0, Ctrl-C 130 and SIGTERM 1.
    """

    @staticmethod
    def _exit_code(
        monkeypatch: pytest.MonkeyPatch,
        fake_run_sprt: Callable[[RunConfig], Awaitable[None]] | None,
        *extra_args: str,
    ) -> int | str | None:
        """Run main() and return the exit status the process would end with.

        ``fake_run_sprt`` replaces ``run_sprt``; with ``None`` the real one runs.
        """
        if fake_run_sprt is not None:
            monkeypatch.setattr("sprt_runner.runner.run_sprt", fake_run_sprt)
        monkeypatch.setattr("sys.argv", ["sprt_runner", *_RUN_ARGV, *extra_args])
        try:
            main()
        except SystemExit as exc:
            return exc.code
        return 0

    def test_a_fatal_stop_exits_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def _fatal(config: RunConfig) -> None:
            print(format_error_message("boom"), flush=True)
            raise RunAbortedError("boom")

        assert self._exit_code(monkeypatch, _fatal) == 1
        assert capsys.readouterr().out.splitlines() == [format_error_message("boom")]

    def test_a_decision_exits_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _decided(config: RunConfig) -> None:
            pass

        assert self._exit_code(monkeypatch, _decided) == 0

    def test_ctrl_c_exits_130(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _interrupted(config: RunConfig) -> None:
            raise KeyboardInterrupt

        assert self._exit_code(monkeypatch, _interrupted) == 130

    def test_sigterm_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _cancelled(config: RunConfig) -> None:
            raise asyncio.CancelledError

        assert self._exit_code(monkeypatch, _cancelled) == 1

    @pytest.mark.parametrize("failure", ["build", "book"])
    def test_a_setup_failure_exits_1_through_the_real_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        failure: str,
    ) -> None:
        """The CLI exits 1 after a failed build or book load, with the error as its only line.

        The real ``run_sprt`` runs. Engine resolution is faked and the book is
        missing, so it stops before launching anything.
        """

        async def _resolve(spec: object, *, repo_root: Path) -> tuple[str, Path]:
            if failure == "build":
                raise WorktreeError("Engine build failed")
            return "engine_cmd", Path("/fake/dir")

        monkeypatch.setattr("sprt_runner.runner.resolve_engine_path", _resolve)
        book = tmp_path / "missing.epd"

        code = self._exit_code(monkeypatch, None, "--book", str(book))

        assert code == 1
        expected = {
            "build": "Failed to resolve engines: Engine build failed",
            "book": f"Failed to load opening book: EPD file not found: {book}",
        }[failure]
        assert capsys.readouterr().out.splitlines() == [format_error_message(expected)]


class TestTimeLimitValidationCLI:
    """Invalid limits fail before any game runs, on the --tc error channel."""

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--time-margin-ms", "-1"),
            ("--move-timeout-ms", "0"),
            ("--move-timeout-ms", "-1"),
            ("--handshake-timeout-ms", "0"),
            ("--handshake-timeout-ms", "-1"),
        ],
        ids=[
            "negative_margin",
            "zero_timeout",
            "negative_timeout",
            "zero_handshake",
            "negative_handshake",
        ],
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

    @pytest.mark.asyncio
    async def test_handshake_limit_reaches_every_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await self._captured_game_configs(
            monkeypatch,
            RunConfig(
                base="base-engine",
                test="test-engine",
                time_control=FixedTimeControl(movetime_ms=100),
                elo0=0.0,
                elo1=500.0,
                handshake_timeout_ms=45_000,
            ),
        )

        tasks = _FakeProcess.captured_tasks
        assert len(tasks) >= 2
        assert {task.handshake_timeout_ms for task in tasks} == {45_000}

    def test_run_config_defaults_to_the_cli_default(self) -> None:
        config = RunConfig(
            base="base-engine",
            test="test-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=0.0,
            elo1=5.0,
        )
        assert config.handshake_timeout_ms == DEFAULT_HANDSHAKE_TIMEOUT_MS


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
        """Worktree cleanup should be called even when run_sprt() stops on a fatal error."""
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

        with pytest.raises(RunAbortedError):
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
    next_pid: ClassVar[int] = _FAKE_PID_BASE + 11000
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
        _StubbornFakeProcess.next_pid = _FAKE_PID_BASE + 11000
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

            next_pid: ClassVar[int] = _FAKE_PID_BASE + 7000

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
        _PromptFakeProcess.next_pid = _FAKE_PID_BASE + 7000

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
        w.pid = _FAKE_PID_BASE - 3
        w.is_alive.return_value = True  # stays alive after terminate

        _terminate_all_workers([w])

        w.terminate.assert_called_once()  # type: ignore[union-attr]
        w.kill.assert_called_once()  # type: ignore[union-attr]
        # join called twice: once after terminate, once after kill
        assert w.join.call_count == 2  # type: ignore[union-attr]

    def test_empty_list_is_noop(self) -> None:
        """Terminating an empty list does nothing."""
        _terminate_all_workers([])  # Should not raise

    def test_kills_each_workers_group_once_the_worker_has_exited(
        self, process_groups: _ProcessGroups
    ) -> None:
        """A worker's engines are in its group and can outlive it, so the group is killed (#209).

        It's killed after the worker has exited, when the worker can no longer
        start an engine.
        """
        events: list[tuple[str, int | None]] = []
        process_groups.log = events
        workers = [_RecordedWorker(pid, events) for pid in (_PID_A, _PID_B)]

        _terminate_all_workers(cast("list[multiprocessing.Process]", workers))

        assert events == [
            *(("terminate", _PID_A), ("join", _PID_A), ("killpg", _PID_A)),
            *(("terminate", _PID_B), ("join", _PID_B), ("killpg", _PID_B)),
        ]
        assert process_groups.killed == [(_PID_A, signal.SIGKILL), (_PID_B, signal.SIGKILL)]

    def test_kills_a_stubborn_workers_group_once_sigkill_has_ended_it(
        self, process_groups: _ProcessGroups
    ) -> None:
        events: list[tuple[str, int | None]] = []
        process_groups.log = events
        worker = _RecordedWorker(_PID_A, events, ignores_sigterm=True)

        _terminate_all_workers(cast("list[multiprocessing.Process]", [worker]))

        assert events == [
            *(("terminate", _PID_A), ("join", _PID_A)),
            *(("kill", _PID_A), ("join", _PID_A), ("killpg", _PID_A)),
        ]

    def test_kills_the_group_of_a_worker_still_alive_after_sigkill(
        self, process_groups: _ProcessGroups
    ) -> None:
        """Its engines can still be killed, though the worker hasn't exited yet."""
        events: list[tuple[str, int | None]] = []
        process_groups.log = events
        worker = _RecordedWorker(_PID_A, events, ignores_sigterm=True, survives_sigkill=True)

        _terminate_all_workers(cast("list[multiprocessing.Process]", [worker]))

        assert events == [
            *(("terminate", _PID_A), ("join", _PID_A)),
            *(("kill", _PID_A), ("join", _PID_A), ("killpg", _PID_A)),
        ]

    @pytest.mark.parametrize(
        "error",
        [ProcessLookupError(), PermissionError()],
        ids=["empty_group", "only_a_zombie_left"],
    )
    def test_a_group_with_no_engine_left_doesnt_stop_the_cleanup(
        self, process_groups: _ProcessGroups, error: OSError
    ) -> None:
        """Killing an empty group raises ``ProcessLookupError``.

        On macOS, a group whose only process is an unreaped zombie raises
        ``PermissionError`` instead, as the worker can be for a moment.
        """
        events: list[tuple[str, int | None]] = []
        process_groups.log = events
        process_groups.errors = [error]
        workers = [_RecordedWorker(pid, events) for pid in (_PID_A, _PID_B)]

        _terminate_all_workers(cast("list[multiprocessing.Process]", workers))

        assert events == [
            *(("terminate", _PID_A), ("join", _PID_A), ("killpg", _PID_A)),
            *(("terminate", _PID_B), ("join", _PID_B), ("killpg", _PID_B)),
        ]

    def test_a_worker_that_never_started_has_no_group_to_kill(
        self, process_groups: _ProcessGroups
    ) -> None:
        events: list[tuple[str, int | None]] = []
        worker = _RecordedWorker(None, events)

        _terminate_all_workers(cast("list[multiprocessing.Process]", [worker]))

        assert process_groups.killed == []


_PID_A = _FAKE_PID_BASE - 2
_PID_B = _FAKE_PID_BASE - 1


class _RecordedWorker:
    """A worker that logs what's done to it into a list it shares with the test.

    It exits on ``terminate()``, unless it ignores SIGTERM, and on ``kill()``,
    unless it survives SIGKILL for a while, as a process in uninterruptible
    sleep does.
    """

    def __init__(
        self,
        pid: int | None,
        events: list[tuple[str, int | None]],
        *,
        ignores_sigterm: bool = False,
        survives_sigkill: bool = False,
    ) -> None:
        self.pid = pid
        self._events = events
        self._ignores_sigterm = ignores_sigterm
        self._survives_sigkill = survives_sigkill
        self._alive = True

    def terminate(self) -> None:
        self._events.append(("terminate", self.pid))
        if not self._ignores_sigterm:
            self._alive = False

    def kill(self) -> None:
        self._events.append(("kill", self.pid))
        if not self._survives_sigkill:
            self._alive = False

    def join(self, timeout: float | None = None) -> None:
        self._events.append(("join", self.pid))

    def is_alive(self) -> bool:
        return self._alive


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
    next_pid: ClassVar[int] = _FAKE_PID_BASE + 12000

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
        _InterruptibleFakeProcess.next_pid = _FAKE_PID_BASE + 12000

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

        # The third worker should have been terminated
        assert _FAKE_PID_BASE + 12002 in _InterruptibleFakeProcess.terminate_called

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
