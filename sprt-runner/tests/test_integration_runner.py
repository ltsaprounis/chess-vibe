"""Integration tests for the SPRT runner with real engine subprocesses.

Exercises the complete test orchestration loop:
``runner.py`` → ``game.py`` → ``UCIClient`` → engine subprocess.

Uses ``random-engine`` vs itself with very wide SPRT bounds so tests
terminate quickly (2-4 games). Marked with ``@pytest.mark.integration``
so unit-test runs can skip them.

The ``random-engine`` tests skip automatically if its venv is not built. The
real-time clock tests use the scripted engine in ``scripted_uci_engine.py``,
which runs on this venv's interpreter and needs no build.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import multiprocessing
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, cast

import chess
import pytest
from shared.storage.models import GameResult
from shared.time_control import (
    DepthTimeControl,
    FixedTimeControl,
    IncrementTimeControl,
    TimeControl,
)
from shared.uci_client import UCIClient
from sprt_runner.adjudication import AdjudicationConfig
from sprt_runner.game import GameConfig, TerminationReason, play_game
from sprt_runner.openings import load_openings, shuffle_openings
from sprt_runner.runner import (
    DEFAULT_BOOK,
    RunConfig,
    WorkerTask,
    _play_single_game,  # type: ignore[reportPrivateUsage]
    run_sprt,
)

pytestmark = pytest.mark.integration

# Repo root — two levels up from this test file.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _add_initial_position_book(root: Path) -> None:
    """Give a temporary repo root the book the CLI plays without ``--book``: the initial position.

    Runs from these roots then play every game from the initial position, as
    they did before the CLI had a default book (#12). Some of their tests
    rely on every game being the same, which the real book, shuffled for
    each run, would vary from run to run.
    """
    book = root / DEFAULT_BOOK
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_text(f"{chess.STARTING_FEN}\n")


def _parse_json_lines(raw: str) -> list[dict[str, object]]:
    """Parse newline-delimited JSON output into a list of dicts."""
    messages: list[dict[str, object]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped:
            messages.append(json.loads(stripped))
    return messages


@pytest.fixture
def sprt_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root with engines.json pointing at the pre-built engine.

    The real ``engines.json`` rebuilds the engine's venv on every run, which
    would replace the repo's own venv under every test that uses it. This
    fixture creates a lightweight directory structure with ``build: null``
    and symlinks the real engine directory so ``resolve_engine_path`` can
    locate the engine without rebuilding.
    """
    # Write engines.json without a build step
    engines_json = tmp_path / "engines.json"
    engines_json.write_text(
        json.dumps(
            [
                {
                    "id": "random-engine",
                    "name": "Random Engine",
                    "dir": "engines/random-engine",
                    "build": None,
                    "run": ".venv/bin/python -m random_engine",
                }
            ]
        )
    )

    # Symlink the engine directory so the run command resolves correctly
    engines_dir = tmp_path / "engines"
    engines_dir.mkdir()
    (engines_dir / "random-engine").symlink_to(
        _REPO_ROOT / "engines" / "random-engine",
        target_is_directory=True,
    )

    return tmp_path


class TestSPRTRunnerIntegration:
    """End-to-end SPRT runner tests with real engine subprocesses."""

    @pytest.mark.asyncio
    async def test_abbreviated_sprt_completes(
        self,
        random_engine_command: str,  # triggers skip if venv absent
        sprt_repo_root: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Run a short SPRT test and verify it reaches a statistical decision.

        Uses very wide bounds (elo0=-200, elo1=200) so the test converges
        in only 2-4 games. Verifies:
        - Real engine subprocesses are spawned and UCI handshake completes
        - Games are played to completion with legal moves
        - SPRT statistics accumulate correctly (wins + draws + losses = total)
        - Test terminates with H0 or H1 decision
        """
        config = RunConfig(
            base="random-engine",
            test="random-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=-200.0,
            elo1=200.0,
            concurrency=1,
            adjudication=AdjudicationConfig(
                # Disable adjudication — let games end naturally
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
            repo_root=sprt_repo_root,
        )

        await run_sprt(config)

        captured = capsys.readouterr()
        messages = _parse_json_lines(captured.out)

        # --- Must have at least one game_result and one complete message ---
        game_results = [m for m in messages if m["type"] == "game_result"]
        progress_msgs = [m for m in messages if m["type"] == "progress"]
        complete_msgs = [m for m in messages if m["type"] == "complete"]
        error_msgs = [m for m in messages if m["type"] == "error"]

        assert len(error_msgs) == 0, f"Unexpected errors: {error_msgs}"
        assert len(game_results) >= 1, "Expected at least one game result"
        assert len(complete_msgs) == 1, "Expected exactly one complete message"

        # --- Verify game results have correct structure ---
        for gr in game_results:
            assert gr["result"] in ("1-0", "0-1", "1/2-1/2")
            assert isinstance(gr["move_count"], int)
            assert gr["move_count"] > 0
            assert gr["termination"] in (
                "checkmate",
                "stalemate",
                "draw_rule",
                "adjudication",
                "timeout",
                "max_moves",
            )

        # --- Verify SPRT statistics accumulate correctly ---
        final_progress = progress_msgs[-1]
        wins = final_progress["wins"]
        losses = final_progress["losses"]
        draws = final_progress["draws"]
        total = final_progress["games_total"]
        assert isinstance(wins, int)
        assert isinstance(losses, int)
        assert isinstance(draws, int)
        assert isinstance(total, int)
        assert wins + losses + draws == total
        assert total == len(game_results)
        assert total >= 1

        # --- Verify SPRT decision ---
        complete = complete_msgs[0]
        assert complete["result"] in ("H0", "H1")
        assert isinstance(complete["total_games"], int)
        assert complete["total_games"] == total
        assert isinstance(complete["llr"], (int, float))

    @pytest.mark.asyncio
    async def test_sprt_with_opening_book(
        self,
        random_engine_command: str,  # triggers skip if venv absent
        sprt_repo_root: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Run SPRT with a small EPD opening book and verify it works.

        Verifies that opening book loading and position setup work correctly
        with real engine subprocesses.
        """
        # Create a small EPD file with 2 positions
        book_path = tmp_path / "openings.epd"
        book_path.write_text(
            # Sicilian Defense and French Defense
            "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2\n"
            "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2\n"
        )

        config = RunConfig(
            base="random-engine",
            test="random-engine",
            time_control=FixedTimeControl(movetime_ms=100),
            elo0=-200.0,
            elo1=200.0,
            concurrency=1,
            book_path=book_path,
            adjudication=AdjudicationConfig(
                win_consecutive_moves=0,
                draw_consecutive_moves=0,
            ),
            repo_root=sprt_repo_root,
        )

        await run_sprt(config)

        captured = capsys.readouterr()
        messages = _parse_json_lines(captured.out)

        game_results = [m for m in messages if m["type"] == "game_result"]
        complete_msgs = [m for m in messages if m["type"] == "complete"]
        error_msgs = [m for m in messages if m["type"] == "error"]

        assert len(error_msgs) == 0, f"Unexpected errors: {error_msgs}"
        assert len(game_results) >= 1
        assert len(complete_msgs) == 1
        assert complete_msgs[0]["result"] in ("H0", "H1")


_SCRIPTED_ENGINE = Path(__file__).with_name("scripted_uci_engine.py")
_NO_ADJUDICATION = AdjudicationConfig(win_consecutive_moves=0, draw_consecutive_moves=0)


def _scripted_engine_client(*engine_args: str) -> UCIClient:
    """Build a client for the scripted engine with a 50 ms default timeout.

    That is far below the scripted search time, so a ``go`` falling back to the
    default would forfeit the first move.
    """
    command = shlex.join([sys.executable, str(_SCRIPTED_ENGINE), *engine_args])
    return UCIClient(command, default_timeout=0.05)


def _logged_clock(go_line: str, name: str) -> int:
    """Read one clock value from a logged ``go`` line."""
    tokens = go_line.split()
    return int(tokens[tokens.index(name) + 1])


class TestRealEngineClock:
    """The whole path over real pipes, in real time."""

    @pytest.mark.asyncio
    async def test_real_engine_sees_falling_clocks_and_flags_on_expected_move(
        self, tmp_path: Path
    ) -> None:
        """500 ms per move from 1200 ms: 1200, 700, 200, then 500 > 200 + 100.

        Flagging a move early would need over 300 ms of scheduling jitter, and
        the upper bounds hold because a sleep never returns early.
        """
        white_log = tmp_path / "white_go.log"
        black_log = tmp_path / "black_go.log"
        white = _scripted_engine_client("--sleep-ms", "500", "--go-log", str(white_log))
        black = _scripted_engine_client("--go-log", str(black_log))
        config = GameConfig(
            time_control=IncrementTimeControl(wtime_ms=1200, btime_ms=1200),
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
        )

        try:
            for engine in (white, black):
                await engine.start()
                await engine.uci(timeout=30.0)
                await engine.isready(timeout=30.0)
            outcome = await play_game(white=white, black=black, config=config)
        finally:
            await white.quit()
            await black.quit()

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert len(outcome.moves) == 4

        white_clocks = [_logged_clock(line, "wtime") for line in white_log.read_text().splitlines()]
        assert len(white_clocks) == 3
        assert white_clocks[0] == 1200
        assert 400 < white_clocks[1] <= 700
        assert 0 <= white_clocks[2] <= 200

        black_lines = black_log.read_text().splitlines()
        assert len(black_lines) == 2
        assert 400 < _logged_clock(black_lines[0], "wtime") <= 700
        assert 0 <= _logged_clock(black_lines[1], "wtime") <= 200
        assert _logged_clock(black_lines[0], "btime") == 1200

        white_after_first = outcome.moves[0].clock_white_ms
        assert white_after_first is not None
        assert 400 < white_after_first <= 700

    @pytest.mark.parametrize(
        "tc",
        [
            FixedTimeControl(movetime_ms=100),
            IncrementTimeControl(wtime_ms=100, btime_ms=100),
            DepthTimeControl(depth=1),
        ],
        ids=["movetime", "clock", "depth"],
    )
    @pytest.mark.asyncio
    async def test_hung_engine_flags_at_the_limit(self, tc: TimeControl) -> None:
        """An engine that never replies loses when the client's deadline fires.

        ``go`` never returns here, so the post-go check cannot catch this: only
        the deadline can. The guard turns a dead deadline into a failure rather
        than a hang. Every limit below works out to 200 ms.
        """
        white = _scripted_engine_client("--sleep-ms", "3600000")
        black = _scripted_engine_client()
        config = GameConfig(
            time_control=tc,
            adjudication=_NO_ADJUDICATION,
            time_margin_ms=100,
            move_timeout_ms=200,
        )

        try:
            for engine in (white, black):
                await engine.start()
                await engine.uci(timeout=30.0)
                await engine.isready(timeout=30.0)
            start_s = time.monotonic()
            outcome = await asyncio.wait_for(
                play_game(white=white, black=black, config=config), timeout=5.0
            )
            elapsed_s = time.monotonic() - start_s
        finally:
            await white.quit()
            await black.quit()

        assert outcome.termination == TerminationReason.TIMEOUT
        assert outcome.result == GameResult.BLACK_WIN
        assert outcome.moves == []
        assert 0.2 <= elapsed_s < 2.0


class TestRealTimeMoveLimits:
    """Real slow moves against real clients with the default 10 s ``default_timeout``."""

    @staticmethod
    async def _play_with_slow_first_move(tc: TimeControl, first_sleep_ms: int) -> None:
        """Play two full moves where only White's first search is slow, and check it survived."""
        slow = shlex.join(
            [sys.executable, str(_SCRIPTED_ENGINE), "--first-sleep-ms", str(first_sleep_ms)]
        )
        fast = shlex.join([sys.executable, str(_SCRIPTED_ENGINE)])
        white = UCIClient(slow)
        black = UCIClient(fast)
        config = GameConfig(time_control=tc, adjudication=_NO_ADJUDICATION, max_moves=2)

        try:
            for engine in (white, black):
                await engine.start()
                await engine.uci()
                await engine.isready()
            outcome = await play_game(white=white, black=black, config=config)
        finally:
            await white.quit()
            await black.quit()

        assert outcome.termination == TerminationReason.MAX_MOVES
        assert outcome.result == GameResult.DRAW
        assert len(outcome.moves) == 4

    @pytest.mark.asyncio
    async def test_real_11s_move_survives_movetime_15000(self) -> None:
        await self._play_with_slow_first_move(FixedTimeControl(movetime_ms=15_000), 11_000)

    @pytest.mark.asyncio
    async def test_real_6s_move_survives_depth_1(self) -> None:
        await self._play_with_slow_first_move(DepthTimeControl(depth=1), 6_000)


@pytest.fixture
def scripted_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root registering a slow and a fast scripted engine."""
    engine_dir = tmp_path / "engines" / "scripted"
    engine_dir.mkdir(parents=True)

    def _entry(engine_id: str, sleep_ms: int) -> dict[str, object]:
        return {
            "id": engine_id,
            "name": engine_id,
            "dir": "engines/scripted",
            "build": None,
            "run": f"{sys.executable} {_SCRIPTED_ENGINE} --sleep-ms {sleep_ms}",
        }

    (tmp_path / "engines.json").write_text(
        json.dumps([_entry("slow-engine", 150), _entry("fast-engine", 0)])
    )
    _add_initial_position_book(tmp_path)
    return tmp_path


def _clock_series(moves: list[dict[str, object]], field: str) -> list[int]:
    """Read one clock field from every sidecar move, requiring it to be populated."""
    series: list[int] = []
    for move in moves:
        value = move[field]
        assert isinstance(value, int), f"{field} is not populated: {value!r}"
        series.append(value)
    return series


class TestClockEndToEnd:
    """``run_sprt`` through spawned workers, real engines and ``.eval.json`` sidecars."""

    @pytest.mark.asyncio
    async def test_slow_engine_flags_and_sidecars_record_falling_clocks(
        self,
        scripted_repo_root: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The slow engine loses on time in every game, so three wins decide the test.

        The outer timeout fails the test instead of hanging it if clocks stop
        being enforced: unflagged games are all draws, which never reach a
        decision.
        """
        output_dir = tmp_path / "games"
        config = RunConfig(
            base="slow-engine",
            test="fast-engine",
            time_control=IncrementTimeControl(wtime_ms=1000, btime_ms=1000),
            elo0=-200.0,
            elo1=200.0,
            concurrency=1,
            adjudication=_NO_ADJUDICATION,
            repo_root=scripted_repo_root,
            output_dir=output_dir,
            time_margin_ms=100,
        )

        await asyncio.wait_for(run_sprt(config), timeout=120)

        messages = _parse_json_lines(capsys.readouterr().out)
        assert [m for m in messages if m["type"] == "error"] == []
        game_results = [m for m in messages if m["type"] == "game_result"]
        assert len(game_results) >= 1
        assert {m["termination"] for m in game_results} == {"timeout"}
        assert [m["result"] for m in messages if m["type"] == "complete"] == ["H1"]

        sidecars = sorted(output_dir.glob("*.eval.json"))
        assert len(sidecars) == len(game_results)
        for sidecar in sidecars:
            game = cast(dict[str, object], json.loads(sidecar.read_text()))
            moves = cast(list[dict[str, object]], game["moves"])
            white_clocks = _clock_series(moves, "clock_white_ms")
            black_clocks = _clock_series(moves, "clock_black_ms")
            assert white_clocks == sorted(white_clocks, reverse=True)
            assert black_clocks == sorted(black_clocks, reverse=True)
            assert max(white_clocks) <= 1000
            assert max(black_clocks) <= 1000

            # The slow engine's clock drops by at least its sleep on each of its moves.
            is_slow_white = game["white_engine"] == "slow-engine"
            slow_clocks = white_clocks[::2] if is_slow_white else black_clocks[1::2]
            assert len(slow_clocks) >= 2
            for before, after in itertools.pairwise(slow_clocks):
                assert after == 0 or before - after >= 150


@pytest.fixture
def hung_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root registering an engine whose searches last 60 s.

    Every ``go`` line the engine receives is appended to ``go.log``, so a test
    can tell when a game is underway.
    """
    engine_dir = tmp_path / "engines" / "scripted"
    engine_dir.mkdir(parents=True)
    go_log = tmp_path / "go.log"
    entry = {
        "id": "hung-engine",
        "name": "hung-engine",
        "dir": "engines/scripted",
        "build": None,
        "run": f"{sys.executable} {_SCRIPTED_ENGINE} --sleep-ms 60000 --go-log {go_log}",
    }
    (tmp_path / "engines.json").write_text(json.dumps([entry]))
    _add_initial_position_book(tmp_path)
    return tmp_path


def _session_members(sid: int) -> list[int]:
    """Return the pid of every process in the session ``sid``."""
    members: list[int] = []
    for pid in _process_table():
        with contextlib.suppress(OSError):
            if os.getsid(pid) == sid:
                members.append(pid)
    return members


def _session_is_empty(sid: int) -> bool:
    """Return True once no process is left in the session ``sid``.

    Each test starts the runner in a session of its own. Each worker leads a
    process group of its own, which its engines join (#209), so the runner's
    group can empty while they run on, but they stay in its session.
    """
    return not _session_members(sid)


def _kill_session(sid: int) -> None:
    """SIGKILL every process left in the session ``sid``, which the runner leads."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(sid, signal.SIGKILL)
    for pid in _session_members(sid):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


class TestSignalExit:
    """The runner exits within seconds of SIGTERM or SIGINT, even mid-game (#198)."""

    @pytest.mark.parametrize("start_method", ["spawn", "fork", "forkserver"])
    @pytest.mark.parametrize(
        ("sig", "exit_code"),
        [(signal.SIGTERM, 1), (signal.SIGINT, 130)],
        ids=["SIGTERM", "SIGINT"],
    )
    def test_runner_exits_promptly_mid_game(
        self, hung_repo_root: Path, sig: signal.Signals, exit_code: int, start_method: str
    ) -> None:
        """Two games are searching when the signal arrives.

        The runner must report ``interrupted`` last and exit with its usual
        code within 10 s, leaving no worker or engine processes behind. The
        queue timeout keeps its default. Every start method is covered: main()
        picks forkserver, but a caller can choose fork, and a forked worker
        starts with the coordinator's signal handling.
        """
        if start_method not in multiprocessing.get_all_start_methods():
            pytest.skip(f"{start_method} isn't available on this platform")
        go_log = hung_repo_root / "go.log"
        # Runs the module as ``python -m sprt_runner`` does, the way the
        # backend starts the runner, after choosing the start method.
        boot = (
            f"import multiprocessing; multiprocessing.set_start_method({start_method!r}); "
            "import runpy; runpy.run_module('sprt_runner', run_name='__main__', alter_sys=True)"
        )
        cmd = [
            sys.executable,
            "-c",
            boot,
            "run",
            "--base",
            "hung-engine",
            "--test",
            "hung-engine",
            "--tc",
            "movetime=100000",
            "--concurrency",
            "2",
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=hung_repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 30
            while not (go_log.exists() and go_log.read_text().count("go ") >= 2):
                assert proc.poll() is None, "the runner exited before both games started"
                assert time.monotonic() < deadline, "both games didn't start within 30 s"
                time.sleep(0.05)

            proc.send_signal(sig)
            try:
                out, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pytest.fail(f"the runner was still running 10 s after {sig.name}")

            assert proc.returncode == exit_code
            assert [m["type"] for m in _parse_json_lines(out)][-1] == "interrupted"
            deadline = time.monotonic() + 5
            while not _session_is_empty(proc.pid):
                assert time.monotonic() < deadline, "processes were left in the runner's session"
                time.sleep(0.05)
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)


def _process_table() -> dict[int, tuple[int, str]]:
    """Map every pid to its parent pid and command line, as ``ps`` reports them."""
    out = subprocess.run(
        ["ps", "-ww", "-A", "-o", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    table: dict[int, tuple[int, str]] = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


class TestWorkerStartMethod:
    """Run as the backend runs it, the runner starts workers from a forkserver."""

    def test_workers_are_children_of_the_forkserver(self, hung_repo_root: Path) -> None:
        """Each engine's worker was started by the forkserver, not by the runner itself.

        The runner is started as ``python -m sprt_runner`` with no start method
        chosen. Walking up from each engine process, the forkserver must come
        before the runner. A fork or spawn would make the runner the worker's
        parent. Each worker logs in the runner's format, though it doesn't
        inherit the runner's logging setup. The runner must still exit
        promptly on SIGTERM.
        """
        if "forkserver" not in multiprocessing.get_all_start_methods():
            pytest.skip("forkserver isn't available on this platform")
        go_log = hung_repo_root / "go.log"
        cmd = [
            sys.executable,
            "-m",
            "sprt_runner",
            "run",
            "--base",
            "hung-engine",
            "--test",
            "hung-engine",
            "--tc",
            "movetime=100000",
            "--concurrency",
            "2",
        ]
        stderr_path = hung_repo_root / "stderr.log"
        # Append mode, so the runner's and workers' writes never overwrite each other.
        with stderr_path.open("a") as stderr_file:
            proc = subprocess.Popen(
                cmd,
                cwd=hung_repo_root,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                start_new_session=True,
            )
        try:
            deadline = time.monotonic() + 30
            while not (go_log.exists() and go_log.read_text().count("go ") >= 2):
                assert proc.poll() is None, "the runner exited before both games started"
                assert time.monotonic() < deadline, "both games didn't start within 30 s"
                time.sleep(0.05)

            table = _process_table()
            engines = [pid for pid, (_, command) in table.items() if str(go_log) in command]
            assert len(engines) >= 2
            for engine in engines:
                ancestors: list[str] = []
                pid = table[engine][0]
                while pid in table and pid != proc.pid and pid > 1:
                    ancestors.append(table[pid][1])
                    pid = table[pid][0]
                assert pid == proc.pid, "the engine doesn't descend from the runner"
                assert any("multiprocessing.forkserver" in a for a in ancestors), ancestors

            proc.send_signal(signal.SIGTERM)
            try:
                out, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pytest.fail("the runner was still running 10 s after SIGTERM")
            assert proc.returncode == 1
            assert [m["type"] for m in _parse_json_lines(out)][-1] == "interrupted"
            deadline = time.monotonic() + 5
            while not _session_is_empty(proc.pid):
                assert time.monotonic() < deadline, "processes were left in the runner's session"
                time.sleep(0.05)
            # Workers start the engines, so these lines come from the workers.
            started = re.compile(
                r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} INFO shared\.uci_client: "
                r"Started engine process: .*scripted_uci_engine\.py",
                re.MULTILINE,
            )
            assert len(started.findall(stderr_path.read_text())) >= 2
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)


@pytest.fixture
def registry_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a git repo holding the real ``engines.json`` and random-engine's tracked files.

    Nothing in it is built. Returns the repo root and its only commit.
    """
    tracked = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", "engines.json", "engines/random-engine"],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.split()
    assert "engines.json" in tracked
    for name in tracked:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_REPO_ROOT / name, tmp_path / name)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=test", "-c", "user.email=test@invalid"]
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "commit.gpgsign=false", "commit", "-qm", "x"],
    ):
        subprocess.run([*git, *args], capture_output=True, timeout=30, check=True)
    commit = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=True
    ).stdout.strip()
    _add_initial_position_book(tmp_path)
    return tmp_path, commit


class TestRepeatableBuilds:
    """The real registry entry builds random-engine twice in one directory (#188).

    Base and test are resolved one after the other. With the same engine on
    both sides they share a directory, so the second build runs over the venv
    the first one made, as every run after the first does in a working tree.
    """

    @pytest.mark.parametrize("pinned", [False, True], ids=["working-tree", "same-commit"])
    def test_the_same_engine_on_both_sides_plays_games(
        self, registry_repo: tuple[Path, str], pinned: bool
    ) -> None:
        """Both builds succeed and a game is played, without an ``error`` line."""
        if shutil.which("uv") is None:
            pytest.skip("the registry's build command needs uv on PATH")
        root, commit = registry_repo
        spec = f"random-engine:{commit}" if pinned else "random-engine"
        games_dir = root / "games"
        cmd = [
            *(sys.executable, "-m", "sprt_runner", "run", "--base", spec, "--test", spec),
            *("--tc", "movetime=10", "--output-dir", str(games_dir)),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 120
            while not any(games_dir.glob("*.eval.json")):
                if proc.poll() is not None:
                    out, _ = proc.communicate(timeout=10)
                    pytest.fail(f"the runner exited ({proc.returncode}) before a game:\n{out}")
                assert time.monotonic() < deadline, "no game finished within 120 s"
                time.sleep(0.1)

            proc.send_signal(signal.SIGTERM)
            try:
                out, _ = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                pytest.fail("the runner was still running 30 s after SIGTERM")
            types = [m["type"] for m in _parse_json_lines(out)]
            assert "game_result" in types
            assert "error" not in types
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)


@pytest.fixture
def startup_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root registering engines that start well, never or slowly.

    ``exiting-engine`` exits with code 2 before reading its input, and
    ``slow-starter`` sleeps 4 s before reading it.
    """
    engine_dir = tmp_path / "engines" / "scripted"
    engine_dir.mkdir(parents=True)

    def _entry(engine_id: str, *engine_args: str) -> dict[str, object]:
        return {
            "id": engine_id,
            "name": engine_id,
            "dir": "engines/scripted",
            "build": None,
            "run": " ".join([sys.executable, str(_SCRIPTED_ENGINE), *engine_args]),
        }

    entries = [
        _entry("base-engine"),
        _entry("exiting-engine", "--exit-at-start", "2"),
        _entry("slow-starter", "--startup-delay-ms", "4000"),
    ]
    (tmp_path / "engines.json").write_text(json.dumps(entries))
    _add_initial_position_book(tmp_path)
    return tmp_path


def _run_cli(root: Path, limit_s: float, *args: str) -> tuple[int, list[dict[str, object]]]:
    """Run the runner CLI in ``root`` and return its exit code and messages.

    Fails the test if the runner is still running after ``limit_s``, or if
    it leaves a process behind in its session.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "sprt_runner", "run", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    try:
        try:
            out, _ = proc.communicate(timeout=limit_s)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the runner was still running after {limit_s} s")
        deadline = time.monotonic() + 10
        while not _session_is_empty(proc.pid):
            assert time.monotonic() < deadline, "processes were left in the runner's session"
            time.sleep(0.05)
        return proc.returncode, _parse_json_lines(out)
    finally:
        _kill_session(proc.pid)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)


class TestGamesThatCannotStart:
    """A run whose engine can't start stops with exit 1, naming the engine (#197).

    Every game fails, so at two workers the run stops after its first two
    games: each is reported, and then the fatal ``error`` comes last.
    """

    @staticmethod
    def _assert_stopped_naming(code: int, messages: list[dict[str, object]], engine: str) -> None:
        """Check the run stopped after two failed games, each failure naming ``engine``.

        ``engine`` is a regex for the start of the failure, from the engine's name onwards.
        """
        assert code == 1
        assert [m for m in messages if m["type"] == "game_result"] == []
        errors = [str(m["message"]) for m in messages if m["type"] == "error"]
        assert len(errors) == 3, errors
        for error in errors[:2]:
            assert re.match(rf"Game \S+ failed: {engine}", error), error
        fatal = rf"Aborting after 2 failed games in a row; the last: {engine}"
        assert re.match(fatal, errors[2]), errors[2]

    def test_an_engine_that_exits_at_startup(self, startup_repo_root: Path) -> None:
        code, messages = _run_cli(
            startup_repo_root,
            60,
            *("--base", "base-engine", "--test", "exiting-engine"),
            *("--tc", "movetime=100", "--concurrency", "2"),
        )

        engine = r"test engine exiting-engine \((White|Black)\) failed to start: "
        self._assert_stopped_naming(code, messages, engine)

    def test_an_engine_slower_than_the_limit(self, startup_repo_root: Path) -> None:
        """The slow starter takes 4 s to read ``uci``, against a 1.5 s limit."""
        code, messages = _run_cli(
            startup_repo_root,
            60,
            *("--base", "base-engine", "--test", "slow-starter"),
            *("--tc", "movetime=100", "--concurrency", "2", "--handshake-timeout-ms", "1500"),
        )

        engine = (
            r"test engine slow-starter \((White|Black)\) failed to start: "
            r"Engine did not respond within "
        )
        self._assert_stopped_naming(code, messages, engine)


class TestSlowStartWithinTheDefaultLimit:
    """#197's slowest engine, 11 s to answer ``uci``, now gets its game."""

    @pytest.mark.asyncio
    async def test_an_engine_slower_than_10_s_starts(self) -> None:
        """Before #197 the handshake used the client's 10 s default, so this game failed."""
        slow = shlex.join([sys.executable, str(_SCRIPTED_ENGINE), "--startup-delay-ms", "11000"])
        fast = shlex.join([sys.executable, str(_SCRIPTED_ENGINE)])
        task = WorkerTask(
            game_id="slow-start",
            white_cmd=slow,
            black_cmd=fast,
            game_config=GameConfig(
                time_control=DepthTimeControl(depth=1),
                adjudication=_NO_ADJUDICATION,
                max_moves=1,
            ),
            swap_colors=False,
            white_label="base engine slow",
            black_label="test engine fast",
        )

        result = await asyncio.wait_for(_play_single_game(task), timeout=60)

        assert result.error is None
        assert result.result == GameResult.DRAW
        assert result.termination == TerminationReason.MAX_MOVES.value


class TestGameLimitsThroughTheCLI:
    """``--max-games`` and ``--no-early-stop`` through the real CLI (#189).

    Both sides run the same deterministic scripted engine, so every game is
    the same draw, and at bounds of [0, 200] the fifth draw accepts H0.
    """

    @staticmethod
    def _run(root: Path, *limit_args: str) -> tuple[int, list[dict[str, object]]]:
        """Play fast-engine against itself at two workers, with the given limit flags."""
        return _run_cli(
            root,
            120,
            *("--base", "fast-engine", "--test", "fast-engine", "--tc", "depth=1"),
            *("--elo0", "0", "--elo1", "200", "--concurrency", "2", *limit_args),
        )

    def test_a_run_with_no_decision_by_the_limit_is_inconclusive(
        self, scripted_repo_root: Path
    ) -> None:
        code, messages = self._run(scripted_repo_root, "--max-games", "3")

        assert code == 0
        assert [m for m in messages if m["type"] == "error"] == []
        assert len([m for m in messages if m["type"] == "game_result"]) == 3
        complete = messages[-1]
        assert (complete["type"], complete["result"], complete["total_games"]) == (
            "complete",
            "inconclusive",
            3,
        )

    def test_a_fixed_length_match_plays_on_past_a_decision(self, scripted_repo_root: Path) -> None:
        """Six draws, one past the decision at the fifth, and ``complete`` tallies them."""
        code, messages = self._run(scripted_repo_root, "--max-games", "6", "--no-early-stop")

        assert code == 0
        assert [m for m in messages if m["type"] == "error"] == []
        fifth = [m for m in messages if m["type"] == "progress"][4]
        assert cast(float, fifth["llr"]) <= cast(float, fifth["lower_bound"])
        game_results = [m for m in messages if m["type"] == "game_result"]
        assert len(game_results) == 6
        complete = messages[-1]
        assert (complete["type"], complete["result"], complete["total_games"]) == (
            "complete",
            "fixed",
            6,
        )
        tally = (complete.get("wins"), complete.get("losses"), complete.get("draws"))
        assert (tally, complete.get("errors")) == ((0, 0, 6), 0)
        terminations = cast("dict[str, int]", complete.get("terminations", {}))
        assert sum(terminations.values()) == 6
        for game in game_results:
            assert game["result"] == "1/2-1/2"
        assert (complete.get("elo"), complete.get("elo_lower"), complete.get("elo_upper")) == (
            0.0,
            0.0,
            0.0,
        )


def _register_fast_engine(root: Path) -> None:
    """Register ``fast-engine``, the scripted engine with instant searches, in ``root``."""
    (root / "engines" / "scripted").mkdir(parents=True)
    entry = {
        "id": "fast-engine",
        "name": "fast-engine",
        "dir": "engines/scripted",
        "build": None,
        "run": f"{sys.executable} {_SCRIPTED_ENGINE}",
    }
    (root / "engines.json").write_text(json.dumps([entry]))


@pytest.fixture
def default_book_root(tmp_path: Path) -> Path:
    """Create a temporary repo root holding the real books and a fast scripted engine."""
    _register_fast_engine(tmp_path)
    books = Path(DEFAULT_BOOK).parent
    (tmp_path / books).symlink_to(_REPO_ROOT / books, target_is_directory=True)
    return tmp_path


class TestDefaultBookThroughTheCLI:
    """Without ``--book``, the CLI plays the book at the default path in its seed's order (#12).

    The run's first line records the seed.
    """

    @pytest.mark.parametrize("seed_args", [("--seed", "7"), ()], ids=["seeded", "unseeded"])
    def test_games_start_from_the_book_in_the_recorded_seeds_order(
        self, default_book_root: Path, seed_args: tuple[str, ...]
    ) -> None:
        """One worker plays four games, so they finish in the order they start.

        They come from the run's first two positions, each twice.
        """
        games_dir = default_book_root / "games"
        code, messages = _run_cli(
            default_book_root,
            120,
            *("--base", "fast-engine", "--test", "fast-engine", "--tc", "depth=1"),
            *("--max-games", "4", "--no-early-stop", "--output-dir", str(games_dir), *seed_args),
        )

        assert code == 0
        assert [m for m in messages if m["type"] == "error"] == []
        start = messages[0]
        assert start["type"] == "start"
        seed = cast(int, start["seed"])
        if seed_args:
            assert seed == 7
        order = shuffle_openings(load_openings(_REPO_ROOT / DEFAULT_BOOK), seed)
        game_ids = [m["game_id"] for m in messages if m["type"] == "game_result"]
        start_fens = [
            json.loads((games_dir / f"{game_id}.eval.json").read_text())["start_fen"]
            for game_id in game_ids
        ]
        assert start_fens == [order[0], order[0], order[1], order[1]]

    def test_runs_without_a_seed_each_pick_their_own(self, default_book_root: Path) -> None:
        """Each CLI run picks a fresh seed, so two runs don't play the same order.

        A seed source that only varied within one process would give every run
        the same one.
        """
        seeds: list[object] = []
        for _ in range(2):
            code, messages = _run_cli(
                default_book_root,
                120,
                *("--base", "fast-engine", "--test", "fast-engine", "--tc", "depth=1"),
                *("--max-games", "1"),
            )
            assert code == 0
            assert messages[0]["type"] == "start"
            seeds.append(messages[0]["seed"])
        assert seeds[0] != seeds[1]

    def test_a_run_with_no_book_at_the_default_path_stops(self, tmp_path: Path) -> None:
        """It doesn't fall back to the initial position, and no other book stands in.

        The default path is resolved from the working directory, as ``engines.json``
        is, so a run from a directory without ``books/`` can't load it.
        """
        _register_fast_engine(tmp_path)

        code, messages = _run_cli(
            tmp_path, 60, *("--base", "fast-engine", "--test", "fast-engine", "--tc", "depth=1")
        )

        assert code == 1
        message = f"Failed to load opening book: EPD file not found: {DEFAULT_BOOK}"
        assert messages == [{"type": "error", "message": message}]

    def test_the_start_line_reaches_a_reader_before_any_game_ends(
        self, hung_repo_root: Path
    ) -> None:
        """The seed is on the pipe at once, so a run killed before its first result has it.

        The engine takes 60 s over each search, so no game can end within the 20 s
        this waits. The line is flushed, and starting the first worker flushes
        stdout too, as multiprocessing does before every start, so either would do.
        """
        cmd = [
            *(sys.executable, "-m", "sprt_runner", "run", "--base", "hung-engine"),
            *("--test", "hung-engine", "--tc", "movetime=100000"),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=hung_repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        try:
            assert proc.stdout is not None
            readable, _, _ = select.select([proc.stdout], [], [], 20)
            assert readable, "the runner printed nothing within 20 s"
            first = json.loads(proc.stdout.readline())
            assert first["type"] == "start"
            assert isinstance(first["seed"], int)
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
            if proc.stdout is not None:
                proc.stdout.close()


@pytest.fixture
def hang_first_repo_root(tmp_path: Path) -> Path:
    """Create a temporary repo root whose test engines hang in their first game (#209).

    ``base-engine`` plays every game. Of the ``hangs`` engines, the first to
    start hangs at its first ``go``, reading nothing more, and the rest play
    like the base. Of the ``hangs-then-fails`` engines, the first hangs the
    same way, and the rest exit with code 2. Either way, the rest first wait
    until the first has hung. Each records its hung engine in ``<id>.hung``.
    """
    engine_dir = tmp_path / "engines" / "scripted"
    engine_dir.mkdir(parents=True)

    def _entry(engine_id: str, *engine_args: str) -> dict[str, object]:
        return {
            "id": engine_id,
            "name": engine_id,
            "dir": "engines/scripted",
            "build": None,
            "run": " ".join([sys.executable, str(_SCRIPTED_ENGINE), *engine_args]),
        }

    entries = [
        _entry("base-engine"),
        _entry("hangs", "--hang-first", str(tmp_path / "hangs.hung")),
        _entry(
            "hangs-then-fails",
            *("--hang-first", str(tmp_path / "hangs-then-fails.hung"), "--exit-at-start", "2"),
        ),
    ]
    (tmp_path / "engines.json").write_text(json.dumps(entries))
    _add_initial_position_book(tmp_path)
    return tmp_path


def _hung_engine(record: Path) -> int | None:
    """Return the pid of the engine that ``record`` says has hung, or None before it has."""
    if not record.exists():
        return None
    lines = record.read_text().split()
    return int(lines[0]) if lines[1:] == ["hung"] else None


def _process_exists(pid: int) -> bool:
    """Return True while ``pid`` is running or hasn't been reaped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class TestEnginesThatStopReading:
    """No engine outlives its run, not even one that never reads its input again (#209).

    The first test engine hangs at its first ``go`` while the other worker
    plays on, until the run ends. A worker the coordinator terminates can't
    quit its engines, and the hung engine never notices the end of its input
    and ignores SIGTERM, so only a SIGKILL ends it.
    """

    @staticmethod
    def _start(
        root: Path,
        test: str,
        *args: str,
        start_method: str | None = None,
        stderr_path: Path | None = None,
    ) -> subprocess.Popen[str]:
        """Start the runner at two workers, ``base-engine`` against ``test``.

        It runs as ``python -m sprt_runner`` would, with SIGHUP at its default
        even if pytest ignores it, as under nohup. With a ``start_method``, the
        runner starts its workers that way, as a caller that chooses one would.
        Otherwise it picks its own, the forkserver. Its stderr goes to
        ``stderr_path`` if given.
        """
        boot = "import signal; signal.signal(signal.SIGHUP, signal.SIG_DFL); "
        if start_method is not None:
            boot += f"import multiprocessing; multiprocessing.set_start_method({start_method!r}); "
        boot += "import runpy; runpy.run_module('sprt_runner', run_name='__main__', alter_sys=True)"
        with contextlib.ExitStack() as stack:
            stderr: int | IO[str] = subprocess.DEVNULL
            if stderr_path is not None:
                # Append mode, so the runner's and workers' writes never overwrite each other.
                stderr = stack.enter_context(stderr_path.open("a"))
            return subprocess.Popen(
                [
                    *(sys.executable, "-c", boot, "run"),
                    *("--base", "base-engine", "--test", test, "--tc", "depth=1"),
                    *("--move-timeout-ms", "60000", "--concurrency", "2", *args),
                ],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )

    @staticmethod
    def _finish(proc: subprocess.Popen[str], limit_s: float) -> list[dict[str, object]]:
        """Wait for the runner to exit by itself and return its messages.

        Workers inherit the runner's output, so one left running keeps it open
        after the runner has gone.
        """
        try:
            out, _ = proc.communicate(timeout=limit_s)
        except subprocess.TimeoutExpired:
            if proc.poll() is not None:
                pytest.fail(
                    f"the runner exited ({proc.returncode}), but something it started "
                    f"still held its output {limit_s} s later"
                )
            pytest.fail(f"the runner was still running after {limit_s} s")
        return _parse_json_lines(out)

    @staticmethod
    def _wait_until_hung(proc: subprocess.Popen[str], record: Path) -> int:
        """Wait for an engine to hang and return its pid, failing after 30 s."""
        deadline = time.monotonic() + 30
        while (hung := _hung_engine(record)) is None:
            assert proc.poll() is None, "the runner exited before an engine hung"
            assert time.monotonic() < deadline, "no engine hung within 30 s"
            time.sleep(0.05)
        return hung

    @staticmethod
    def _assert_nothing_left(hung: int, sid: int) -> None:
        """The hung engine and everything else in the runner's session go within 10 s."""
        deadline = time.monotonic() + 10
        while _process_exists(hung):
            assert time.monotonic() < deadline, "the hung engine is still running"
            time.sleep(0.05)
        while not _session_is_empty(sid):
            assert time.monotonic() < deadline, "processes were left in the runner's session"
            time.sleep(0.05)

    @pytest.mark.parametrize(
        "start_method", [None, "fork", "spawn"], ids=["default", "fork", "spawn"]
    )
    def test_sigterm(self, hang_first_repo_root: Path, start_method: str | None) -> None:
        """The runner gets SIGTERM once the engine has hung.

        It runs with the runner's own start method, the forkserver, and with
        each start method a caller can choose instead.
        """
        if start_method is not None and start_method not in multiprocessing.get_all_start_methods():
            pytest.skip(f"{start_method} isn't available on this platform")
        proc = self._start(hang_first_repo_root, "hangs", start_method=start_method)
        try:
            hung = self._wait_until_hung(proc, hang_first_repo_root / "hangs.hung")

            proc.send_signal(signal.SIGTERM)
            messages = self._finish(proc, 10)

            assert proc.returncode == 1
            assert messages[-1]["type"] == "interrupted"
            self._assert_nothing_left(hung, proc.pid)
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)

    def test_a_decision(self, hang_first_repo_root: Path) -> None:
        """The other worker's games are identical draws, and the fifth accepts H0."""
        proc = self._start(hang_first_repo_root, "hangs", "--elo0", "0", "--elo1", "200")
        try:
            messages = self._finish(proc, 60)
            hung = _hung_engine(hang_first_repo_root / "hangs.hung")

            assert hung is not None, "no engine hung before the run ended"
            assert proc.returncode == 0
            assert (messages[-1]["type"], messages[-1].get("result")) == ("complete", "H0")
            self._assert_nothing_left(hung, proc.pid)
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)

    def test_a_fatal_stop(self, hang_first_repo_root: Path) -> None:
        """The other worker's games fail, and two failures in a row stop the run."""
        proc = self._start(hang_first_repo_root, "hangs-then-fails")
        try:
            messages = self._finish(proc, 60)
            hung = _hung_engine(hang_first_repo_root / "hangs-then-fails.hung")

            assert hung is not None, "no engine hung before the run ended"
            assert proc.returncode == 1
            last = messages[-1]
            assert last["type"] == "error"
            assert str(last["message"]).startswith("Aborting after 2 failed games in a row")
            self._assert_nothing_left(hung, proc.pid)
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)

    def test_a_hangup(self, hang_first_repo_root: Path) -> None:
        """SIGHUP reaches the runner's process group once the engine has hung.

        That's what a terminal sends to its foreground group when it hangs
        up. The workers aren't in that group, so the runner has to end them.
        The forkserver and its resource tracker are in it, and must survive
        the hangup: with the forkserver gone, every worker would look dead.
        """
        stderr_path = hang_first_repo_root / "stderr.log"
        proc = self._start(hang_first_repo_root, "hangs", stderr_path=stderr_path)
        try:
            hung = self._wait_until_hung(proc, hang_first_repo_root / "hangs.hung")

            os.killpg(proc.pid, signal.SIGHUP)
            messages = self._finish(proc, 10)

            assert proc.returncode == 1
            assert messages[-1]["type"] == "interrupted"
            assert [m for m in messages if m["type"] == "error"] == []
            self._assert_nothing_left(hung, proc.pid)
            assert "process died unexpectedly" not in stderr_path.read_text()
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)

    def test_a_dead_worker(self, hang_first_repo_root: Path) -> None:
        """The hung engine's worker is SIGKILLed, and the run goes on.

        The coordinator finds the death and kills the dead worker's group, so
        the engine goes while the run is still playing. One death isn't a full
        round at two workers, so SIGTERM ends the run.
        """
        proc = self._start(hang_first_repo_root, "hangs")
        try:
            hung = self._wait_until_hung(proc, hang_first_repo_root / "hangs.hung")
            worker = _process_table()[hung][0]
            assert worker != proc.pid

            os.kill(worker, signal.SIGKILL)
            deadline = time.monotonic() + 10
            while _process_exists(hung):
                assert proc.poll() is None, "the runner exited after one worker died"
                assert time.monotonic() < deadline, "the hung engine is still running"
                time.sleep(0.05)
            assert proc.poll() is None, "the runner exited after one worker died"

            proc.send_signal(signal.SIGTERM)
            messages = self._finish(proc, 10)

            assert proc.returncode == 1
            assert messages[-1]["type"] == "interrupted"
            errors = [str(m["message"]) for m in messages if m["type"] == "error"]
            assert len(errors) == 1
            assert re.fullmatch(
                rf"Worker for game \S+ died unexpectedly \(pid={worker}\)", errors[0]
            )
            self._assert_nothing_left(hung, proc.pid)
        finally:
            _kill_session(proc.pid)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)
